"""Shared helpers for Overleaf Copy: paths, settings, compilation and log parsing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from flask import abort, jsonify

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "project_templates"

BUILD_DIR_NAME = ".build"
SETTINGS_FILE = ".overleaf_copy.json"
HISTORY_DIR_NAME = ".history"
NOTES_FILE = ".overleaf_notes.json"
HIDDEN_NAMES = {BUILD_DIR_NAME, SETTINGS_FILE, HISTORY_DIR_NAME, NOTES_FILE,
                ".git", ".gitignore", ".DS_Store", "__MACOSX"}
RESERVED_NAMES = {BUILD_DIR_NAME, SETTINGS_FILE, HISTORY_DIR_NAME, NOTES_FILE, ".git"}

TEXT_EXTENSIONS = {
    ".tex", ".bib", ".cls", ".sty", ".bst", ".bbx", ".cbx", ".txt", ".md",
    ".csv", ".dat", ".tikz", ".pgf", ".ltx", ".dtx", ".ins", ".cfg", ".def",
    ".clo", ".fd", ".json", ".yaml", ".yml", ".py", ".r", ".m", ".latexmkrc",
}
ENGINES = {"pdflatex", "xelatex", "lualatex"}
COMPILE_TIMEOUT = int(os.environ.get("OVERLEAF_COPY_TIMEOUT", "180"))
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,79}$")

PROJECTS_DIR: Path = BASE_DIR / "projects"

# Callbacks run after every compile: fn(project_root, result_dict).
COMPILE_HOOKS: list = []

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
    if parts and parts[0] in RESERVED_NAMES:
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


# latexmk, latexdiff and texcount are Perl scripts. TeX Live (incl. Windows) ships Perl, but
# MiKTeX does not: its latexmk.exe launcher exists yet fails with "could not find the script
# engine 'perl'". So for these tools "on PATH" isn't enough - we check they actually run.
PERL_TOOLS = {"latexmk": ["-v"], "latexdiff": ["--version"], "texcount": ["-ver"]}
_broken_tools: set[str] = set()
_tool_cache: dict[str, bool] = {}


def _looks_like_missing_perl(output: str) -> bool:
    o = output.lower()
    return "script engine" in o or ("perl" in o and ("not found" in o or "could not find" in o
                                                    or "is not recognized" in o or "no such file" in o))


def tool_works(tool: str) -> bool:
    """True if `tool` is on PATH and (for Perl-based tools) can actually start."""
    if tool in _broken_tools or os.environ.get(f"OVERLEAF_COPY_NO_{tool.upper()}"):
        return False
    if tool in _tool_cache:
        return _tool_cache[tool]
    ok = shutil.which(tool) is not None
    if ok and tool in PERL_TOOLS:
        try:
            p = subprocess.run([tool, *PERL_TOOLS[tool]], capture_output=True, text=True, errors="replace",
                               timeout=30, stdin=subprocess.DEVNULL)
            ok = p.returncode == 0 and not _looks_like_missing_perl(p.stdout + p.stderr)
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    _tool_cache[tool] = ok
    return ok


def mark_broken(tool: str) -> None:
    _broken_tools.add(tool)


def perl_missing() -> list[str]:
    """Perl-based tools that are installed but can't run (typically MiKTeX without Perl)."""
    return [t for t in PERL_TOOLS if shutil.which(t) and not tool_works(t)]


def which_tools() -> dict:
    tools = {tool: (tool_works(tool) if tool in PERL_TOOLS else shutil.which(tool) is not None)
             for tool in ("latexmk", "pdflatex", "xelatex", "lualatex", "bibtex", "biber",
                          "synctex", "texcount", "latexdiff", "makeindex")}
    tools["perl_missing"] = perl_missing()
    return tools


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


def unwrap_log(log: str) -> list[str]:
    """TeX hard-wraps log lines at 79 characters; re-join them into logical lines."""
    out, buf = [], ""
    for line in log.split("\n"):
        line = line.rstrip("\r")
        buf += line
        if len(line) == 79:
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


FILE_OPEN_RE = re.compile(r"\((\.{0,2}/[^\s()]+|[A-Za-z]:[\\/][^\s()]+|[\w\-.]+\.(?:tex|ltx|bbl|aux|toc|sty|cls|cfg|def|clo|fd))")


def _project_rel(path: str | None) -> str | None:
    """Map a path from the log to a project-relative path, or None for system files."""
    if not path or path.startswith("/") or re.match(r"[A-Za-z]:[\\/]", path):
        return None
    rel = path[2:] if path.startswith("./") else path
    if rel.startswith(BUILD_DIR_NAME + "/") or not rel.endswith((".tex", ".ltx")):
        return None
    return rel


