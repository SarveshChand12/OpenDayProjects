"""References: parse .bib files, lint citations, and fetch BibTeX by DOI or arXiv ID."""

from __future__ import annotations

import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Blueprint, jsonify, request

from core import err, iter_project_files, project_dir, read_text, safe_path, strip_comment

bp = Blueprint("references", __name__)

DOI_BASE = os.environ.get("OVERLEAF_COPY_DOI_BASE", "https://doi.org")
ARXIV_API = os.environ.get("OVERLEAF_COPY_ARXIV_API", "https://export.arxiv.org/api/query")

# \cite, \citep, \citet*, \parencite, \textcite, \autocite, \footcite, \citeauthor, \nocite ...
CITE_RE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]*)\}")
REQUIRED = {
    "article": [("author",), ("title",), ("journal", "journaltitle"), ("year", "date")],
    "inproceedings": [("author",), ("title",), ("booktitle",), ("year", "date")],
    "conference": [("author",), ("title",), ("booktitle",), ("year", "date")],
    "book": [("author", "editor"), ("title",), ("publisher",), ("year", "date")],
    "incollection": [("author",), ("title",), ("booktitle",), ("publisher",), ("year", "date")],
    "phdthesis": [("author",), ("title",), ("school", "institution"), ("year", "date")],
    "mastersthesis": [("author",), ("title",), ("school", "institution"), ("year", "date")],
    "techreport": [("author",), ("title",), ("institution",), ("year", "date")],
    "misc": [("title",)],
    "online": [("title",), ("url",)],
}


