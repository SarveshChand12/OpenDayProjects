"""Writing statistics: per-section word counts, daily progress, goals and deadlines.

History is kept in the project settings file as {"word_log": {"YYYY-MM-DD": total_words}},
updated whenever stats are requested or the project is compiled.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from flask import Blueprint, jsonify, request

import core
from core import count_words, err, flatten_tex, load_settings, project_dir, save_settings

bp = Blueprint("stats", __name__)

SECTION_RE = re.compile(r"\\(part|chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}")
LEVEL = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3}


def section_counts(root: Path, main: str) -> tuple[list[dict], int]:
    lines = flatten_tex(root, main)
    sections = [{"title": "Front matter (title, abstract)", "level": 1, "path": main, "line": 1, "text": []}]
    in_doc = False
    for rel, ln, text in lines:
        if not in_doc:
            if "\\begin{document}" in text:
                in_doc = True
                text = text.split("\\begin{document}", 1)[1]
            else:
                continue
        if "\\end{document}" in text:
            sections[-1]["text"].append(text.split("\\end{document}", 1)[0])
            break
        m = SECTION_RE.search(core.strip_comment(text))
        if m:
            sections.append({"title": re.sub(r"\\[A-Za-z]+\s*|[{}]", "", m.group(2)).strip() or "(untitled)",
                             "level": LEVEL[m.group(1)], "path": rel, "line": ln,
                             "text": [text[m.end():]]})
            continue
        sections[-1]["text"].append(text)
    out = []
    for s in sections:
        words = count_words("\n".join(s.pop("text")))
        if s["title"].startswith("Front matter") and words == 0:
            continue
        out.append({**s, "words": words})
    return out, sum(s["words"] for s in out)


def _record(root: Path, total: int) -> dict:
    settings = load_settings(root)
    log = settings.get("word_log") or {}
    starts = settings.get("day_start") or {}
    today = dt.date.today().isoformat()
    if today not in starts:
        # First time we see the paper today: remember where the day started.
        prev_days = [k for k in sorted(log) if k < today]
        starts[today] = log[prev_days[-1]] if prev_days else log.get(today, total)
        settings["day_start"] = {k: v for k, v in starts.items() if k >= (dt.date.today() - dt.timedelta(days=40)).isoformat()}
        save_settings(root, settings)
    if log.get(today) != total:
        log[today] = total
        # keep a year of history
        for k in sorted(log)[:-366]:
            del log[k]
        settings["word_log"] = log
        save_settings(root, settings)
    return settings


def _compile_hook(root: Path, result: dict) -> None:
    settings = load_settings(root)
    if settings.get("main"):
        _, total = section_counts(root, settings["main"])
        _record(root, total)


core.COMPILE_HOOKS.append(_compile_hook)


@bp.get("/api/projects/<name>/stats")
def api_stats(name):
    root = project_dir(name)
    settings = load_settings(root)
    main = settings.get("main")
    if not main:
        return err("No main .tex file.")
    sections, total = section_counts(root, main)
    settings = _record(root, total)
    log = settings.get("word_log", {})
    today = dt.date.today()
    days = []
    prev_total = None
    # The total at the end of the most recent day before the window starts.
    window_start = today - dt.timedelta(days=29)
    earlier = [k for k in sorted(log) if k < window_start.isoformat()]
    if earlier:
        prev_total = log[earlier[-1]]
    for i in range(30):
        d = (window_start + dt.timedelta(days=i)).isoformat()
        if d in log:
            base = prev_total if prev_total is not None else (settings.get("day_start") or {}).get(d)
            written = log[d] - base if base is not None else 0
            prev_total = log[d]
            days.append({"date": d, "total": log[d], "written": written})
        else:
            days.append({"date": d, "total": prev_total, "written": 0})
    goals = settings.get("goals") or {}
    deadline_days = None
    if goals.get("deadline"):
        try:
            deadline_days = (dt.date.fromisoformat(goals["deadline"]) - today).days
        except ValueError:
            pass
    return jsonify({"total": total, "sections": sections, "days": days,
                    "today_written": days[-1]["written"], "goals": goals, "deadline_days": deadline_days})


@bp.put("/api/projects/<name>/stats/goals")
def api_goals(name):
    root = project_dir(name)
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings(root)
    goals = settings.get("goals") or {}
    for k in ("daily_goal", "target_words"):
        if k in data:
            v = data[k]
            goals[k] = int(v) if str(v).strip().isdigit() and int(v) > 0 else None
    if "deadline" in data:
        d = (data["deadline"] or "").strip()
        if d:
            try:
                dt.date.fromisoformat(d)
            except ValueError:
                return err("Deadline must be a date (YYYY-MM-DD).")
        goals["deadline"] = d or None
    if "deadline_label" in data:
        goals["deadline_label"] = (data["deadline_label"] or "").strip()[:60]
    settings["goals"] = goals
    save_settings(root, settings)
    return jsonify(goals)