def parse_log(log: str, main_file: str | None) -> list[dict]:
    issues: list[dict] = []
    lines = unwrap_log(log)
    stack: list[str | None] = []      # TeX's "(file ... )" nesting tells us which file is being read

    def current_file() -> str | None:
        for f in reversed(stack):
            rel = _project_rel(f)
            if rel:
                return rel
        return main_file

    def track(text: str) -> None:
        i = 0
        while i < len(text):
            c = text[i]
            if c == "(":
                m = FILE_OPEN_RE.match(text, i)
                if m:
                    stack.append(m.group(1))
                    i = m.end()
                    continue
                stack.append(None)
            elif c == ")" and stack:
                stack.pop()
            i += 1

    i = 0
    while i < len(lines):
        line = lines[i]
        m = FILE_LINE_ERROR_RE.match(line)
        if m:
            context = []
            for j in range(i + 1, min(i + 8, len(lines))):
                if lines[j].startswith("l.") or not lines[j].strip():
                    if lines[j].startswith("l."):
                        context.append(lines[j])
                    break
                context.append(lines[j])
            issues.append({"level": "error", "file": _project_rel(m.group(1)) or m.group(1).lstrip("./"),
                           "line": int(m.group(2)), "message": m.group(3), "context": "\n".join(context)})
            i += 1
            continue
        if line.startswith("! "):
            line_no = None
            for j in range(i + 1, min(i + 15, len(lines))):
                lm = LINE_NO_RE.match(lines[j])
                if lm:
                    line_no = int(lm.group(1))
                    break
            issues.append({"level": "error", "file": current_file(), "line": line_no,
                           "message": line[2:], "context": ""})
            i += 1
            continue
        wm = WARNING_RE.match(line)
        if wm:
            text = wm.group(3)
            k = i + 1
            # Package warnings continue on lines indented with "(pkg)  " or spaces.
            while k < len(lines) and re.match(r"^(\s+|\(\w+\)\s+)\S", lines[k]) and len(text) < 600:
                text += " " + re.sub(r"^\(\w+\)\s+", "", lines[k]).strip()
                k += 1
            src = wm.group(1) or wm.group(2)
            lm = INPUT_LINE_RE.search(text)
            issues.append({"level": "warning", "file": current_file(),
                           "line": int(lm.group(1)) if lm else None,
                           "message": (f"[{src}] " if src else "") + text,
                           "context": ""})
            i = k
            continue
        bm = BOX_RE.match(line)
        if bm:
            issues.append({"level": "typesetting", "file": current_file(), "line": int(bm.group(2)),
                           "message": bm.group(1), "context": ""})
        track(line)
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
def _manual_pipeline(run, build: Path, root: Path, engine: str, common: list[str], main_rel: str,
                     stem: str, tools: dict) -> int:
    """Compile without latexmk: engine, then BibTeX/Biber and makeindex if needed, then rerun
    the engine until references settle (at most 4 runs in total)."""
    base = [engine, f"-output-directory={BUILD_DIR_NAME}", *common, main_rel]
    rc = run(base)
    aux, bcf, idx = build / f"{stem}.aux", build / f"{stem}.bcf", build / f"{stem}.idx"
    log = build / f"{stem}.log"
    if not aux.exists():
        return rc                      # fatal error before LaTeX wrote anything useful
    extra = False
    if bcf.exists() and tools.get("biber"):
        run(["biber", f"--input-directory={BUILD_DIR_NAME}", f"--output-directory={BUILD_DIR_NAME}", stem])
        extra = True
    elif tools.get("bibtex") and "\\bibdata" in aux.read_text(errors="ignore"):
        # Run inside the build folder (like latexmk) - MiKTeX/TeX Live refuse to write into a
        # dot-folder given as a path. BIBINPUTS/BSTINPUTS point back at the project.
        run(["bibtex", stem], cwd=build)
        extra = True
    if idx.exists() and tools.get("makeindex"):
        run(["makeindex", f"{stem}.idx"], cwd=build)
        extra = True
    runs = 1
    rerun_re = re.compile(r"Rerun to get|Label\(s\) may have changed|There were undefined references|"
                          r"Please \(re\)run Biber|rerun LaTeX", re.I)
    while runs < 4:
        log_text = log.read_text(errors="replace") if log.exists() else ""
        if not extra and not rerun_re.search(log_text):
            break
        extra = False
        rc = run(base)
        runs += 1
        if runs >= 3 and "There were undefined references" in (log.read_text(errors="replace") if log.exists() else "") \
                and not re.search(r"Rerun to get|may have changed", log.read_text(errors="replace")):
            break                      # genuinely undefined - more runs won't help
    return rc


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
    env["INDEXSTYLE"] = f".{sep}{root}{sep}{env.get('INDEXSTYLE', '')}"

    # nonstopmode (without -halt-on-error) behaves like Overleaf: a PDF is still
    # produced where possible and errors are reported alongside it.
    common = ["-interaction=nonstopmode", "-file-line-error", "-synctex=1"]
    log_chunks: list[str] = []
    started = time.time()

    last_output = [""]

    def run(cmd: list[str], cwd: Path | None = None) -> int:
        log_chunks.append("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(cmd, cwd=cwd or root, env=env, capture_output=True, text=True,
                                  errors="replace", timeout=COMPILE_TIMEOUT,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            log_chunks.append(f"!! Timed out after {COMPILE_TIMEOUT}s")
            return -1
        except FileNotFoundError:
            log_chunks.append(f"!! Command not found: {cmd[0]}")
            return -1
        last_output[0] = proc.stdout + proc.stderr
        log_chunks.append(proc.stdout[-20000:] + proc.stderr[-5000:])
        return proc.returncode

    if not tools.get(engine):
        return {"ok": False, "issues": [],
                "log": f"'{engine}' was not found on PATH. Install a TeX distribution "
                       f"(TeX Live, MiKTeX or MacTeX) and restart the server."}

    used_latexmk = False
    if tools["latexmk"]:
        flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
        rc = run(["latexmk", flag, "-f", "-silent", f"-outdir={BUILD_DIR_NAME}",
                  *common, main_rel])
        used_latexmk = True
        if _looks_like_missing_perl(last_output[0]):
            # e.g. MiKTeX without Perl: remember that and fall back to the built-in pipeline.
            mark_broken("latexmk")
            log_chunks.append("!! latexmk needs Perl, which isn't installed - using the built-in "
                              "compile pipeline instead (install Strawberry Perl to use latexmk).")
            used_latexmk = False
    if not used_latexmk:
        rc = _manual_pipeline(run, build, root, engine, common, main_rel, stem, tools)

    tex_log_path = build / f"{stem}.log"
    tex_log = tex_log_path.read_text(encoding="utf-8", errors="replace") if tex_log_path.exists() else ""
    pdf = build / f"{stem}.pdf"
    issues = parse_log(tex_log, main_rel)
    has_errors = any(i["level"] == "error" for i in issues)
    # latexmk skips the rebuild (rc 0, PDF untouched) when nothing changed - that PDF is current.
    fresh = pdf.exists() and (pdf.stat().st_mtime >= started - 1 or rc == 0)
    ok = fresh and not has_errors

    return {
        "ok": ok,
        "pdf": pdf.exists(),
        "pdf_stale": pdf.exists() and not fresh,
        "issues": issues,
        "log": tex_log or "\n".join(log_chunks),
        "runner_log": "\n".join(log_chunks)[-30000:],
        "seconds": round(time.time() - started, 2),
        "returncode": rc,
    }




# --------------------------------------------------------------------------- #
# Shared utilities used by feature modules
# --------------------------------------------------------------------------- #
def iter_project_files(root: Path):
    """Yield (path, relative posix path) for every user file in a project."""
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel_parts = f.relative_to(root).parts
        if any(p in HIDDEN_NAMES for p in rel_parts) or f.name.endswith(".tmp"):
            continue
        yield f, f.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_pdf_path(root: Path, settings: dict | None = None) -> Path | None:
    settings = settings or load_settings(root)
    if not settings.get("main"):
        return None
    return root / BUILD_DIR_NAME / (Path(settings["main"]).stem + ".pdf")


def build_log_path(root: Path, settings: dict | None = None) -> Path | None:
    settings = settings or load_settings(root)
    if not settings.get("main"):
        return None
    return root / BUILD_DIR_NAME / (Path(settings["main"]).stem + ".log")


INPUT_RE = re.compile(r"\\(input|include|subfile)\s*\{([^}]+)\}")


def resolve_tex(root: Path, base_dir: Path, name: str) -> Path | None:
    name = name.strip()
    for cand in (name, name + ".tex"):
        for base in (root, base_dir):
            p = (base / cand).resolve()
            if p.is_file() and (root.resolve() in p.parents):
                return p
    return None


def flatten_tex(root: Path, rel: str, _depth: int = 0) -> list[tuple[str, int, str]]:
    """Return [(file_rel, line_no, line_text)] for the main file with \\input/\\include expanded."""
    out: list[tuple[str, int, str]] = []
    path = (root / rel)
    if _depth > 20 or not path.is_file():
        return out
    for i, line in enumerate(read_text(path).splitlines(), 1):
        code = strip_comment(line)
        m = INPUT_RE.search(code)
        if m:
            target = resolve_tex(root, path.parent, m.group(2))
            if target:
                before = code[:m.start()]
                if before.strip():
                    out.append((rel, i, before))
                out.extend(flatten_tex(root, target.relative_to(root.resolve()).as_posix(), _depth + 1))
                continue
        out.append((rel, i, line))
    return out


def strip_comment(line: str) -> str:
    """Remove a TeX % comment (ignoring escaped \\%)."""
    m = re.search(r"(?<!\\)%", line)
    return line[:m.start()] if m else line


def count_words(text: str) -> int:
    text = "\n".join(strip_comment(l) for l in text.splitlines())
    text = re.sub(r"\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\)", " ", text, flags=re.S)
    text = re.sub(r"\\begin\{(equation|align|gather|multline|eqnarray|tikzpicture|tabular|thebibliography|filecontents|verbatim|lstlisting)\*?\}.*?\\end\{\1\*?\}", " ", text, flags=re.S)
    text = re.sub(r"\\(?:begin|end|label|ref|eqref|autoref|cref|Cref|cite\w*|includegraphics|usepackage|input|include|bibliography\w*|documentclass|graphicspath)\*?(\[[^\]]*\])?\{[^}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    return len(re.findall(r"[A-Za-z0-9\u00C0-\u024F][A-Za-z0-9\u00C0-\u024F'\-]*", text))
