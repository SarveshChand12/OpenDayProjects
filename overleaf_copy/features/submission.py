"""Submission helpers: pre-submission checklist, arXiv-ready export, and the to-do scanner."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import core
from core import (BUILD_DIR_NAME, build_log_path, build_pdf_path, err, get_lock, iter_project_files,
                  load_settings, parse_log, project_dir, read_text, run_compile, strip_comment)

bp = Blueprint("submission", __name__)

TODO_COMMENT_RE = re.compile(r"(?<!\\)%.*?\b(TODO|FIXME|XXX|HACK)\b[:\s]*(.*)", re.I)
TODO_CMD_RE = re.compile(r"\\(todo|fixme|hl|note|comment)\s*(?:\[[^\]]*\])?\{([^}]*)\}")
UNDEF_RE = re.compile(r"(Reference|Citation) `([^']+)' on page \d+ undefined on input line (\d+)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# --------------------------------------------------------------------------- #
# To-do scanner
# --------------------------------------------------------------------------- #
def find_todos(root: Path) -> list[dict]:
    items = []
    for path, rel in iter_project_files(root):
        if path.suffix.lower() not in (".tex", ".bib", ".ltx"):
            continue
        for i, line in enumerate(read_text(path).splitlines(), 1):
            m = TODO_COMMENT_RE.search(line)
            if m:
                items.append({"path": rel, "line": i, "kind": m.group(1).upper(),
                              "text": m.group(2).strip() or line.strip()})
                continue
            code = strip_comment(line)
            for m in TODO_CMD_RE.finditer(code):
                items.append({"path": rel, "line": i, "kind": "\\" + m.group(1), "text": m.group(2).strip()})
            if "??" in code:
                items.append({"path": rel, "line": i, "kind": "??", "text": code.strip()[:120]})
    return items


def undefined_refs(root: Path) -> list[dict]:
    log = build_log_path(root)
    if not log or not log.exists():
        return []
    out, seen = [], set()
    text = read_text(log).replace("\n", " ")
    for m in UNDEF_RE.finditer(text):
        key = (m.group(1), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": m.group(1).lower(), "key": m.group(2), "line": int(m.group(3))})
    return out


@bp.get("/api/projects/<name>/todos")
def api_todos(name):
    root = project_dir(name)
    return jsonify({"todos": find_todos(root), "undefined": undefined_refs(root)})


# --------------------------------------------------------------------------- #
# Pre-submission checklist
# --------------------------------------------------------------------------- #
def _sources_newer_than(root: Path, pdf: Path) -> bool:
    t = pdf.stat().st_mtime
    return any(p.stat().st_mtime > t for p, _ in iter_project_files(root))


def _check(cid, title, status, detail="", items=None):
    return {"id": cid, "title": title, "status": status, "detail": detail, "items": items or []}


def _pdf_page_count(pdf: Path, log_text: str) -> int | None:
    m = re.search(r"Output written on .*?\((\d+) pages?", log_text.replace("\n", ""))
    if m:
        return int(m.group(1))
    if shutil.which("pdfinfo"):
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        if m:
            return int(m.group(1))
    return None


def _pdf_meta(pdf: Path) -> dict:
    if not shutil.which("pdfinfo"):
        return {}
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=20).stdout
    meta = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def _fonts(pdf: Path) -> list[dict] | None:
    if not shutil.which("pdffonts"):
        return None
    out = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, timeout=30).stdout.splitlines()
    fonts = []
    for line in out[2:]:
        # name type encoding emb sub uni object ID  (name may contain no spaces; type may be "Type 1C")
        m = re.match(r"^(\S+)\s+(.+?)\s{2,}(\S+)\s+(yes|no)\s+(yes|no)\s+(yes|no)", line)
        if m:
            fonts.append({"name": m.group(1), "type": m.group(2).strip(), "embedded": m.group(4) == "yes"})
    return fonts


@bp.post("/api/projects/<name>/check")
def api_check(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings(root)
    main = settings.get("main")
    if not main:
        return err("No main .tex file.")
    pdf = build_pdf_path(root, settings)

    # Make sure the PDF reflects the current sources.
    compile_result = None
    if data.get("recompile", True) or not pdf.exists() or _sources_newer_than(root, pdf):
        lock = get_lock(name)
        with lock:
            compile_result = run_compile(root, main, settings.get("engine", "pdflatex"))
    log_path = build_log_path(root, settings)
    log_text = read_text(log_path) if log_path and log_path.exists() else ""
    issues = compile_result["issues"] if compile_result else parse_log(log_text, main)
    checks = []

    # 1. Compiles cleanly
    errors = [i for i in issues if i["level"] == "error"]
    if not pdf.exists():
        checks.append(_check("compile", "Document compiles", "fail", "No PDF could be produced."))
    elif errors:
        checks.append(_check("compile", "Document compiles without errors", "fail",
                             f"{len(errors)} LaTeX error(s).",
                             [{"path": e["file"], "line": e["line"], "text": e["message"]} for e in errors[:30]]))
    else:
        checks.append(_check("compile", "Document compiles without errors", "pass"))

    # 2. Undefined references / citations
    undef = undefined_refs(root)
    multiply = re.findall(r"Label `([^']+)' multiply defined", log_text)
    if undef or multiply:
        items = [{"path": main, "line": u["line"], "text": f"{u['kind']} '{u['key']}' is undefined"} for u in undef]
        items += [{"path": None, "line": None, "text": f"label '{l}' is defined more than once"} for l in sorted(set(multiply))]
        checks.append(_check("refs", "All references and citations resolve", "fail",
                             f"{len(undef)} undefined, {len(set(multiply))} duplicate label(s). They show as ?? in the PDF.", items))
    else:
        checks.append(_check("refs", "All references and citations resolve", "pass"))

    # 3. Page limit
    pages = _pdf_page_count(pdf, log_text) if pdf.exists() else None
    limit = data.get("page_limit")
    if pages is None:
        checks.append(_check("pages", "Page count", "info", "Couldn't determine the page count."))
    elif limit:
        status = "pass" if pages <= int(limit) else "fail"
        checks.append(_check("pages", f"Within the page limit ({limit})", status,
                             f"The PDF has {pages} page(s)" + ("" if status == "pass" else f" — {pages - int(limit)} over the limit") + "."))
    else:
        checks.append(_check("pages", "Page count", "info", f"The PDF has {pages} page(s). Enter a page limit to check it."))

    # 4. Fonts embedded
    if pdf.exists():
        fonts = _fonts(pdf)
        if fonts is None:
            checks.append(_check("fonts", "All fonts embedded", "info", "Install poppler-utils (pdffonts) to check fonts."))
        else:
            not_emb = [f for f in fonts if not f["embedded"]]
            type3 = [f for f in fonts if f["type"].startswith("Type 3")]
            if not_emb:
                checks.append(_check("fonts", "All fonts embedded", "fail",
                                     "Many venues (IEEE PDF eXpress, ACM) reject PDFs with non-embedded fonts. "
                                     "Often caused by figures exported from other tools.",
                                     [{"path": None, "line": None, "text": f"{f['name']} ({f['type']})"} for f in not_emb]))
            elif type3:
                checks.append(_check("fonts", "All fonts embedded", "warn",
                                     "Type 3 (bitmap) fonts found — usually from matplotlib figures. "
                                     "Set matplotlib rcParams['pdf.fonttype'] = 42 or use \\usepackage[T1]{fontenc}.",
                                     [{"path": None, "line": None, "text": f["name"]} for f in type3]))
            else:
                checks.append(_check("fonts", "All fonts embedded", "pass", f"{len(fonts)} font(s), all embedded."))

    # 5. Overfull boxes (text running into the margin)
    over = [i for i in issues if i["level"] == "typesetting" and i["message"].startswith("Overfull")]
    if over:
        checks.append(_check("overfull", "No text running into the margins", "warn",
                             f"{len(over)} overfull box(es). Small ones (< 1pt) are invisible; large ones stick out.",
                             [{"path": i["file"], "line": i["line"], "text": i["message"]} for i in over[:30]]))
    else:
        checks.append(_check("overfull", "No text running into the margins", "pass"))

    # 6. Leftover to-dos
    todos = find_todos(root)
    if todos:
        checks.append(_check("todos", "No leftover TODOs / \\todo notes / ??", "warn", f"{len(todos)} found.",
                             [{"path": t["path"], "line": t["line"], "text": f"{t['kind']}: {t['text']}"} for t in todos[:50]]))
    else:
        checks.append(_check("todos", "No leftover TODOs / \\todo notes / ??", "pass"))

    # 7. Anonymity (double-blind)
    if data.get("anonymous"):
        problems = []
        names = [n.strip() for n in (data.get("names") or []) if n.strip()]
        for path, rel in iter_project_files(root):
            if path.suffix.lower() not in (".tex",):
                continue
            for i, line in enumerate(read_text(path).splitlines(), 1):
                code = strip_comment(line)
                if not code.strip():
                    continue
                m = re.search(r"\\author\s*(\[[^\]]*\])?\{([^}]*)", code)
                if m and m.group(2).strip() and not re.search(r"anonym", m.group(2), re.I):
                    problems.append({"path": rel, "line": i, "text": "\\author{...} is not anonymous"})
                if "\\thanks" in code:
                    problems.append({"path": rel, "line": i, "text": "\\thanks may reveal funding/affiliation"})
                if EMAIL_RE.search(code):
                    problems.append({"path": rel, "line": i, "text": f"e-mail address: {EMAIL_RE.search(code).group()}"})
                if re.search(r"\\(section|paragraph|subsection)\*?\{acknowledg", code, re.I):
                    problems.append({"path": rel, "line": i, "text": "Acknowledgements section (usually removed for review)"})
                if re.search(r"\b(our (previous|prior|earlier) (work|paper)|we previously (showed|proposed))", code, re.I):
                    problems.append({"path": rel, "line": i, "text": "self-reference: '" + code.strip()[:80] + "'"})
                if re.search(r"github\.com/[A-Za-z0-9_-]+", code):
                    problems.append({"path": rel, "line": i, "text": "GitHub link may reveal your identity"})
                for n in names:
                    if re.search(r"\b" + re.escape(n) + r"\b", code, re.I):
                        problems.append({"path": rel, "line": i, "text": f"mentions '{n}'"})
                    # Also catch it in comments, which arXiv/reviewers can see in the source.
                if names and line != code:
                    for n in names:
                        if re.search(r"\b" + re.escape(n) + r"\b", line[len(code):], re.I):
                            problems.append({"path": rel, "line": i, "text": f"comment mentions '{n}' (comments are visible in source uploads)"})
        meta = _pdf_meta(pdf) if pdf.exists() else {}
        if meta.get("Author") and not re.search(r"anonym", meta["Author"], re.I):
            problems.append({"path": None, "line": None, "text": f"PDF metadata Author = '{meta['Author']}' (set pdfauthor={{}} in \\hypersetup)"})
        checks.append(_check("anon", "Anonymous for double-blind review", "fail" if problems else "pass",
                             "" if not problems else f"{len(problems)} possible identity leak(s).", problems[:60]))

    # 8. Files arXiv / submission systems dislike
    bad_names, big = [], []
    total = 0
    for path, rel in iter_project_files(root):
        size = path.stat().st_size
        total += size
        if re.search(r"[^A-Za-z0-9._/+-]", rel):
            bad_names.append({"path": rel, "line": None, "text": "contains spaces or special characters"})
        if size > 10 * 1024 * 1024:
            big.append({"path": rel, "line": None, "text": f"{size / 1048576:.1f} MB"})
    if bad_names or big:
        checks.append(_check("files", "File names and sizes are upload-friendly", "warn",
                             f"Project size {total / 1048576:.1f} MB (arXiv limit: 50 MB).", bad_names + big))
    else:
        checks.append(_check("files", "File names and sizes are upload-friendly", "pass",
                             f"Project size {total / 1048576:.1f} MB."))

    # 9. Bibliography compiled (.bbl needed for arXiv)
    stem = Path(main).stem
    uses_bib = bool(re.search(r"\\(bibliography|addbibresource)\{", "\n".join(read_text(p) for p, r in iter_project_files(root) if p.suffix == ".tex")))
    if uses_bib:
        bbl = root / BUILD_DIR_NAME / f"{stem}.bbl"
        checks.append(_check("bbl", "Bibliography (.bbl) generated", "pass" if bbl.exists() else "warn",
                             "arXiv doesn't run BibTeX — the arXiv export includes this file." if bbl.exists()
                             else "No .bbl file found; compile so BibTeX/Biber runs."))

    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("pass", "warn", "fail", "info")}
    return jsonify({"checks": checks, "summary": summary, "pages": pages})


# --------------------------------------------------------------------------- #
# arXiv export
# --------------------------------------------------------------------------- #
def strip_tex_comments(text: str) -> str:
    """Remove comments like arxiv_latex_cleaner: drop comment-only lines, keep a trailing '%'
    after code (it can matter for spacing), leave verbatim-like environments untouched."""
    out, in_verb = [], False
    for line in text.splitlines():
        if re.search(r"\\begin\{(verbatim|lstlisting|minted|comment)\*?\}", line):
            in_verb = True
        if in_verb:
            out.append(line)
            if re.search(r"\\end\{(verbatim|lstlisting|minted|comment)\*?\}", line):
                in_verb = False
            continue
        code = strip_comment(line)
        if code == line:
            out.append(line)
        elif code.strip() == "":
            continue                       # whole-line comment
        else:
            out.append(code + "%")
    return "\n".join(out) + "\n"


def _used_files(root: Path, stem: str) -> set[str] | None:
    """Files LaTeX actually read, from latexmk's .fls recorder file."""
    fls = root / BUILD_DIR_NAME / f"{stem}.fls"
    if not fls.exists():
        return None
    used = set()
    pwd = None
    for line in read_text(fls).splitlines():
        if line.startswith("PWD "):
            pwd = Path(line[4:].strip())
        elif line.startswith("INPUT "):
            p = Path(line[6:].strip())
            if not p.is_absolute() and pwd:
                p = pwd / p
            try:
                rel = p.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if not rel.startswith(BUILD_DIR_NAME):
                used.add(rel)
    return used


