"""Version history: snapshots, diffs, restore, and latexdiff "changes" PDFs.

Snapshots live inside each project in `.history/`:
    .history/objects/<sha1>        file contents (zlib-compressed, de-duplicated)
    .history/snapshots/<id>.json   {"id", "time", "label", "kind", "files": {path: sha1}}
Because files are de-duplicated, a snapshot of an unchanged project costs a few bytes.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import core
from core import (BUILD_DIR_NAME, HISTORY_DIR_NAME, err, iter_project_files, load_settings,
                  project_dir, safe_path, which_tools)

bp = Blueprint("history", __name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
AUTO_MIN_INTERVAL = 120          # seconds between automatic snapshots
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _hist(root: Path) -> Path:
    h = root / HISTORY_DIR_NAME
    (h / "objects").mkdir(parents=True, exist_ok=True)
    (h / "snapshots").mkdir(parents=True, exist_ok=True)
    return h


def _store_blob(root: Path, data: bytes) -> str:
    sha = hashlib.sha1(data).hexdigest()
    obj = _hist(root) / "objects" / sha
    if not obj.exists():
        obj.write_bytes(zlib.compress(data, 6))
    return sha


def _load_blob(root: Path, sha: str) -> bytes:
    return zlib.decompress((_hist(root) / "objects" / sha).read_bytes())


def current_manifest(root: Path) -> dict[str, str]:
    files = {}
    for path, rel in iter_project_files(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            files[rel] = hashlib.sha1(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return files


def list_snapshots(root: Path) -> list[dict]:
    snaps = []
    for f in (_hist(root) / "snapshots").glob("*.json"):
        try:
            snaps.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    snaps.sort(key=lambda s: s["time"], reverse=True)
    return snaps


def get_snapshot(root: Path, sid: str) -> dict | None:
    if not sid.replace("-", "").isalnum():
        return None
    f = _hist(root) / "snapshots" / f"{sid}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def _write_snapshot(root: Path, snap: dict) -> None:
    (_hist(root) / "snapshots" / f"{snap['id']}.json").write_text(json.dumps(snap), encoding="utf-8")


def take_snapshot(root: Path, kind: str = "manual", label: str = "", force: bool = False) -> dict | None:
    """Create a snapshot. Returns None when nothing changed (unless forced/labelled)."""
    with _lock:
        snaps = list_snapshots(root)
        latest = snaps[0] if snaps else None
        manifest = {}
        for path, rel in iter_project_files(root):
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                manifest[rel] = _store_blob(root, path.read_bytes())
            except OSError:
                continue
        unchanged = latest is not None and latest["files"] == manifest
        if unchanged and not force and not label:
            return None
        if kind == "auto" and latest and time.time() - latest["time"] < AUTO_MIN_INTERVAL and not label:
            return None
        now = time.time()
        snap = {"id": f"{int(now * 1000)}", "time": now, "label": label.strip()[:200],
                "kind": kind, "files": manifest}
        _write_snapshot(root, snap)
        _prune(root)
        return snap


def _prune(root: Path) -> None:
    """Thin out old automatic snapshots; labelled/manual ones are always kept.
    Keeps every auto snapshot from the last 24h, one per hour for 30 days, then one per day."""
    now = time.time()
    seen_buckets = set()
    for s in list_snapshots(root):  # newest first
        if s.get("label") or s["kind"] not in ("auto", "compile"):
            continue
        age = now - s["time"]
        if age < 86400:
            continue
        bucket = ("h", int(s["time"] // 3600)) if age < 30 * 86400 else ("d", int(s["time"] // 86400))
        if bucket in seen_buckets:
            (_hist(root) / "snapshots" / f"{s['id']}.json").unlink(missing_ok=True)
        else:
            seen_buckets.add(bucket)
    # Garbage-collect unreferenced objects occasionally.
    if int(now) % 20 == 0:
        used = {sha for s in list_snapshots(root) for sha in s["files"].values()}
        for obj in (_hist(root) / "objects").iterdir():
            if obj.name not in used:
                obj.unlink(missing_ok=True)


def _summary(snap: dict, prev: dict | None) -> dict:
    prev_files = prev["files"] if prev else {}
    files = snap["files"]
    added = [p for p in files if p not in prev_files]
    removed = [p for p in prev_files if p not in files]
    modified = [p for p in files if p in prev_files and files[p] != prev_files[p]]
    return {"added": added, "removed": removed, "modified": modified}


def _compile_hook(root: Path, result: dict) -> None:
    take_snapshot(root, kind="auto")


core.COMPILE_HOOKS.append(_compile_hook)


# --------------------------------------------------------------------------- #
# Diff helpers
# --------------------------------------------------------------------------- #
def _content_of(root: Path, files: dict, rel: str, current: bool) -> bytes | None:
    if current:
        p = root / rel
        return p.read_bytes() if p.is_file() else None
    sha = files.get(rel)
    return _load_blob(root, sha) if sha else None


def _is_text(data: bytes | None) -> bool:
    return data is not None and b"\x00" not in data[:4096]


def _resolve_side(root: Path, sid: str) -> tuple[dict, bool]:
    if sid == "current":
        return current_manifest(root), True
    if sid == "empty":
        return {}, False
    snap = get_snapshot(root, sid)
    if not snap:
        raise KeyError(sid)
    return snap["files"], False


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@bp.get("/api/projects/<name>/history")
def api_list(name):
    root = project_dir(name)
    snaps = list_snapshots(root)
    out = []
    for i, s in enumerate(snaps):
        prev = snaps[i + 1] if i + 1 < len(snaps) else None
        summ = _summary(s, prev)
        out.append({"id": s["id"], "time": s["time"], "label": s.get("label", ""), "kind": s["kind"],
                    "files": len(s["files"]),
                    "changes": {k: len(v) for k, v in summ.items()},
                    "changed_files": (summ["added"] + summ["modified"] + summ["removed"])[:12]})
    cur = current_manifest(root)
    dirty = not snaps or snaps[0]["files"] != cur
    return jsonify({"snapshots": out, "unsaved_changes": dirty})


@bp.post("/api/projects/<name>/history")
def api_create(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("kind", "manual")
    if kind not in ("manual", "auto"):
        kind = "manual"
    snap = take_snapshot(root, kind=kind, label=data.get("label", ""), force=kind == "manual")
    if not snap:
        return jsonify({"created": False})
    return jsonify({"created": True, "id": snap["id"]})


@bp.patch("/api/projects/<name>/history/<sid>")
def api_label(name, sid):
    root = project_dir(name)
    snap = get_snapshot(root, sid)
    if not snap:
        return err("Snapshot not found.", 404)
    snap["label"] = ((request.get_json(force=True, silent=True) or {}).get("label") or "").strip()[:200]
    _write_snapshot(root, snap)
    return jsonify({"ok": True})


@bp.delete("/api/projects/<name>/history/<sid>")
def api_delete(name, sid):
    root = project_dir(name)
    if not get_snapshot(root, sid):
        return err("Snapshot not found.", 404)
    (_hist(root) / "snapshots" / f"{sid}.json").unlink()
    return jsonify({"deleted": True})


@bp.get("/api/projects/<name>/history/diff")
def api_diff(name):
    """Compare `from` and `to` (snapshot ids or "current"). Optional `path` for a single file."""
    root = project_dir(name)
    try:
        a_files, a_cur = _resolve_side(root, request.args.get("from", ""))
        b_files, b_cur = _resolve_side(root, request.args.get("to", "current"))
    except KeyError as e:
        return err(f"Snapshot {e} not found.", 404)
    only = request.args.get("path")
    paths = sorted(set(a_files) | set(b_files))
    files = []
    for p in paths:
        if only and p != only:
            continue
        sa, sb = a_files.get(p), b_files.get(p)
        status = "added" if sa is None else "removed" if sb is None else "modified" if sa != sb else "same"
        entry = {"path": p, "status": status}
        if status != "same" and (only or len(files) < 500):
            da, db = _content_of(root, a_files, p, a_cur), _content_of(root, b_files, p, b_cur)
            if (da is None or _is_text(da)) and (db is None or _is_text(db)):
                la = (da or b"").decode("utf-8", "replace").splitlines()
                lb = (db or b"").decode("utf-8", "replace").splitlines()
                diff = list(difflib.unified_diff(la, lb, "before", "after", n=3, lineterm=""))[2:]
                entry["diff"] = diff[:5000]
                entry["stats"] = {"plus": sum(1 for l in diff if l.startswith("+")),
                                  "minus": sum(1 for l in diff if l.startswith("-"))}
            else:
                entry["binary"] = True
        files.append(entry)
    return jsonify({"files": files})


@bp.get("/api/projects/<name>/history/<sid>/file")
def api_file(name, sid):
    root = project_dir(name)
    snap = get_snapshot(root, sid)
    rel = request.args.get("path", "")
    if not snap or rel not in snap["files"]:
        return err("Not found in snapshot.", 404)
    data = _load_blob(root, snap["files"][rel])
    return jsonify({"path": rel, "content": data.decode("utf-8", "replace") if _is_text(data) else None})


@bp.post("/api/projects/<name>/history/<sid>/restore")
def api_restore(name, sid):
    """Restore one file (body: {"path": ...}) or the whole project (no path)."""
    root = project_dir(name)
    snap = get_snapshot(root, sid)
    if not snap:
        return err("Snapshot not found.", 404)
    rel = (request.get_json(force=True, silent=True) or {}).get("path")
    if rel and rel not in snap["files"]:
        return err("That file isn't in this snapshot.", 404)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap["time"]))
    # Always keep the current state first, so a restore can itself be undone.
    take_snapshot(root, kind="before-restore", label=f"Before restoring {rel or 'project'} from {when}",
                  force=True)
    if rel:
        if rel not in snap["files"]:
            return err("That file isn't in this snapshot.", 404)
        dest = safe_path(root, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_load_blob(root, snap["files"][rel]))
        return jsonify({"restored": [rel]})
    # Whole project: write snapshot files, remove files that didn't exist then.
    for path, r in list(iter_project_files(root)):
        if r not in snap["files"]:
            path.unlink(missing_ok=True)
    for r, sha in snap["files"].items():
        dest = safe_path(root, r)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_load_blob(root, sha))
    # Tidy now-empty folders.
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if HISTORY_DIR_NAME in d.relative_to(root).parts or BUILD_DIR_NAME in d.relative_to(root).parts:
            continue
        try:
            d.rmdir()
        except OSError:
            pass
    return jsonify({"restored": sorted(snap["files"])})


# --------------------------------------------------------------------------- #
# latexdiff
# --------------------------------------------------------------------------- #
def _materialize(root: Path, files: dict, current: bool, dest: Path) -> None:
    for rel in files:
        data = _content_of(root, files, rel, current)
        if data is None:
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)


@bp.post("/api/projects/<name>/latexdiff")
def api_latexdiff(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    tools = which_tools()
    if not shutil.which("latexdiff"):
        return err("latexdiff is not installed. It ships with TeX Live / MiKTeX "
                   "(on Debian/Ubuntu: sudo apt install latexdiff).")
    settings = load_settings(root)
    main = data.get("main") or settings.get("main")
    engine = settings.get("engine", "pdflatex")
    if not main:
        return err("No main file set.")
    try:
        old_files, old_cur = _resolve_side(root, data.get("old", ""))
        new_files, new_cur = _resolve_side(root, data.get("new", "current"))
    except KeyError as e:
        return err(f"Snapshot {e} not found.", 404)
    if main not in old_files:
        return err(f"'{main}' doesn't exist in the older version.")
    style = data.get("style", "UNDERLINE")
    if style not in ("UNDERLINE", "CFONT", "FONTSTRIKE", "CHANGEBAR", "CCHANGEBAR"):
        style = "UNDERLINE"

    log = []
    with tempfile.TemporaryDirectory(prefix="oc-latexdiff-") as tmp:
        tmp = Path(tmp)
        old_dir, new_dir = tmp / "old", tmp / "new"
        _materialize(root, old_files, old_cur, old_dir)
        _materialize(root, new_files, new_cur, new_dir)
        out_tex = new_dir / (Path(main).parent / "zz-changes.tex")
        cmd = ["latexdiff", "--flatten", f"--type={style}", "--math-markup=coarse",
               str(old_dir / main), str(new_dir / main)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=new_dir / Path(main).parent)
        except subprocess.TimeoutExpired:
            return err("latexdiff timed out.")
        log.append("$ latexdiff ...\n" + proc.stderr[-4000:])
        if proc.returncode != 0 or not proc.stdout.strip():
            return jsonify({"ok": False, "log": "\n".join(log)})
        out_tex.write_text(proc.stdout, encoding="utf-8")
        flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}.get(engine, "-pdf")
        if tools["latexmk"]:
            ccmd = ["latexmk", flag, "-f", "-interaction=nonstopmode", "-silent", out_tex.name]
        else:
            ccmd = [engine, "-interaction=nonstopmode", out_tex.name]
        for _ in range(1 if tools["latexmk"] else 2):
            try:
                p2 = subprocess.run(ccmd, capture_output=True, text=True, timeout=core.COMPILE_TIMEOUT,
                                    cwd=out_tex.parent, stdin=subprocess.DEVNULL, errors="replace")
                log.append("$ " + " ".join(ccmd) + "\n" + p2.stdout[-6000:])
            except subprocess.TimeoutExpired:
                log.append("compile timed out")
        pdf = out_tex.with_suffix(".pdf")
        if not pdf.exists():
            texlog = out_tex.with_suffix(".log")
            if texlog.exists():
                log.append(texlog.read_text(errors="replace")[-8000:])
            return jsonify({"ok": False, "log": "\n".join(log)})
        build = root / BUILD_DIR_NAME
        build.mkdir(exist_ok=True)
        shutil.copy(pdf, build / "changes.pdf")
        shutil.copy(out_tex, build / "changes.tex")
    return jsonify({"ok": True, "pdf": f"/api/projects/{name}/latexdiff.pdf", "log": "\n".join(log)})


@bp.get("/api/projects/<name>/latexdiff.pdf")
def api_latexdiff_pdf(name):
    root = project_dir(name)
    pdf = root / BUILD_DIR_NAME / "changes.pdf"
    if not pdf.exists():
        return err("No changes PDF yet.", 404)
    return send_file(pdf, mimetype="application/pdf", max_age=0,
                     as_attachment=request.args.get("download") == "1",
                     download_name=f"{name}-changes.pdf")


@bp.get("/api/projects/<name>/latexdiff.tex")
def api_latexdiff_tex(name):
    root = project_dir(name)
    tex = root / BUILD_DIR_NAME / "changes.tex"
    if not tex.exists():
        return err("No changes file yet.", 404)
    return send_file(tex, mimetype="text/plain", as_attachment=True, download_name=f"{name}-changes.tex")
