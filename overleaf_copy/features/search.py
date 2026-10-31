"""Project-wide search and replace across all text files."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from core import err, is_text_file, iter_project_files, project_dir, read_text

bp = Blueprint("search", __name__)
error_type = re.error
MAX_RESULTS = 2000


def _pattern(data) -> re.Pattern:
    q = data.get("q") or ""
    if not q:
        raise ValueError("Enter something to search for.")
    is_regex = str(data.get("regex", "0")) in ("1", "true", "True")
    case = str(data.get("case", "0")) in ("1", "true", "True")
    whole = str(data.get("whole", "0")) in ("1", "true", "True")
    pat = q if is_regex else re.escape(q)
    if whole:
        pat = rf"(?<!\w){pat}(?!\w)"
    try:
        return re.compile(pat, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"Invalid regular expression: {e}") from e


def _files(root, only):
    for path, rel in iter_project_files(root):
        if only and rel not in only:
            continue
        if is_text_file(path) and path.stat().st_size < 5_000_000:
            yield path, rel


@bp.get("/api/projects/<name>/search")
def api_search(name):
    root = project_dir(name)
    try:
        rx = _pattern(request.args)
    except ValueError as e:
        return err(str(e))
    results, total, truncated = [], 0, False
    for path, rel in _files(root, None):
        for i, line in enumerate(read_text(path).splitlines(), 1):
            for m in rx.finditer(line):
                total += 1
                if len(results) >= MAX_RESULTS:
                    truncated = True
                    break
                results.append({"path": rel, "line": i, "col": m.start(), "len": m.end() - m.start(),
                                "text": line[:400]})
                if m.end() == m.start():
                    break
    return jsonify({"results": results, "total": total, "truncated": truncated})


@bp.post("/api/projects/<name>/replace")
def api_replace(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    try:
        rx = _pattern(data)
    except ValueError as e:
        return err(str(e))
    repl = data.get("replacement", "")
    is_regex = str(data.get("regex", "0")) in ("1", "true", "True")
    only = set(data.get("paths") or []) or None

    # The replacement is LaTeX, so backslashes are literal (\textbf stays \textbf). In regex mode
    # \1..\99 and \g<name> insert captured groups; nothing else is special.
    group_ref = re.compile(r"\\(\d{1,2})|\\g<(\w+)>")

    def expand(m: re.Match) -> str:
        if not is_regex:
            return repl

        def sub(g: re.Match) -> str:
            ref = g.group(1) or g.group(2)
            try:
                val = m.group(int(ref) if ref.isdigit() else ref)
            except (IndexError, error_type):
                raise ValueError(f"The replacement refers to group {ref}, which the pattern doesn't have.")
            return val or ""
        return group_ref.sub(sub, repl)

    changed = {}
    try:
        for path, rel in _files(root, only):
            text = read_text(path)
            # Match line by line, exactly like the search, so the counts agree.
            out, n = [], 0
            for line in text.splitlines(keepends=True):
                body = line.rstrip("\r\n")
                new, k = rx.subn(expand, body)
                n += k
                out.append(new + line[len(body):])
            if n:
                path.write_text("".join(out), encoding="utf-8", newline="")
                changed[rel] = n
    except ValueError as e:
        return err(str(e))
    return jsonify({"changed": changed, "total": sum(changed.values())})