@bp.get("/api/projects/<name>/export/arxiv")
def api_export_arxiv(name):
    root = project_dir(name)
    settings = load_settings(root)
    main = settings.get("main")
    if not main:
        return err("No main .tex file.")
    stem = Path(main).stem
    strip = request.args.get("strip_comments", "1") == "1"
    only_used = request.args.get("only_used", "1") == "1"
    used = _used_files(root, stem) if only_used else None
    buf = io.BytesIO()
    included = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in iter_project_files(root):
            is_bib = path.suffix.lower() == ".bib"
            has_bbl = (root / BUILD_DIR_NAME / f"{stem}.bbl").exists()
            if is_bib and has_bbl:
                continue                    # arXiv uses the .bbl instead
            if used is not None and rel not in used and rel != main and not is_bib:
                continue                    # LaTeX never read this file
            if path.suffix.lower() == ".tex" and strip:
                zf.writestr(rel, strip_tex_comments(read_text(path)))
            else:
                zf.write(path, rel)
            included.append(rel)
        bbl = root / BUILD_DIR_NAME / f"{stem}.bbl"
        if bbl.exists():
            target = (Path(main).parent / f"{stem}.bbl").as_posix()
            zf.write(bbl, target)
            included.append(target)
        engine = settings.get("engine", "pdflatex")
        if engine != "pdflatex":
            zf.writestr("00README.json", '{"process": {"compiler": "%s"}, "sources": [{"filename": "%s", "usage": "toplevel"}], "spec_version": 1}\n'
                        % (engine, main))
    buf.seek(0)
    resp = send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"{name}-arxiv.zip")
    resp.headers["X-Included-Files"] = str(len(included))
    return resp


@bp.get("/api/projects/<name>/export/arxiv/preview")
def api_export_preview(name):
    """List what the arXiv zip would contain."""
    root = project_dir(name)
    settings = load_settings(root)
    stem = Path(settings.get("main") or "main").stem
    used = _used_files(root, stem)
    has_bbl = (root / BUILD_DIR_NAME / f"{stem}.bbl").exists()
    files = []
    for path, rel in iter_project_files(root):
        is_bib = path.suffix.lower() == ".bib"
        if is_bib and has_bbl:
            files.append({"path": rel, "included": False, "why": "replaced by the .bbl"})
        elif used is not None and rel not in used and rel != settings.get("main") and not is_bib:
            files.append({"path": rel, "included": False, "why": "not used by LaTeX"})
        else:
            files.append({"path": rel, "included": True, "why": ""})
    return jsonify({"files": files, "has_bbl": has_bbl, "recorder": used is not None,
                    "engine": settings.get("engine", "pdflatex")})
