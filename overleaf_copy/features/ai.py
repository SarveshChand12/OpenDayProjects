"""Optional AI writing help using Claude (Anthropic API).

Nothing is sent anywhere unless you click an AI action, and then only the selected
text (plus the surrounding paragraph if you allow it) is sent.

Setup (one of):
  * set the ANTHROPIC_API_KEY environment variable before starting the app, or
  * paste a key in Tools -> AI assistant settings (stored in ~/.overleaf_copy/config.json,
    readable only by you), or
  * `ant auth login` (the SDK picks up that profile automatically).
Requires:  pip install -r requirements-ai.txt
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from flask import Blueprint, jsonify, request

from core import err

bp = Blueprint("ai", __name__)

CONFIG_DIR = Path(os.environ.get("OVERLEAF_COPY_CONFIG_DIR", Path.home() / ".overleaf_copy"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_INPUT_CHARS = 60_000

SYSTEM = (
    "You are an expert academic editor helping a researcher write a paper in LaTeX. "
    "When asked to revise text, return ONLY the revised LaTeX source for the given passage - "
    "no preamble, no explanations, no Markdown code fences. Preserve every LaTeX command, "
    "label, citation key, reference and maths expression exactly unless the instruction asks "
    "you to change them. Keep the author's meaning and technical claims; never invent results, "
    "numbers or citations. Match the existing spelling variety (British or American)."
)

ACTIONS = {
    "rephrase": ("Rephrase this passage to read more clearly and fluently, keeping the same meaning and roughly the same length.", True),
    "tighten": ("Tighten this passage: make it more concise (aim for about 30% shorter) without losing any technical content.", True),
    "academic": ("Rewrite this passage in a formal academic register suitable for a peer-reviewed venue.", True),
    "grammar": ("Fix grammar, spelling and punctuation only. Change as little as possible.", True),
    "abstract": ("This is a paper abstract. Improve it: state the problem, approach, key results and significance clearly, within 150-250 words.", True),
    "simplify": ("Rewrite this passage so a non-specialist reader can follow it, keeping it accurate.", True),
    "explain_error": ("Explain this LaTeX compiler error in plain English and give the most likely fix. Be brief: "
                      "a one-sentence diagnosis, then the corrected code if you can infer it from the context.", False),
    "custom": ("", True),
}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)   # 600: the key is a secret
    except OSError:
        pass


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The AI assistant needs the Anthropic SDK: pip install -r requirements-ai.txt") from e
    key = load_config().get("api_key")
    # Without a stored key the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`.
    return anthropic, (anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic())


@bp.get("/api/ai/status")
def api_status():
    cfg = load_config()
    try:
        import anthropic  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    return jsonify({
        "sdk_installed": sdk,
        "key_stored": bool(cfg.get("api_key")),
        "env_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "model": cfg.get("model") or DEFAULT_MODEL,
        "effort": cfg.get("effort") or "medium",
        "send_context": cfg.get("send_context", True),
    })


@bp.put("/api/ai/settings")
def api_settings():
    d = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    if "api_key" in d:
        key = (d["api_key"] or "").strip()
        if key:
            cfg["api_key"] = key
        else:
            cfg.pop("api_key", None)
    if d.get("model"):
        cfg["model"] = d["model"].strip()
    if d.get("effort") in ("low", "medium", "high", "xhigh", "max"):
        cfg["effort"] = d["effort"]
    if "send_context" in d:
        cfg["send_context"] = bool(d["send_context"])
    save_config(cfg)
    return api_status()


def build_prompt(action: str, text: str, instruction: str, context: str) -> str:
    base, is_edit = ACTIONS[action]
    task = instruction.strip() if action == "custom" else base
    parts = [f"<task>\n{task}\n</task>"]
    if context:
        parts.append("<surrounding_context>\n(For reference only - do not return it.)\n"
                     f"{context}\n</surrounding_context>")
    tag = "error_and_code" if action == "explain_error" else "passage"
    parts.append(f"<{tag}>\n{text}\n</{tag}>")
    if is_edit:
        parts.append("Return only the revised passage.")
    return "\n\n".join(parts)


@bp.post("/api/ai/run")
def api_run():
    d = request.get_json(force=True, silent=True) or {}
    action = d.get("action")
    if action not in ACTIONS:
        return err("Unknown AI action.")
    text = (d.get("text") or "").strip()
    if not text:
        return err("Select some text first.")
    if action == "custom" and not (d.get("instruction") or "").strip():
        return err("Type an instruction.")
    cfg = load_config()
    context = (d.get("context") or "") if cfg.get("send_context", True) else ""
    if len(text) + len(context) > MAX_INPUT_CHARS:
        return err(f"That's too much text for one request ({len(text) + len(context):,} characters). "
                   "Select a smaller passage (e.g. one section).")
    try:
        anthropic, client = _client()
    except RuntimeError as e:
        return err(str(e), 501)

    try:
        # Thinking is always on for this model; effort controls depth. "default" fallbacks
        # re-run a declined request on Anthropic's recommended fallback model server-side.
        response = client.beta.messages.create(
            model=cfg.get("model") or DEFAULT_MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": cfg.get("effort") or "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": build_prompt(action, text, d.get("instruction", ""), context)}],
        )
    except anthropic.AuthenticationError:
        return err("Your Anthropic API key was rejected. Update it in Tools → AI assistant settings.", 401)
    except anthropic.PermissionDeniedError as e:
        return err(f"The API key doesn't have access: {e.message}", 403)
    except anthropic.NotFoundError:
        return err("Model not found — check the model name in AI assistant settings.", 404)
    except anthropic.RateLimitError:
        return err("Rate limited by the Anthropic API — wait a minute and try again.", 429)
    except anthropic.BadRequestError as e:
        return err(f"The request was rejected: {e.message}", 400)
    except anthropic.APIStatusError as e:
        return err(f"Anthropic API error ({e.status_code}). Try again shortly.", 502)
    except anthropic.APIConnectionError:
        return err("Couldn't reach the Anthropic API — are you online?", 502)
    except Exception as e:  # e.g. no credentials configured at all
        msg = str(e)
        if "api_key" in msg.lower() or "auth" in msg.lower() or "credential" in msg.lower():
            return err("No Anthropic API key configured. Add one in Tools → AI assistant settings, "
                       "or set ANTHROPIC_API_KEY before starting the app.", 401)
        return err(f"AI request failed: {msg}", 500)

    if response.stop_reason == "refusal":
        return err("Claude declined this request.", 422)
    out = "".join(b.text for b in response.content if b.type == "text").strip()
    if out.startswith("```"):
        out = out.strip("`")
        out = (out.split("\n", 1)[1] if "\n" in out else out).strip()
    return jsonify({"result": out, "model": response.model, "truncated": response.stop_reason == "max_tokens",
                    "is_edit": ACTIONS[action][1]})
