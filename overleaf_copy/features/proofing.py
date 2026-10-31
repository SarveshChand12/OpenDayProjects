"""Proofing: list spell-check dictionaries and proxy grammar checks to LanguageTool.

Spell checking runs entirely in the browser (Typo.js + Hunspell dictionaries in
static/vendor/dictionaries/<lang>/<lang>.aff|.dic - drop in more to add languages).

Grammar checking uses LanguageTool. By default it talks to a LanguageTool server on
your own machine (http://localhost:8081, see README), so your text never leaves it.
You can opt in to the public api.languagetool.org service in Editor settings.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request

from core import STATIC_DIR, err

bp = Blueprint("proofing", __name__)

DICT_DIR = STATIC_DIR / "vendor" / "dictionaries"
DEFAULT_LT = os.environ.get("OVERLEAF_COPY_LANGUAGETOOL", "http://localhost:8081")
PUBLIC_LT = "https://api.languagetool.org"


@bp.get("/api/dictionaries")
def api_dictionaries():
    langs = []
    if DICT_DIR.is_dir():
        for d in sorted(DICT_DIR.iterdir()):
            if (d / f"{d.name}.aff").exists() and (d / f"{d.name}.dic").exists():
                langs.append(d.name)
    return jsonify({"languages": langs})


@bp.post("/api/grammar")
def api_grammar():
    data = request.get_json(force=True, silent=True) or {}
    annotation = data.get("annotation")
    if not isinstance(annotation, list) or not annotation:
        return err("Nothing to check.")
    server = (data.get("server") or DEFAULT_LT).rstrip("/")
    if server == "public":
        server = PUBLIC_LT
    if not server.startswith(("http://", "https://")):
        return err("Invalid LanguageTool server URL.")
    lang = data.get("language") or "en-US"
    form = {
        "data": json.dumps({"annotation": annotation}),
        "language": lang,
        # Spelling is handled by the editor's spell checker; whitespace rules misfire on LaTeX.
        "disabledRules": "WHITESPACE_RULE,MORFOLOGIK_RULE_EN_US,MORFOLOGIK_RULE_EN_GB,"
                         "MORFOLOGIK_RULE_EN_AU,MORFOLOGIK_RULE_EN_CA,COMMA_PARENTHESIS_WHITESPACE",
    }
    if data.get("picky"):
        form["level"] = "picky"
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(f"{server}/v2/check", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json",
                                          "User-Agent": "overleaf-copy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return err(f"LanguageTool returned {e.code}: {detail}", 502)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        hint = ("Start a local LanguageTool server (see README) or choose the public service "
                "in Tools → Editor settings." if server == DEFAULT_LT else "")
        return err(f"Couldn't reach LanguageTool at {server}: {getattr(e, 'reason', e)}. {hint}", 502)
    matches = [{
        "offset": m["offset"], "length": m["length"], "message": m.get("message", ""),
        "short": m.get("shortMessage", ""),
        "replacements": [r["value"] for r in m.get("replacements", [])[:6]],
        "rule": m.get("rule", {}).get("id", ""), "category": m.get("rule", {}).get("category", {}).get("name", ""),
    } for m in result.get("matches", [])]
    return jsonify({"matches": matches, "server": server})
