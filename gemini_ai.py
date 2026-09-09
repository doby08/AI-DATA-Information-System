"""
Gemini AI API Client for Interview System
==========================================
Optional upgrade over the built-in rule-based "AI" in ai_service.py.

- Uses the Google Gemini REST API (generativelanguage.googleapis.com)
- Python standard library only (urllib) — no extra pip installs needed
- Get a FREE API key at: https://aistudio.google.com/apikey
- If no key is configured, offline, or the API errors out, callers
  transparently fall back to the rule-based logic in ai_service.py.
"""

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.6-flash"
SUGGESTED_MODELS = [
    "gemini-3.6-flash",     # default: stable, fast, balanced for everyday tasks
    "gemini-3.8-flash",     # newest Flash model
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",     # legacy (still works, previous generation)
]
REQUEST_TIMEOUT = 30      # seconds for normal AI calls
MAX_OUTPUT_TOKENS = 2048

# ---- Circuit breaker: after repeated failures, pause AI calls for a while ----
_consecutive_failures = 0
_disabled_until = 0.0
FAIL_LIMIT = 2
COOLDOWN_SECONDS = 120


class GeminiError(Exception):
    """Raised when a Gemini API call fails (network, auth, or bad response)."""


def _db_path() -> str:
    """Locate the SQLite database (data.db) next to this module / app.py."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def mask_key(key: str) -> str:
    """Mask an API key for safe display (e.g. 'AIzaSy••••••a9B2')."""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "••••••"
    return f"{key[:6]}••••••{key[-4:]}"


def is_available() -> bool:
    """False while the circuit breaker is cooling down after failures."""
    return time.time() >= _disabled_until


def _note_success():
    global _consecutive_failures
    _consecutive_failures = 0


def _note_failure(reason: str):
    global _consecutive_failures, _disabled_until
    _consecutive_failures += 1
    if _consecutive_failures >= FAIL_LIMIT:
        _disabled_until = time.time() + COOLDOWN_SECONDS
        logger.warning(
            "Gemini disabled for %d seconds after %d consecutive failures (last: %s)",
            COOLDOWN_SECONDS, _consecutive_failures, reason,
        )


def get_config(user_id=None) -> dict:
    """
    Read the Gemini configuration.

    Priority: system-wide settings saved by an administrator (user_id = 0 in
    the settings table) -> the user's own legacy per-user settings ->
    GEMINI_API_KEY environment variable.

    One person (e.g. the Dean) saves the API key once in Settings; every
    account then receives AI-generated results — no per-account keys needed.

    Returns a dict: {enabled: bool, api_key: str, model: str, source: str}.
    'enabled' is False when no key is saved, when the feature was switched
    off, or while the circuit breaker is cooling down.
    """
    cfg = {"enabled": False, "api_key": "", "model": DEFAULT_MODEL, "source": ""}

    def _read_settings(uid):
        rows = {}
        try:
            conn = sqlite3.connect(_db_path())
            rows = {k: (v or "") for k, v in conn.execute(
                "SELECT setting_key, setting_value FROM settings "
                "WHERE user_id = ? AND setting_key IN ('ai_api_enabled', 'ai_api_key', 'ai_model')",
                (uid,),
            ).fetchall()}
            conn.close()
        except sqlite3.Error as exc:
            logger.warning(f"Gemini config: could not read settings: {exc}")
        return rows

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    source = "env" if api_key else ""

    rows = {}
    if user_id is not None:
        # 1) System-wide admin configuration (saved under user_id = 0)
        global_rows = _read_settings(0)
        if (global_rows.get("ai_api_key") or "").strip():
            rows = global_rows
            source = "global"
        else:
            # 2) Legacy per-user settings (kept for backward compatibility)
            rows = _read_settings(user_id)

    saved_key = (rows.get("ai_api_key") or "").strip()
    if saved_key:
        api_key = saved_key
        if source != "global":
            source = "settings"

    cfg["api_key"] = api_key
    cfg["model"] = (rows.get("ai_model") or "").strip() or DEFAULT_MODEL

    explicit_off = (rows.get("ai_api_enabled") == "0")
    cfg["enabled"] = bool(api_key) and not explicit_off
    cfg["source"] = source if cfg["enabled"] else ""

    if cfg["enabled"] and not is_available():
        cfg["enabled"] = False
    return cfg


def call_gemini(prompt: str, api_key: str = None, model: str = None,
                timeout: int = REQUEST_TIMEOUT,
                max_output_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """
    Send a prompt to Gemini and return the response text.

    Raises GeminiError on any failure so callers can fall back gracefully.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise GeminiError("No Gemini API key configured.")

    model = (model or DEFAULT_MODEL).strip().strip("/")
    url = f"{API_BASE}/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": max_output_tokens,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            detail = str(error_body.get("error", {}).get("message", ""))[:300]
        except Exception:
            pass
        _note_failure(f"HTTP {exc.code}")
        if exc.code in (401, 403):
            raise GeminiError(f"API key was rejected (HTTP {exc.code}). Check your key. {detail}")
        if exc.code == 429:
            raise GeminiError("Rate limit reached (HTTP 429). Try again in a minute.")
        raise GeminiError(f"Gemini API error (HTTP {exc.code}): {detail or exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _note_failure("network")
        raise GeminiError(f"Cannot reach Gemini (network error): {exc}")
    except json.JSONDecodeError as exc:
        _note_failure("bad json")
        raise GeminiError(f"Gemini returned an invalid response: {exc}")

    text = extract_text(body)
    if not text:
        _note_failure("empty")
        raise GeminiError("Gemini returned an empty response (possibly blocked by safety filters).")
    _note_success()
    return text


def extract_text(body: dict) -> str:
    """Pull the joined text out of a generateContent response body."""
    try:
        candidates = body.get("candidates") or []
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text", "")) for part in parts)
        return text.strip()
    except Exception:
        return ""


def parse_json_block(text: str):
    """
    Robustly extract the first JSON array/object from model output.
    Handles ```json fences and prose around the JSON. Raises ValueError.
    """
    if not text or not text.strip():
        raise ValueError("Empty text — no JSON to parse.")

    cleaned = text.strip()
    if "```" in cleaned:
        for part in cleaned.split("```"):
            candidate = part.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith(("[", "{")):
                cleaned = candidate
                break

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Scan for the outermost matching bracket in the text.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth = 0
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(cleaned[start:index + 1])
    raise ValueError("No valid JSON found in the model response.")


def test_connection(api_key: str = None, model: str = None):
    """
    Quick connectivity/validity test. Returns (ok: bool, message: str).
    """
    api_key = (api_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return False, "No API key provided. Get a free key at aistudio.google.com/apikey"
    model = (model or DEFAULT_MODEL).strip()
    try:
        reply = call_gemini("Reply with exactly: OK", api_key=api_key,
                            model=model, timeout=15, max_output_tokens=512)
        return True, f"Connected to {model} successfully! Model replied: {reply[:80]}"
    except GeminiError as exc:
        return False, str(exc)
