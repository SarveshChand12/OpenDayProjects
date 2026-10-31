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


import core
from core import *  # noqa: F401,F403  (helpers shared with feature modules)
from core import (BUILD_DIR_NAME, ENGINES, HIDDEN_NAMES, STATIC_DIR, TEMPLATES_DIR, COMPILE_TIMEOUT,
                  build_tree, err, get_lock, is_text_file, load_settings, project_dir, run_compile,
                  safe_path, save_settings, which_tools)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB uploads

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
    return jsonify({"tools": which_tools(), "projects_dir": str(core.PROJECTS_DIR),
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
    for d in sorted(core.PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
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
    for hook in core.COMPILE_HOOKS:
        try:
            hook(root, result)
        except Exception as exc:  # a broken hook must never break compiling
            app.logger.warning("compile hook %s failed: %s", hook, exc)
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
def configure(projects_dir: str | Path) -> None:
    core.PROJECTS_DIR = Path(projects_dir).expanduser().resolve()
    core.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


# Feature modules (history, references, checks, ...) register their own routes.
import features  # noqa: E402

features.register(app)


def main():
    
    parser = argparse.ArgumentParser(description="Local Overleaf-style LaTeX editor")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind (default 127.0.0.1 - local machine only)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--projects-dir", default=os.environ.get("OVERLEAF_COPY_PROJECTS",
                                                                  str(BASE_DIR / "projects")))
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    args = parser.parse_args()

    configure(args.projects_dir)

    tools = which_tools()
    url = f"http://{args.host}:{args.port}"
    print("=" * 60)
    print(" Overleaf Copy - local LaTeX editor")
    print(f" Projects folder : {core.PROJECTS_DIR}")
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