# --------------------------------------------------------------------------- #
# BibTeX parsing
# --------------------------------------------------------------------------- #
def _balanced(text: str, i: int, open_c: str, close_c: str) -> int:
    """Return index just after the delimiter closing the one at text[i]."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def parse_bib(text: str) -> list[dict]:
    entries = []
    for m in re.finditer(r"@\s*([A-Za-z]+)\s*([{(])", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        open_c = m.group(2)
        close_c = "}" if open_c == "{" else ")"
        end = _balanced(text, m.end() - 1, open_c, close_c)
        body = text[m.end():end - 1]
        key, _, rest = body.partition(",")
        fields = {}
        i = 0
        while i < len(rest):
            fm = re.compile(r"\s*([A-Za-z][\w\-:.]*)\s*=\s*").match(rest, i)
            if not fm:
                i += 1
                continue
            name = fm.group(1).lower()
            j = fm.end()
            parts = []
            while j < len(rest):
                c = rest[j]
                if c == "{":
                    k = _balanced(rest, j, "{", "}")
                    parts.append(rest[j + 1:k - 1])
                    j = k
                elif c == '"':
                    k = j + 1
                    depth = 0
                    while k < len(rest) and not (rest[k] == '"' and depth == 0):
                        depth += rest[k] == "{"
                        depth -= rest[k] == "}"
                        k += 1
                    parts.append(rest[j + 1:k])
                    j = k + 1
                else:
                    vm = re.compile(r"[^,#\s}]+").match(rest, j)
                    if vm:
                        parts.append(vm.group())
                        j = vm.end()
                    else:
                        j += 1
                sm = re.compile(r"\s*#\s*").match(rest, j)
                if sm:
                    j = sm.end()
                    continue
                break
            fields[name] = " ".join(" ".join(parts).split())
            cm = re.compile(r"\s*,?").match(rest, j)
            i = cm.end() if cm else j + 1
        entries.append({"type": etype, "key": key.strip(), "fields": fields,
                        "line": text.count("\n", 0, m.start()) + 1})
    return entries


def all_entries(root: Path) -> list[dict]:
    out = []
    for path, rel in iter_project_files(root):
        if path.suffix.lower() == ".bib":
            for e in parse_bib(read_text(path)):
                e["path"] = rel
                out.append(e)
    return out


ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304",
           ".": "\u0307", "c": "\u0327", "u": "\u0306", "v": "\u030c", "H": "\u030b", "k": "\u0328", "r": "\u030a"}
SPECIAL = {"ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "aa": "å", "AA": "Å", "oe": "œ", "OE": "Œ",
           "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ", "&": "&", "%": "%", "_": "_", "$": "$", "#": "#"}


def _clean(s: str) -> str:
    """Turn BibTeX/LaTeX markup into readable text: accents, logos, maths, ~, braces."""
    s = s or ""
    s = re.sub(r"\\(LaTeX|TeX|BibTeX|LaTeXe)\b\s*", r"\1", s)              # keep logos as words
    # Accents: {\"u}, \"{u}, \"u, \c{c}, \v{s} ...
    s = re.sub(r"\\([\"'`^~=.])\s*\{?\s*(\\?[A-Za-z])\s*\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2).lstrip("\\") + ACCENTS[m.group(1)]), s)
    s = re.sub(r"\\([cuvHkr])\s*\{\s*(\\?[A-Za-z])\s*\}",
               lambda m: unicodedata.normalize("NFC", m.group(2).lstrip("\\") + ACCENTS[m.group(1)]), s)
    s = re.sub(r"\\(ss|ae|AE|aa|AA|oe|OE|o|O|l|L)\b\s*|\\([&%_$#])", lambda m: SPECIAL[m.group(1) or m.group(2)], s)
    # Maths: keep the content readable ($\alpha$-stable -> alpha-stable)
    s = re.sub(r"\$([^$]*)\$", lambda m: re.sub(r"\\([A-Za-z]+)", r"\1", m.group(1)), s)
    s = s.replace("~", " ").replace("--", "–")
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)
    return re.sub(r"[{}]", "", s).strip()


def cited_keys(root: Path) -> tuple[dict[str, list], bool]:
    cites: dict[str, list] = {}
    nocite_all = False
    for path, rel in iter_project_files(root):
        if path.suffix.lower() not in (".tex", ".ltx"):
            continue
        # Whole file at once so \cite{a,<newline> b} spanning lines is understood.
        text = "\n".join(strip_comment(l) for l in read_text(path).splitlines())
        for m in CITE_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            for k in m.group(1).split(","):
                k = k.strip()
                if k == "*":
                    nocite_all = True
                elif k:
                    cites.setdefault(k, []).append({"path": rel, "line": line})
    return cites, nocite_all


# --------------------------------------------------------------------------- #
# Routes: list + lint
# --------------------------------------------------------------------------- #
@bp.get("/api/projects/<name>/bib")
def api_bib(name):
    root = project_dir(name)
    cites, _ = cited_keys(root)
    out = []
    for e in all_entries(root):
        f = e["fields"]
        authors = [a.strip() for a in re.split(r"\s+and\s+", _clean(f.get("author") or f.get("editor", ""))) if a.strip()]
        short = ""
        if authors:
            last = lambda a: a.split(",")[0].strip() if "," in a else a.split()[-1]
            short = last(authors[0]) + (" et al." if len(authors) > 2 else f" & {last(authors[1])}" if len(authors) == 2 else "")
        out.append({"key": e["key"], "type": e["type"], "title": _clean(f.get("title", "")),
                    "authors": short, "year": _clean(f.get("year") or f.get("date", ""))[:4],
                    "venue": _clean(f.get("journal") or f.get("booktitle") or f.get("publisher") or f.get("eprinttype", "")),
                    "doi": f.get("doi", ""), "path": e["path"], "line": e["line"],
                    "cited": len(cites.get(e["key"], []))})
    bibs = sorted({p for p in (e["path"] for e in out)} |
                  {rel for path, rel in iter_project_files(root) if path.suffix.lower() == ".bib"})
    return jsonify({"entries": out, "bib_files": bibs, "default_bib": _main_bib(root, bibs)})


def _main_bib(root: Path, bibs: list[str]) -> str | None:
    """The .bib file the document actually uses (\\bibliography{x} or \\addbibresource{x.bib})."""
    from core import load_settings
    main = load_settings(root).get("main")
    if not main:
        return bibs[0] if bibs else None
    text = "\n".join(strip_comment(l) for l in read_text(root / main).splitlines()) if (root / main).exists() else ""
    base = Path(main).parent
    for m in re.finditer(r"\\(?:bibliography|addbibresource)(?:\[[^\]]*\])?\{([^}]*)\}", text):
        for name in m.group(1).split(","):
            name = name.strip()
            cand = (base / (name if name.endswith(".bib") else name + ".bib")).as_posix()
            if cand in bibs:
                return cand
            if name.endswith(".bib") or not name:
                continue
            return cand                     # not created yet - the "Add" dialog will create it
    return bibs[0] if bibs else None


def _norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", _clean(t)).lower()
    return re.sub(r"[^a-z0-9]+", "", t)


@bp.get("/api/projects/<name>/bib/lint")
def api_lint(name):
    root = project_dir(name)
    entries = all_entries(root)
    cites, nocite_all = cited_keys(root)
    by_key: dict[str, list] = {}
    for e in entries:
        by_key.setdefault(e["key"], []).append(e)
    problems = []

    for key, locs in sorted(cites.items()):
        if key not in by_key:
            problems.append({"level": "error", "kind": "missing", "key": key,
                             "message": f"'{key}' is cited but not defined in any .bib file",
                             "path": locs[0]["path"], "line": locs[0]["line"], "count": len(locs)})
    for key, es in by_key.items():
        if len(es) > 1:
            problems.append({"level": "error", "kind": "duplicate-key", "key": key,
                             "message": f"'{key}' is defined {len(es)} times ({', '.join(e['path'] + ':' + str(e['line']) for e in es)})",
                             "path": es[1]["path"], "line": es[1]["line"]})
    if not nocite_all:
        for e in entries:
            if e["key"] not in cites:
                problems.append({"level": "info", "kind": "unused", "key": e["key"],
                                 "message": f"'{e['key']}' is never cited", "path": e["path"], "line": e["line"]})
    seen_titles: dict[str, dict] = {}
    seen_doi: dict[str, dict] = {}
    for e in entries:
        t = _norm_title(e["fields"].get("title", ""))
        doi = e["fields"].get("doi", "").lower().strip()
        for store, val, what in ((seen_titles, t, "title"), (seen_doi, doi, "DOI")):
            if not val:
                continue
            if val in store and store[val]["key"] != e["key"]:
                problems.append({"level": "warning", "kind": "duplicate-entry", "key": e["key"],
                                 "message": f"'{e['key']}' looks like a duplicate of '{store[val]['key']}' (same {what})",
                                 "path": e["path"], "line": e["line"]})
            store.setdefault(val, e)
        for options in REQUIRED.get(e["type"], []):
            if not any(e["fields"].get(o) for o in options):
                problems.append({"level": "warning", "kind": "missing-field", "key": e["key"],
                                 "message": f"'{e['key']}' (@{e['type']}) has no {' / '.join(options)}",
                                 "path": e["path"], "line": e["line"]})
        title = e["fields"].get("title", "")
        if re.search(r"\b[A-Z]{2,}\b", re.sub(r"\{[^{}]*\}", "", title)):
            problems.append({"level": "info", "kind": "case", "key": e["key"],
                             "message": f"'{e['key']}': acronyms in the title may be lower-cased by the style — wrap them in braces, e.g. {{NASA}}",
                             "path": e["path"], "line": e["line"]})
    order = {"error": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda p: (order[p["level"]], p["kind"], p["key"]))
    return jsonify({"problems": problems, "entries": len(entries), "cited": len(cites)})


# --------------------------------------------------------------------------- #
# Fetch BibTeX by DOI / arXiv
# --------------------------------------------------------------------------- #
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv[:\s/]*|arxiv\.org/(?:abs|pdf)/)?((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?", re.I)


def identify(raw: str) -> tuple[str, str] | None:
    raw = raw.strip()
    if "arxiv" in raw.lower() or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", raw):
        m = ARXIV_RE.search(raw)
        if m:
            return "arxiv", m.group(1)
    m = DOI_RE.search(urllib.parse.unquote(raw))
    if m:
        return "doi", m.group(1).rstrip(".,;")
    m = ARXIV_RE.fullmatch(raw)
    if m:
        return "arxiv", m.group(1)
    return None


def _http(url: str, accept: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "overleaf-copy/1.0 (local LaTeX editor)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def make_key(authors: list[str], year: str, title: str) -> str:
    last = ""
    if authors:
        a = authors[0]
        last = a.split(",")[0] if "," in a else a.split()[-1]
    word = next((w for w in re.findall(r"[A-Za-z]+", _clean(title))
                 if w.lower() not in {"a", "an", "the", "on", "of", "for", "in", "and", "to", "with", "towards"}), "")
    key = f"{last}{year}{word}".lower()
    key = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", key) or "ref"


def format_entry(etype: str, key: str, fields: dict) -> str:
    order = ["author", "editor", "title", "journal", "booktitle", "publisher", "volume", "number", "pages",
             "year", "month", "doi", "eprint", "archiveprefix", "primaryclass", "url", "note"]
    keys = sorted(fields, key=lambda k: (order.index(k) if k in order else 99, k))
    width = max((len(k) for k in keys), default=0)
    months = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}

    def value(k: str) -> str:
        v = fields[k]
        if k == "month" and v.lower()[:3] in months:
            return v.lower()[:3]                      # BibTeX month macro, unbraced
        if k == "pages":
            v = re.sub(r"\s*[\u2013\u2014-]+\s*", "--", v)   # en dash -> -- (pdfLaTeX-safe)
        return "{" + v + "}"
    body = ",\n".join(f"  {k.ljust(width)} = {value(k)}" for k in keys if fields[k])
    return f"@{etype}{{{key},\n{body}\n}}\n"


def fetch_doi(doi: str) -> str:
    text = _http(f"{DOI_BASE}/{urllib.parse.quote(doi, safe='/')}", "application/x-bibtex; charset=utf-8")
    entries = parse_bib(text)
    if not entries:
        raise ValueError("The DOI service didn't return BibTeX.")
    e = entries[0]
    f = e["fields"]
    authors = [a.strip() for a in re.split(r"\s+and\s+", f.get("author", "")) if a.strip()]
    f.setdefault("doi", doi)
    f.pop("url", None) if f.get("url", "").startswith(("http://dx.doi.org", "https://doi.org", "http://doi.org")) else None
    return format_entry(e["type"], make_key(authors, f.get("year", ""), f.get("title", "")), f)


def fetch_arxiv(aid: str) -> str:
    xml = _http(f"{ARXIV_API}?id_list={urllib.parse.quote(aid)}", "application/atom+xml")
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    entry = root.find("a:entry", ns)
    if entry is None or (entry.findtext("a:title", "", ns) or "").strip().lower() == "error":
        raise ValueError(f"arXiv has no paper with ID {aid}.")
    title = " ".join((entry.findtext("a:title", "", ns) or "").split())
    authors = [" ".join((a.findtext("a:name", "", ns) or "").split()) for a in entry.findall("a:author", ns)]
    year = (entry.findtext("a:published", "", ns) or "")[:4]
    prim = entry.find("x:primary_category", ns)
    fields = {
        "author": " and ".join(authors), "title": title, "year": year,
        "eprint": aid, "archiveprefix": "arXiv",
        "primaryclass": prim.get("term") if prim is not None else "",
        "url": f"https://arxiv.org/abs/{aid}",
    }
    doi = entry.findtext("x:doi", "", ns)
    if doi:
        fields["doi"] = doi.strip()
    jref = entry.findtext("x:journal_ref", "", ns)
    if jref:
        fields["note"] = " ".join(jref.split())
    return format_entry("misc", make_key(authors, year, title), fields)


@bp.post("/api/projects/<name>/bib/fetch")
def api_fetch(name):
    project_dir(name)
    raw = ((request.get_json(force=True, silent=True) or {}).get("id") or "").strip()
    ident = identify(raw)
    if not ident:
        return err("That doesn't look like a DOI (10.xxxx/…) or an arXiv ID (e.g. 2101.00001).")
    kind, value = ident
    try:
        bib = fetch_doi(value) if kind == "doi" else fetch_arxiv(value)
    except urllib.error.HTTPError as e:
        return err(f"{'DOI' if kind == 'doi' else 'arXiv'} lookup failed ({e.code}). Check the ID.", 502)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return err(f"Couldn't reach {'doi.org' if kind == 'doi' else 'arXiv'} — are you online? ({getattr(e, 'reason', e)})", 502)
    except (ValueError, ET.ParseError) as e:
        return err(str(e), 502)
    return jsonify({"kind": kind, "id": value, "bibtex": bib})


@bp.post("/api/projects/<name>/bib/add")
def api_add(name):
    """Append BibTeX entries to a .bib file, renaming keys that already exist."""
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    rel = data.get("path") or "references.bib"
    if not rel.endswith(".bib"):
        return err("Target must be a .bib file.")
    target = safe_path(root, rel)
    new = parse_bib(data.get("bibtex", ""))
    if not new:
        return err("No BibTeX entries found in the text.")
    existing = {e["key"] for e in all_entries(root)}
    text = data["bibtex"]
    added = []
    for e in new:
        key = e["key"]
        if key in existing:
            suffix = "abcdefghijklmnopqrstuvwxyz"
            nk = next((key + s for s in suffix if key + s not in existing), key + "_new")
            text = re.sub(r"(@\s*" + re.escape(e["type"]) + r"\s*[{(]\s*)" + re.escape(key) + r"\s*,",
                          lambda m, nk=nk: m.group(1) + nk + ",", text, count=1, flags=re.I)
            key = nk
        existing.add(key)
        added.append(key)
    prev = target.read_text(encoding="utf-8") if target.exists() else ""
    sep = "" if not prev or prev.endswith("\n\n") else ("\n" if prev.endswith("\n") else "\n\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(prev + sep + text.strip() + "\n", encoding="utf-8")
    return jsonify({"added": added, "path": rel})
