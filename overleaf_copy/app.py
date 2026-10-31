#!/usr/bin/env python3
"""
Overleaf Copy - a local, offline LaTeX editor for writing papers.

Runs a small web server on your own machine (127.0.0.1 only by default) that
lets you create projects, edit .tex/.bib files in the browser, upload figures,
and compile to PDF with your locally installed TeX distribution
(TeX Live / MiKTeX / MacTeX).

Usage:
    python app.py                      # http://127.0.0.1:5000
    python app.py --port 8080
    python app.py --projects-dir ~/papers
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
import zipfile
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "project_templates"

BUILD_DIR_NAME = ".build"
SETTINGS_FILE = ".overleaf_copy.json"
HIDDEN_NAMES = {BUILD_DIR_NAME, SETTINGS_FILE, ".git", ".DS_Store", "__MACOSX"}

TEXT_EXTENSIONS = {
    ".tex", ".bib", ".cls", ".sty", ".bst", ".bbx", ".cbx", ".txt", ".md",
    ".csv", ".dat", ".tikz", ".pgf", ".ltx", ".dtx", ".ins", ".cfg", ".def",
    ".clo", ".fd", ".json", ".yaml", ".yml", ".py", ".r", ".m", ".latexmkrc",
}
ENGINES = {"pdflatex", "xelatex", "lualatex"}
COMPILE_TIMEOUT = int(os.environ.get("OVERLEAF_COPY_TIMEOUT", "180"))
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,79}$")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB uploads
PROJECTS_DIR: Path = BASE_DIR / "projects"

_compile_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def err(message: str, status: int = 400):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def project_dir(name: str, must_exist: bool = True) -> Path:
    if not PROJECT_NAME_RE.match(name or "") or name in (".", ".."):
        abort(err("Invalid project name. Use letters, numbers, spaces, '.', '_' or '-'.", 400))
    path = (PROJECTS_DIR / name).resolve()
    if path.parent != PROJECTS_DIR.resolve():
        abort(err("Invalid project name.", 400))
    if must_exist and not path.is_dir():
        abort(err(f"Project '{name}' not found.", 404))
    return path


def safe_path(root: Path, rel: str, allow_root: bool = False) -> Path:
    """Resolve `rel` inside `root`, refusing anything that escapes it."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel and not allow_root:
        abort(err("A file path is required.", 400))
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        abort(err("Path escapes the project directory.", 400))
    parts = Path(rel).parts
    if parts and parts[0] in (BUILD_DIR_NAME, SETTINGS_FILE):
        abort(err("That path is reserved.", 400))
    return target


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"latexmkrc", ".latexmkrc", "Makefile"}:
        return True
    if path.suffix == "":
        try:
            chunk = path.read_bytes()[:2048]
            return b"\x00" not in chunk
        except OSError:
            return False
    return False


def load_settings(root: Path) -> dict:
    f = root / SETTINGS_FILE
    settings = {"main": None, "engine": "pdflatex"}
    if f.exists():
        try:
            settings.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if not settings.get("main") or not (root / settings["main"]).is_file():
        settings["main"] = guess_main_file(root)
    return settings


def save_settings(root: Path, settings: dict) -> None:
    (root / SETTINGS_FILE).write_text(json.dumps(settings, indent=2), encoding="utf-8")


def guess_main_file(root: Path) -> str | None:
    candidates = []
    for p in sorted(root.rglob("*.tex")):
        if any(part in HIDDEN_NAMES for part in p.relative_to(root).parts):
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:20000]
        except OSError:
            continue
        if re.search(r"^\s*\\documentclass", head, re.M):
            rel = p.relative_to(root).as_posix()
            score = 0 if p.stem in ("main", "paper", "manuscript") else 1
            candidates.append((score, len(rel), rel))
    return sorted(candidates)[0][2] if candidates else None


def build_tree(root: Path, current: Path | None = None) -> list[dict]:
    current = current or root
    entries = []
    try:
        children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name in HIDDEN_NAMES:
            continue
        rel = child.relative_to(root).as_posix()
        if child.is_dir():
            entries.append({"name": child.name, "path": rel, "type": "folder",
                            "children": build_tree(root, child)})
        else:
            entries.append({"name": child.name, "path": rel, "type": "file",
                            "text": is_text_file(child), "size": child.stat().st_size})
    return entries


