"""Backups: copy the project to a folder (e.g. Dropbox/OneDrive/USB) as a dated .zip,
and/or commit and push it to a git remote (e.g. a private GitHub repository).

Settings live in the project's settings file under "backup":
    {"folder": "/path/to/Dropbox/papers", "keep": 30,
     "git_remote": "git@github.com:me/my-paper.git", "git_branch": "main",
     "auto": true, "auto_minutes": 30, "last": {...}}
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request

import core
from core import err, iter_project_files, load_settings, project_dir, save_settings

bp = Blueprint("backup", __name__)
_lock = threading.Lock()

GITIGNORE = ".build/\n.history/\n*.tmp\n.overleaf_copy.json\n"


def _zip_to_folder(root: Path, name: str, folder: str, keep: int) -> str:
    dest_dir = Path(folder).expanduser() / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{name}-{stamp}.zip"
    n = 1
    while dest.exists():                       # two backups in the same second
        dest = dest_dir / f"{name}-{stamp}-{n}.zip"
        n += 1
    tmp = dest.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in iter_project_files(root):
            zf.write(path, rel)
        notes = root / core.NOTES_FILE
        if notes.exists():
            zf.write(notes, core.NOTES_FILE)
    os.replace(tmp, dest)
    zips = sorted(dest_dir.glob(f"{name}-*.zip"))
    for old in zips[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return str(dest)


def _git(root: Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # never hang waiting for a password
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=180, env=env)
    out = (proc.stdout + proc.stderr).strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{out}")
    return out


def _git_backup(root: Path, remote: str, branch: str) -> str:
    if not shutil.which("git"):
        raise RuntimeError("git is not installed.")
    log = []
    if not (root / ".git").exists():
        log.append(_git(root, "-c", "init.defaultBranch=main", "init", "--quiet") or "Initialised git repository.")
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    # Use a local identity if the user hasn't configured one globally.
    ident = []
    if not _git(root, "config", "user.email", check=False):
        ident = ["-c", "user.name=Overleaf Copy", "-c", "user.email=overleaf-copy@localhost"]
    _git(root, "add", "-A")
    status = _git(root, "status", "--porcelain")
    if status:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        log.append(_git(root, *ident, "commit", "-m", f"Backup {stamp}"))
    else:
        log.append("No changes since the last commit.")
    _git(root, "branch", "-M", branch)
    if remote:
        remotes = _git(root, "remote", check=False).split()
        if "origin" in remotes:
            _git(root, "remote", "set-url", "origin", remote)
        else:
            _git(root, "remote", "add", "origin", remote)
        log.append(_git(root, "push", "-u", "origin", branch))
    return "\n".join(l for l in log if l)


def run_backup(root: Path, name: str) -> dict:
    with _lock:
        settings = load_settings(root)
        cfg = settings.get("backup") or {}
        if not cfg.get("folder") and not cfg.get("git_remote") and not cfg.get("git_local"):
            raise ValueError("Set a backup folder or a git remote first (Tools → Backup settings).")
        result = {"time": time.time(), "ok": True, "messages": []}
        if cfg.get("folder"):
            try:
                path = _zip_to_folder(root, name, cfg["folder"], int(cfg.get("keep") or 30))
                result["messages"].append(f"Saved {path}")
            except OSError as e:
                result["ok"] = False
                result["messages"].append(f"Folder backup failed: {e}")
        if cfg.get("git_remote") or cfg.get("git_local"):
            try:
                result["messages"].append(_git_backup(root, cfg.get("git_remote", ""), cfg.get("git_branch") or "main"))
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                result["ok"] = False
                result["messages"].append(str(e))
        settings = load_settings(root)
        settings.setdefault("backup", {})["last"] = result
        save_settings(root, settings)
        return result


def _compile_hook(root: Path, result: dict) -> None:
    cfg = (load_settings(root).get("backup") or {})
    if not cfg.get("auto"):
        return
    last = (cfg.get("last") or {}).get("time", 0)
    if time.time() - last < 60 * int(cfg.get("auto_minutes") or 30):
        return
    threading.Thread(target=lambda: _safe_backup(root), daemon=True).start()


def _safe_backup(root: Path) -> None:
    try:
        run_backup(root, root.name)
    except Exception:  # background: result is recorded in settings when possible
        pass


core.COMPILE_HOOKS.append(_compile_hook)


@bp.get("/api/projects/<name>/backup")
def api_get(name):
    root = project_dir(name)
    cfg = load_settings(root).get("backup") or {}
    return jsonify({**cfg, "git_available": bool(shutil.which("git"))})


@bp.put("/api/projects/<name>/backup")
def api_set(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings(root)
    cfg = settings.get("backup") or {}
    folder = (data.get("folder") or "").strip()
    if folder:
        p = Path(folder).expanduser()
        if not p.is_absolute():
            return err("Use a full folder path, e.g. /Users/me/Dropbox/papers or C:\\Users\\me\\OneDrive\\papers")
        try:
            dest = (p / name).resolve()      # zips are written to <folder>/<project name>/
            projects = core.PROJECTS_DIR.resolve()
            if dest == projects or projects in dest.parents or root.resolve() in p.resolve().parents \
                    or p.resolve() == root.resolve():
                return err("Choose a backup folder outside your projects folder "
                           f"({projects}), otherwise backups would end up inside projects.")
        except OSError:
            pass
    cfg.update({
        "folder": folder,
        "keep": max(1, int(data.get("keep") or 30)),
        "git_remote": (data.get("git_remote") or "").strip(),
        "git_local": bool(data.get("git_local")),
        "git_branch": (data.get("git_branch") or "main").strip() or "main",
        "auto": bool(data.get("auto")),
        "auto_minutes": max(5, int(data.get("auto_minutes") or 30)),
    })
    settings["backup"] = cfg
    save_settings(root, settings)
    return jsonify(cfg)


@bp.post("/api/projects/<name>/backup/run")
def api_run(name):
    root = project_dir(name)
    try:
        return jsonify(run_backup(root, name))
    except ValueError as e:
        return err(str(e))
