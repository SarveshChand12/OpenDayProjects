"""Notes to self: comments attached to lines of your source files.

Stored in <project>/.overleaf_notes.json, never in the .tex files, so they can't
end up in the PDF or in an arXiv upload.
"""

from __future__ import annotations

import json
import time
import uuid

from flask import Blueprint, jsonify, request

from core import NOTES_FILE, err, project_dir

bp = Blueprint("notes", __name__)


def _load(root):
    f = root / NOTES_FILE
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(root, notes):
    (root / NOTES_FILE).write_text(json.dumps(notes, indent=1), encoding="utf-8")


@bp.get("/api/projects/<name>/notes")
def api_list(name):
    return jsonify({"notes": _load(project_dir(name))})


@bp.post("/api/projects/<name>/notes")
def api_add(name):
    root = project_dir(name)
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("path") or not (d.get("text") or "").strip():
        return err("A file and some text are required.")
    note = {"id": uuid.uuid4().hex[:10], "path": d["path"], "line": int(d.get("line") or 1),
            "anchor": (d.get("anchor") or "")[:300], "text": d["text"].strip()[:5000],
            "created": time.time(), "updated": time.time(), "resolved": False}
    notes = _load(root)
    notes.append(note)
    _save(root, notes)
    return jsonify(note)


@bp.patch("/api/projects/<name>/notes/<nid>")
def api_update(name, nid):
    root = project_dir(name)
    d = request.get_json(force=True, silent=True) or {}
    notes = _load(root)
    for n in notes:
        if n["id"] == nid:
            for k in ("text", "resolved", "line", "anchor", "path"):
                if k in d:
                    n[k] = d[k]
            n["updated"] = time.time()
            _save(root, notes)
            return jsonify(n)
    return err("Note not found.", 404)


@bp.post("/api/projects/<name>/notes/positions")
def api_positions(name):
    """Bulk-update line numbers/anchors after edits moved lines around."""
    root = project_dir(name)
    updates = {u["id"]: u for u in (request.get_json(force=True, silent=True) or {}).get("updates", [])}
    notes = _load(root)
    for n in notes:
        if n["id"] in updates:
            n["line"] = int(updates[n["id"]].get("line", n["line"]))
            n["anchor"] = updates[n["id"]].get("anchor", n["anchor"])[:300]
            if "path" in updates[n["id"]]:
                n["path"] = updates[n["id"]]["path"]
    _save(root, notes)
    return jsonify({"ok": True})


def rename_paths(root, src: str, dst: str) -> None:
    """Keep notes attached when a file or folder is renamed/moved."""
    notes = _load(root)
    changed = False
    for n in notes:
        if n["path"] == src or n["path"].startswith(src + "/"):
            n["path"] = dst + n["path"][len(src):]
            changed = True
    if changed:
        _save(root, notes)


@bp.delete("/api/projects/<name>/notes/<nid>")
def api_delete(name, nid):
    root = project_dir(name)
    notes = [n for n in _load(root) if n["id"] != nid]
    _save(root, notes)
    return jsonify({"deleted": nid})