def which_tools() -> dict:
    return {tool: shutil.which(tool) is not None
            for tool in ("latexmk", "pdflatex", "xelatex", "lualatex", "bibtex", "biber",
                         "synctex", "texcount")}


def get_lock(name: str) -> threading.Lock:
    with _locks_guard:
        return _compile_locks.setdefault(name, threading.Lock())


# --------------------------------------------------------------------------- #
# Log parsing
# --------------------------------------------------------------------------- #
FILE_LINE_ERROR_RE = re.compile(r"^(?:\./)?(.+?\.(?:tex|sty|cls|bib|ltx)):(\d+): (.*)$")
LINE_NO_RE = re.compile(r"^l\.(\d+)\s")
WARNING_RE = re.compile(r"^(?:LaTeX|Package (\S+)|Class (\S+)) Warning: (.*)$")
INPUT_LINE_RE = re.compile(r"on input line (\d+)")
BOX_RE = re.compile(r"^((?:Over|Under)full \\[hv]box .*?) (?:in paragraph |detected )?at lines? (\d+)")


def parse_log(log: str, main_file: str | None) -> list[dict]:
    issues: list[dict] = []
    lines = log.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = FILE_LINE_ERROR_RE.match(line)
        if m:
            message = m.group(3)
            # Collect continuation lines until the l.<n> context line.
            context = []
            for j in range(i + 1, min(i + 8, len(lines))):
                if lines[j].startswith("l.") or not lines[j].strip():
                    if lines[j].startswith("l."):
                        context.append(lines[j])
                    break
                context.append(lines[j])
            issues.append({"level": "error", "file": m.group(1), "line": int(m.group(2)),
                           "message": message, "context": "\n".join(context)})
            i += 1
            continue
        if line.startswith("! "):
            line_no = None
            for j in range(i + 1, min(i + 15, len(lines))):
                lm = LINE_NO_RE.match(lines[j])
                if lm:
                    line_no = int(lm.group(1))
                    break
            issues.append({"level": "error", "file": main_file, "line": line_no,
                           "message": line[2:], "context": ""})
            i += 1
            continue
        wm = WARNING_RE.match(line)
        if wm:
            text = wm.group(3)
            k = i + 1
            while k < len(lines) and lines[k].startswith(" ") and len(text) < 400:
                text += " " + lines[k].strip()
                k += 1
            src = wm.group(1) or wm.group(2)
            lm = INPUT_LINE_RE.search(text)
            issues.append({"level": "warning", "file": main_file,
                           "line": int(lm.group(1)) if lm else None,
                           "message": (f"[{src}] " if src else "") + text,
                           "context": ""})
            i = k
            continue
        bm = BOX_RE.match(line)
        if bm:
            issues.append({"level": "typesetting", "file": main_file, "line": int(bm.group(2)),
                           "message": bm.group(1), "context": ""})
        i += 1

    # De-duplicate (latexmk reruns produce repeated messages).
    seen, unique = set(), []
    for issue in issues:
        key = (issue["level"], issue["file"], issue["line"], issue["message"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# --------------------------------------------------------------------------- #
# Compilation
# --------------------------------------------------------------------------- #
def run_compile(root: Path, main: str, engine: str, clean: bool = False) -> dict:
    build = root / BUILD_DIR_NAME
    if clean and build.exists():
        shutil.rmtree(build, ignore_errors=True)
    build.mkdir(exist_ok=True)

    main_path = safe_path(root, main)
    if not main_path.is_file():
        return {"ok": False, "log": f"Main file '{main}' does not exist.", "issues": []}
    main_rel = main_path.relative_to(root).as_posix()
    stem = Path(main_rel).stem
    # Make sure sub-directories exist in the build dir (for \include{chapters/x}).
    for d in root.rglob("*"):
        if d.is_dir() and BUILD_DIR_NAME not in d.relative_to(root).parts and d.name not in HIDDEN_NAMES:
            (build / d.relative_to(root)).mkdir(parents=True, exist_ok=True)

    tools = which_tools()
    env = os.environ.copy()
    # Let TeX find files relative to the project root and the build dir.
    sep = os.pathsep
    env["TEXINPUTS"] = f".{sep}{root}{sep}{env.get('TEXINPUTS', '')}"
    env["BIBINPUTS"] = f".{sep}{root}{sep}{env.get('BIBINPUTS', '')}"
    env["BSTINPUTS"] = f".{sep}{root}{sep}{env.get('BSTINPUTS', '')}"

    # nonstopmode (without -halt-on-error) behaves like Overleaf: a PDF is still
    # produced where possible and errors are reported alongside it.
    common = ["-interaction=nonstopmode", "-file-line-error", "-synctex=1"]
    log_chunks: list[str] = []
    started = time.time()

    def run(cmd: list[str]) -> int:
        log_chunks.append("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True,
                                  errors="replace", timeout=COMPILE_TIMEOUT,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            log_chunks.append(f"!! Timed out after {COMPILE_TIMEOUT}s")
            return -1
        except FileNotFoundError:
            log_chunks.append(f"!! Command not found: {cmd[0]}")
            return -1
        log_chunks.append(proc.stdout[-20000:] + proc.stderr[-5000:])
        return proc.returncode

    if not tools.get(engine):
        return {"ok": False, "issues": [],
                "log": f"'{engine}' was not found on PATH. Install a TeX distribution "
                       f"(TeX Live, MiKTeX or MacTeX) and restart the server."}

    if tools["latexmk"]:
        flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
        rc = run(["latexmk", flag, "-f", "-silent", f"-outdir={BUILD_DIR_NAME}",
                  *common, main_rel])
    else:
        # Manual pipeline: engine -> bibtex/biber -> engine -> engine
        base = [engine, f"-output-directory={BUILD_DIR_NAME}", *common, main_rel]
        rc = run(base)
        aux = build / f"{stem}.aux"
        bcf = build / f"{stem}.bcf"
        if bcf.exists() and tools["biber"]:
            run(["biber", f"--input-directory={BUILD_DIR_NAME}",
                 f"--output-directory={BUILD_DIR_NAME}", stem])
        elif aux.exists() and tools["bibtex"] and "\\bibdata" in aux.read_text(errors="ignore"):
            run(["bibtex", f"{BUILD_DIR_NAME}/{stem}"])
        if aux.exists():
            run(base)
            rc = run(base)

    tex_log_path = build / f"{stem}.log"
    tex_log = tex_log_path.read_text(encoding="utf-8", errors="replace") if tex_log_path.exists() else ""
    pdf = build / f"{stem}.pdf"
    issues = parse_log(tex_log, main_rel)
    has_errors = any(i["level"] == "error" for i in issues)
    ok = pdf.exists() and pdf.stat().st_mtime >= started - 1 and not has_errors

    return {
        "ok": ok,
        "pdf": pdf.exists(),
        "pdf_stale": pdf.exists() and pdf.stat().st_mtime < started - 1,
        "issues": issues,
        "log": tex_log or "\n".join(log_chunks),
        "runner_log": "\n".join(log_chunks)[-30000:],
        "seconds": round(time.time() - started, 2),
        "returncode": rc,
    }


# --------------------------------------------------------------------------- #
# Routes: static UI
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# --------------------------------------------------------------------------- #
# Routes: API
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def api_status():
    return jsonify({"tools": which_tools(), "projects_dir": str(PROJECTS_DIR),
                    "templates": list_templates()})


def list_templates() -> list[dict]:
    out = []
    if TEMPLATES_DIR.is_dir():
        for d in sorted(TEMPLATES_DIR.iterdir()):
            if d.is_dir():
                desc_file = d / "TEMPLATE.txt"
                desc = desc_file.read_text(encoding="utf-8").strip() if desc_file.exists() else ""
                out.append({"id": d.name, "description": desc})
    return out


@app.get("/api/projects")
def api_list_projects():
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir() and not d.name.startswith("."):
            latest = max((f.stat().st_mtime for f in d.rglob("*")
                          if f.is_file() and BUILD_DIR_NAME not in f.parts), default=d.stat().st_mtime)
            projects.append({"name": d.name, "modified": latest})
    projects.sort(key=lambda p: p["modified"], reverse=True)
    return jsonify(projects)


@app.post("/api/projects")
def api_create_project():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    template = data.get("template") or "blank"
    root = project_dir(name, must_exist=False)
    if root.exists():
        return err(f"A project called '{name}' already exists.", 409)
    tpl = TEMPLATES_DIR / secure_filename(template)
    if tpl.is_dir():
        shutil.copytree(tpl, root, ignore=shutil.ignore_patterns("TEMPLATE.txt"))
    else:
        root.mkdir(parents=True)
        (root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello, world!\n\\end{document}\n",
            encoding="utf-8")
    settings = load_settings(root)
    save_settings(root, settings)
    return jsonify({"name": name, "settings": settings})


@app.post("/api/projects/import")
def api_import_project():
    """Import a .zip (e.g. downloaded from Overleaf) as a new project."""
    upload = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    if not upload:
        return err("No zip file uploaded.")
    if not name:
        name = Path(upload.filename or "imported").stem
        name = re.sub(r"[^A-Za-z0-9 _.\-]", "_", name)[:80] or "imported"
    root = project_dir(name, must_exist=False)
    if root.exists():
        return err(f"A project called '{name}' already exists.", 409)
    try:
        zf = zipfile.ZipFile(io.BytesIO(upload.read()))
    except zipfile.BadZipFile:
        return err("That file is not a valid zip archive.")
    root.mkdir(parents=True)
    names = [n for n in zf.namelist() if not n.startswith("__MACOSX/")]
    # If everything is inside a single top-level folder, strip it.
    tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
    strip = len(tops) == 1 and all("/" in n for n in names if n.strip("/"))
    for member in names:
        rel = member.split("/", 1)[1] if strip and "/" in member else member
        if not rel or rel.endswith("/"):
            continue
        dest = (root / rel).resolve()
        if root.resolve() not in dest.parents:
            continue  # zip-slip protection
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    save_settings(root, load_settings(root))
    return jsonify({"name": name})


@app.delete("/api/projects/<name>")
def api_delete_project(name):
    root = project_dir(name)
    shutil.rmtree(root)
    return jsonify({"deleted": name})


@app.post("/api/projects/<name>/rename")
def api_rename_project(name):
    root = project_dir(name)
    new_name = ((request.get_json(force=True, silent=True) or {}).get("name") or "").strip()
    new_root = project_dir(new_name, must_exist=False)
    if new_root.exists():
        return err(f"A project called '{new_name}' already exists.", 409)
    root.rename(new_root)
    return jsonify({"name": new_name})


@app.post("/api/projects/<name>/duplicate")
def api_duplicate_project(name):
    root = project_dir(name)
    new_name = ((request.get_json(force=True, silent=True) or {}).get("name") or "").strip()
    new_root = project_dir(new_name, must_exist=False)
    if new_root.exists():
        return err(f"A project called '{new_name}' already exists.", 409)
    shutil.copytree(root, new_root, ignore=shutil.ignore_patterns(BUILD_DIR_NAME))
    return jsonify({"name": new_name})


@app.get("/api/projects/<name>")
def api_project(name):
    root = project_dir(name)
    return jsonify({"name": name, "tree": build_tree(root), "settings": load_settings(root)})


@app.put("/api/projects/<name>/settings")
def api_settings(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings(root)
    if "main" in data:
        main = safe_path(root, data["main"])
        if not main.is_file():
            return err("Main file does not exist.")
        settings["main"] = main.relative_to(root).as_posix()
    if "engine" in data:
        if data["engine"] not in ENGINES:
            return err("Unknown engine.")
        settings["engine"] = data["engine"]
    save_settings(root, settings)
    return jsonify(settings)


@app.get("/api/projects/<name>/file")
def api_read_file(name):
    root = project_dir(name)
    path = safe_path(root, request.args.get("path", ""))
    if not path.is_file():
        return err("File not found.", 404)
    if not is_text_file(path):
        return err("Binary file - cannot be edited as text.", 415)
    return jsonify({"path": request.args["path"], "content": path.read_text(encoding="utf-8", errors="replace"),
                    "mtime": path.stat().st_mtime})


@app.put("/api/projects/<name>/file")
def api_write_file(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    path = safe_path(root, data.get("path", ""))
    if path.is_dir():
        return err("That path is a folder.")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a unique temp file then rename, so a crash never leaves a half-written file.
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(data.get("content", ""))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return jsonify({"saved": True, "mtime": path.stat().st_mtime})


@app.post("/api/projects/<name>/new")
def api_new_entry(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    path = safe_path(root, data.get("path", ""))
    if path.exists():
        return err("Something with that name already exists.", 409)
    if data.get("type") == "folder":
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data.get("content", ""), encoding="utf-8")
    return jsonify({"created": path.relative_to(root).as_posix()})


@app.post("/api/projects/<name>/move")
def api_move(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    src = safe_path(root, data.get("from", ""))
    dst = safe_path(root, data.get("to", ""))
    if not src.exists():
        return err("Source not found.", 404)
    if dst.exists():
        return err("Destination already exists.", 409)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    settings = load_settings(root)
    save_settings(root, settings)
    return jsonify({"moved": dst.relative_to(root).as_posix()})


@app.delete("/api/projects/<name>/file")
def api_delete_entry(name):
    root = project_dir(name)
    path = safe_path(root, request.args.get("path", ""))
    if not path.exists():
        return err("Not found.", 404)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return jsonify({"deleted": True})


@app.post("/api/projects/<name>/upload")
def api_upload(name):
    root = project_dir(name)
    folder = safe_path(root, request.form.get("folder", ""), allow_root=True)
    if not folder.is_dir():
        return err("Target folder does not exist.")
    saved = []
    for f in request.files.getlist("files"):
        filename = secure_filename(f.filename or "")
        if not filename:
            continue
        dest = safe_path(root, (folder / filename).relative_to(root.resolve()).as_posix())
        f.save(dest)
        saved.append(dest.relative_to(root.resolve()).as_posix())
    return jsonify({"saved": saved})


@app.get("/api/projects/<name>/raw/<path:rel>")
def api_raw(name, rel):
    root = project_dir(name)
    path = safe_path(root, rel)
    if not path.is_file():
        abort(404)
    return send_file(path)


@app.post("/api/projects/<name>/compile")
def api_compile(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings(root)
    main = data.get("main") or settings.get("main")
    engine = data.get("engine") or settings.get("engine", "pdflatex")
    if not main:
        return err("No main .tex file found. Create one containing \\documentclass, "
                   "or choose one in Settings.")
    if engine not in ENGINES:
        return err("Unknown engine.")
    lock = get_lock(name)
    if not lock.acquire(timeout=COMPILE_TIMEOUT):
        return err("Another compile is still running.", 409)
    try:
        result = run_compile(root, main, engine, clean=bool(data.get("clean")))
    finally:
        lock.release()
    result["main"] = main
    result["engine"] = engine
    return jsonify(result)


@app.get("/api/projects/<name>/pdf")
def api_pdf(name):
    root = project_dir(name)
    settings = load_settings(root)
    if not settings.get("main"):
        abort(404)
    pdf = root / BUILD_DIR_NAME / (Path(settings["main"]).stem + ".pdf")
    if not pdf.exists():
        abort(404)
    as_attachment = request.args.get("download") == "1"
    return send_file(pdf, mimetype="application/pdf", as_attachment=as_attachment,
                     download_name=f"{name}.pdf", max_age=0)


@app.get("/api/projects/<name>/synctex")
def api_synctex(name):
    """Forward search: source line -> PDF page (needs the `synctex` CLI)."""
    root = project_dir(name)
    settings = load_settings(root)
    if not shutil.which("synctex") or not settings.get("main"):
        return jsonify({"page": None})
    rel = request.args.get("path", settings["main"])
    line = int(request.args.get("line", "1"))
    src = safe_path(root, rel)
    pdf = root / BUILD_DIR_NAME / (Path(settings["main"]).stem + ".pdf")
    if not pdf.exists():
        return jsonify({"page": None})
    try:
        out = subprocess.run(["synctex", "view", "-i", f"{line}:0:{src}", "-o", str(pdf)],
                             cwd=root, capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return jsonify({"page": None})
    m = re.search(r"^Page:(\d+)", out, re.M)
    return jsonify({"page": int(m.group(1)) if m else None})


@app.get("/api/projects/<name>/wordcount")
def api_wordcount(name):
    root = project_dir(name)
    settings = load_settings(root)
    if not settings.get("main"):
        return jsonify({"words": 0, "detail": "No main file."})
    main = safe_path(root, settings["main"])
    if shutil.which("texcount"):
        try:
            out = subprocess.run(["texcount", "-inc", "-total", "-brief", "-sum", main.name],
                                 cwd=main.parent, capture_output=True, text=True, timeout=30).stdout
            m = re.search(r"(\d+)", out)
            return jsonify({"words": int(m.group(1)) if m else 0, "detail": out.strip(), "tool": "texcount"})
        except (subprocess.TimeoutExpired, OSError):
            pass
    # Rough fallback: strip comments, commands and math.
    text = main.read_text(encoding="utf-8", errors="ignore")
    text = text.split("\\begin{document}", 1)[-1].split("\\end{document}", 1)[0]
    text = re.sub(r"(?<!\\)%.*", "", text)
    text = re.sub(r"\$\$.*?\$\$|\$.*?\$", " ", text, flags=re.S)
    text = re.sub(r"\\(?:begin|end|label|ref|cite\w*|includegraphics|usepackage)\*?(\[[^\]]*\])?\{[^}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text)
    return jsonify({"words": len(words), "detail": "Approximate count (install texcount for accuracy).",
                    "tool": "builtin"})


@app.get("/api/projects/<name>/keys")
def api_keys(name):
    """Collect \\label{} names and BibTeX keys for editor autocompletion."""
    root = project_dir(name)
    labels, cites = set(), set()
    for f in root.rglob("*"):
        if not f.is_file() or any(p in HIDDEN_NAMES for p in f.relative_to(root).parts):
            continue
        suffix = f.suffix.lower()
        if suffix not in (".tex", ".bib") or f.stat().st_size > 5_000_000:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".tex":
            labels.update(re.findall(r"\\label\{([^}]+)\}", text))
        else:
            cites.update(k.strip() for k in re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", text)
                         if not k.lower().startswith(("comment", "string", "preamble")))
    return jsonify({"labels": sorted(labels), "cites": sorted(cites)})


@app.get("/api/projects/<name>/zip")
def api_zip(name):
    root = project_dir(name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in root.rglob("*"):
            rel_parts = f.relative_to(root).parts
            if f.is_file() and not any(p in HIDDEN_NAMES for p in rel_parts):
                zf.write(f, f.relative_to(root).as_posix())
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{name}.zip")


@app.post("/api/projects/<name>/clean")
def api_clean(name):
    root = project_dir(name)
    shutil.rmtree(root / BUILD_DIR_NAME, ignore_errors=True)
    return jsonify({"cleaned": True})


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main():
    global PROJECTS_DIR
    parser = argparse.ArgumentParser(description="Local Overleaf-style LaTeX editor")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind (default 127.0.0.1 - local machine only)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--projects-dir", default=os.environ.get("OVERLEAF_COPY_PROJECTS",
                                                                  str(BASE_DIR / "projects")))
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    args = parser.parse_args()

    PROJECTS_DIR = Path(args.projects_dir).expanduser().resolve()
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    tools = which_tools()
    url = f"http://{args.host}:{args.port}"
    print("=" * 60)
    print(" Overleaf Copy - local LaTeX editor")
    print(f" Projects folder : {PROJECTS_DIR}")
    print(f" TeX tools found : {', '.join(t for t, ok in tools.items() if ok) or 'NONE'}")
    if not any(tools[e] for e in ENGINES):
        print(" WARNING: no LaTeX engine found. Install TeX Live / MiKTeX / MacTeX to compile.")
    print(f" Open            : {url}")
    print("=" * 60)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
