"""Import Word documents and convert them to LaTeX.

Engines:
  * "builtin" - features/docx2tex.py, pure Python, always available.
  * "pandoc"  - uses Pandoc if installed (system `pandoc`, or `pip install pypandoc_binary`).
  * "auto"    - built-in converter (it understands paper structure: abstract, keywords,
                IEEE-style numbering, [1]-style citations -> \\cite, reference lists).
Old .doc, .odt and .rtf files are first turned into .docx with LibreOffice when it is installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from core import (err, iter_project_files, load_settings, project_dir, safe_path, save_settings)

from . import docx2tex

bp = Blueprint("word_import", __name__)

WORD_EXTS = {".docx"}
CONVERTIBLE_EXTS = {".doc", ".odt", ".rtf", ".docm", ".dotx"}
DOCCLASSES = {"article", "ieee", "report"}
MAX_BYTES = 100 * 1024 * 1024


# --------------------------------------------------------------------------- #
# External tools
# --------------------------------------------------------------------------- #
def pandoc_path() -> str | None:
    p = shutil.which("pandoc")
    if p:
        return p
    try:
        import pypandoc  # pip install pypandoc_binary  (bundles Pandoc)
        return pypandoc.get_pandoc_path()
    except Exception:
        return None


def soffice_path() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(cand):
            return cand
    return None


def to_docx(data: bytes, filename: str) -> bytes:
    """Return .docx bytes, converting .doc/.odt/.rtf with LibreOffice if needed."""
    ext = Path(filename).suffix.lower()
    if ext in WORD_EXTS:
        return data
    if ext not in CONVERTIBLE_EXTS:
        raise ValueError("Please choose a Word document (.docx). Older .doc, .odt and .rtf files also "
                         "work if LibreOffice is installed.")
    office = soffice_path()
    if not office:
        raise ValueError(f"{ext} files need LibreOffice to convert them (or open the file in Word and "
                         "use File → Save As → Word Document (.docx)).")
    with tempfile.TemporaryDirectory(prefix="oc-word-") as tmp:
        src = Path(tmp) / ("input" + ext)
        src.write_bytes(data)
        try:
            subprocess.run([office, "--headless", "--convert-to", "docx", "--outdir", tmp, str(src)],
                           capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"LibreOffice couldn't convert the file: {e}") from e
        out = Path(tmp) / "input.docx"
        if not out.exists():
            raise ValueError("LibreOffice couldn't convert the file to .docx.")
        return out.read_bytes()


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
def convert(data: bytes, docclass: str, engine: str, media_prefix: str = "") -> dict:
    """Return {"tex", "media": {relpath: bytes}, "warnings", "stats", "title", "engine"}."""
    if engine == "pandoc":
        return _convert_pandoc(data, media_prefix)
    r = docx2tex.convert_docx(data, docclass)
    media = r.media
    tex = r.tex
    if media_prefix:
        renamed = {}
        for path, blob in media.items():
            new = f"figures/{media_prefix}{Path(path).name}"
            tex = tex.replace("{" + path + "}", "{" + new + "}")
            renamed[new] = blob
        media = renamed
    return {"tex": tex, "media": media, "warnings": r.warnings, "stats": r.stats,
            "title": r.title, "engine": "builtin"}


def _convert_pandoc(data: bytes, media_prefix: str) -> dict:
    exe = pandoc_path()
    if not exe:
        raise ValueError("Pandoc isn't installed. Install it from pandoc.org (or `pip install pypandoc_binary`) "
                         "and restart, or use the built-in converter.")
    with tempfile.TemporaryDirectory(prefix="oc-pandoc-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "in.docx").write_bytes(data)
        try:
            proc = subprocess.run([exe, "in.docx", "-f", "docx", "-t", "latex", "--standalone",
                                   "--extract-media=media", "-o", "out.tex"],
                                  cwd=tmpd, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"Pandoc failed: {e}") from e
        if proc.returncode != 0 or not (tmpd / "out.tex").exists():
            raise ValueError("Pandoc failed: " + (proc.stderr or "")[-500:])
        tex = (tmpd / "out.tex").read_text(encoding="utf-8", errors="replace")
        media = {}
        warnings = [l for l in (proc.stderr or "").splitlines() if l.strip()][:20]
        for f in sorted((tmpd / "media").rglob("*")) if (tmpd / "media").exists() else []:
            if f.is_file():
                rel_old = f.relative_to(tmpd).as_posix()
                new = f"figures/{media_prefix}{f.name}"
                n = 1
                while new in media:
                    new = f"figures/{media_prefix}{f.stem}-{n}{f.suffix}"
                    n += 1
                media[new] = f.read_bytes()
                tex = tex.replace(rel_old, new)
                if f.suffix.lower() in (".emf", ".wmf", ".tif", ".tiff", ".bmp", ".svg"):
                    warnings.append(f"{new}: {f.suffix[1:].upper()} images can't be used by pdfLaTeX — "
                                    "convert it to PNG or PDF.")
    m = re.search(r"\\title\{(.*?)\}", tex)
    return {"tex": tex, "media": media, "warnings": warnings,
            "stats": {"images": len(media)}, "title": m.group(1) if m else "", "engine": "pandoc"}


def body_only(tex: str) -> str:
    """The part of a converted document that can be \\input into another document."""
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, re.S)
    body = m.group(1) if m else tex
    body = re.sub(r"^\s*\\maketitle\s*", "", body.strip())
    return body.strip() + "\n"


PACKAGE_HINTS = [  # (pattern in the converted body, package, options)
    (r"\\includegraphics", "graphicx", ""), (r"\\(top|mid|bottom)rule", "booktabs", ""),
    (r"\\href\{|\\url\{", "hyperref", ""), (r"\\sout\{", "ulem", "normalem"),
    (r"\\begin\{(aligned|align|gathered)\}|\\text\{|\\operatorname|\\genfrac|\\iint", "amsmath", ""),
    (r"\\mathbb|\\mathfrak", "amssymb", ""), (r"\\textdegree|\\texteuro", "textcomp", ""),
    (r"p\{[0-9.]+\\linewidth\}", "array", ""), (r"\\begin\{longtable\}", "longtable", ""),
]


def needed_packages(body: str) -> list[list[str]]:
    return [[pkg, opts] for pat, pkg, opts in PACKAGE_HINTS if re.search(pat, body)]


def _unique_media(root: Path, media: dict) -> dict:
    """Rename images that would overwrite files already in the project."""
    out = {}
    for rel, blob in media.items():
        p = Path(rel)
        cand, n = rel, 1
        while (root / cand).exists() or cand in out:
            cand = (p.parent / f"{p.stem}-{n}{p.suffix}").as_posix()
            n += 1
        out[cand] = blob
    return out


def _write_media(root: Path, media: dict, tex: str) -> str:
    renamed = _unique_media(root, media)
    for (old, _), new in zip(media.items(), renamed):
        if old != new:
            tex = tex.replace("{" + old + "}", "{" + new + "}")
    for rel, blob in renamed.items():
        dest = safe_path(root, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
    return tex


def _read_upload() -> tuple[bytes, str]:
    f = request.files.get("file")
    if not f or not f.filename:
        raise ValueError("Choose a Word document to import.")
    data = f.read()
    if len(data) > MAX_BYTES:
        raise ValueError("That file is too large (over 100 MB).")
    return data, f.filename


def _options():
    docclass = request.form.get("docclass", "article")
    engine = request.form.get("engine", "auto")
    if docclass not in DOCCLASSES:
        docclass = "article"
    if engine not in ("auto", "builtin", "pandoc"):
        engine = "auto"
    if engine == "auto":
        engine = "builtin"
    return docclass, engine


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@bp.get("/api/word/status")
def api_status():
    pd = pandoc_path()
    version = None
    if pd:
        try:
            version = subprocess.run([pd, "--version"], capture_output=True, text=True,
                                     timeout=15).stdout.splitlines()[0]
        except (OSError, subprocess.TimeoutExpired, IndexError):
            pass
    return jsonify({"pandoc": bool(pd), "pandoc_version": version, "libreoffice": bool(soffice_path()),
                    "accepts": sorted(WORD_EXTS | (CONVERTIBLE_EXTS if soffice_path() else set()))})


@bp.post("/api/import/word")
def api_new_project():
    """Create a new project from a Word document."""
    try:
        data, filename = _read_upload()
        docclass, engine = _options()
        docx = to_docx(data, filename)
        result = convert(docx, docclass, engine)
    except ValueError as e:
        return err(str(e))
    name = (request.form.get("name") or "").strip() or Path(filename).stem
    name = re.sub(r"[^A-Za-z0-9 _.\-]", "_", name)[:80].strip(" ._") or "word-import"
    root = project_dir(name, must_exist=False)
    if root.exists():
        return err(f"A project called '{name}' already exists.", 409)
    root.mkdir(parents=True)
    tex = _write_media(root, result["media"], result["tex"])
    (root / "main.tex").write_text(tex, encoding="utf-8")
    original = root / "word" / (secure_filename(filename) or "original.docx")
    original.parent.mkdir(exist_ok=True)
    original.write_bytes(data)
    settings = load_settings(root)
    settings["main"] = "main.tex"
    settings["engine"] = "pdflatex"
    save_settings(root, settings)
    return jsonify({"name": name, "engine": result["engine"], "warnings": result["warnings"],
                    "stats": result["stats"], "title": result["title"]})


def _import_into(root: Path, data: bytes, filename: str, target: str | None, engine: str):
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename).stem).strip("-").lower() or "imported"
    docx = to_docx(data, filename)
    result = convert(docx, "article", engine, media_prefix=f"{stem}-")
    rel = target or f"sections/{stem}.tex"
    if not rel.endswith(".tex"):
        rel += ".tex"
    dest = safe_path(root, rel)
    n = 1
    while dest.exists():
        rel = f"{Path(rel).with_suffix('').as_posix().rstrip('-0123456789')}-{n}.tex"
        dest = safe_path(root, rel)
        n += 1
    body = body_only(result["tex"])
    if result["engine"] == "pandoc":
        body = ("% Converted with Pandoc. If the main file doesn't load them yet, you may need:\n"
                "% \\usepackage{longtable,booktabs,graphicx,hyperref}  and  \\providecommand{\\tightlist}"
                "{\\setlength{\\itemsep}{0pt}\\setlength{\\parskip}{0pt}}\n" + body)
    body = _write_media(root, result["media"], body)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    return {"path": rel, "input": Path(rel).with_suffix("").as_posix(), "engine": result["engine"],
            "warnings": result["warnings"], "stats": result["stats"], "packages": needed_packages(body)}


@bp.post("/api/projects/<name>/import/word")
def api_import_into(name):
    """Convert a Word document into a .tex file inside an existing project (body only)."""
    root = project_dir(name)
    try:
        data, filename = _read_upload()
        _, engine = _options()
        return jsonify(_import_into(root, data, filename, request.form.get("target") or None, engine))
    except ValueError as e:
        return err(str(e))


@bp.post("/api/projects/<name>/convert-word")
def api_convert_existing(name):
    """Convert a .docx that is already in the project (e.g. uploaded via the file tree)."""
    root = project_dir(name)
    d = request.get_json(force=True, silent=True) or {}
    src = safe_path(root, d.get("path", ""))
    if not src.is_file():
        return err("File not found.", 404)
    engine = d.get("engine") if d.get("engine") in ("builtin", "pandoc") else "builtin"
    target = (src.parent / (src.stem + ".tex")).relative_to(root.resolve()).as_posix()
    try:
        return jsonify(_import_into(root, src.read_bytes(), src.name, target, engine))
    except ValueError as e:
        return err(str(e))
