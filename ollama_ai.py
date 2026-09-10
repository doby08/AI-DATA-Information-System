"""

Ollama AI Client for Interview System
======================================
Optional **local** alternative to the Google Gemini API client (gemini_ai.py).

- Uses the Ollama REST API (http://localhost:11434/api/generate)
- Python standard library only (urllib) -- no extra pip installs needed
  * (the `ollama` PyPI package is *not* required; the REST API is called directly)
- Start the server:   ollama serve
- Pull a model:     ollama pull qwen2.5:1.5b
- If Ollama is not running, unreachable, or errors out, callers
  transparently fall back to the rule-based logic in ai_service.py.
  Nothing ever breaks.
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

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:1.5b"
SUGGESTED_MODELS = [
    "qwen2.5:1.5b",
    "qwen2.5:3b",
    "qwen2.5:7b",
    "llama3.2:3b",
    "llama3.1:8b",
    "phi3.5:3.5b",
    "gemma2:9b",
    "mistral-small3",
]

REQUEST_TIMEOUT = 120
MAX_OUTPUT_TOKENS = 4096

_consecutive_failures = 0
_disabled_until = 0.0
FAIL_LIMIT = 5
COOLDOWN_SECONDS = 30


def reset_circuit_breaker():
    """Reset the circuit breaker (e.g., when settings are updated)."""
    global _consecutive_failures, _disabled_until
    _consecutive_failures = 0
    _disabled_until = 0.0


class OllamaError(Exception):
    """Raised when an Ollama API call fails."""


def _db_path():
    """Locate the SQLite database (data.db) next to this module."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def is_available():
    """False while the circuit breaker is cooling down after failures."""
    return time.time() >= _disabled_until


def _note_success():
    global _consecutive_failures
    _consecutive_failures = 0


def _note_failure(reason):
    global _consecutive_failures, _disabled_until
    _consecutive_failures += 1
    if _consecutive_failures >= FAIL_LIMIT:
        _disabled_until = time.time() + COOLDOWN_SECONDS
        logger.warning(
            "Ollama disabled for %d seconds after %d consecutive failures (last: %s)",
            COOLDOWN_SECONDS, _consecutive_failures, reason,
        )



def get_config(user_id=None) -> dict:
    """
    Read the Ollama configuration.

    Priority: system-wide settings saved by an administrator (user_id = 0 in
    the settings table) -> environment variables (OLLAMA_URL / OLLAMA_MODEL).

    Returns a dict:
        {enabled: bool, url: str, model: str, has_url: bool, source: str}
    'enabled' is True only when the admin has explicitly enabled it in
    settings (or env vars are set), not explicitly turned off, and the
    circuit breaker currently allows calls.
    """
    cfg = {
        "enabled": False,
        "url": DEFAULT_URL,
        "model": DEFAULT_MODEL,
        "has_url": False,
        "source": "",
    }

    def _read_settings(uid):
        rows = {}
        try:
            conn = sqlite3.connect(_db_path())
            rows = {k: (v or "") for k, v in conn.execute(
                "SELECT setting_key, setting_value FROM settings "
                "WHERE user_id = ? "
                "AND setting_key IN ('ollama_enabled', 'ollama_url', 'ollama_model')",
                (uid,),
            ).fetchall()}
            conn.close()
        except sqlite3.Error as exc:
            logger.warning("Ollama config: could not read settings: %s", exc)
        return rows

    env_url = os.environ.get("OLLAMA_URL", "").strip()
    env_model = os.environ.get("OLLAMA_MODEL", "").strip()
    url = env_url or DEFAULT_URL
    model = env_model or DEFAULT_MODEL
    source = "env" if env_url else ""

    rows = {}
    if user_id is not None:
        global_rows = _read_settings(0)
        if any((global_rows.get("ollama_url") or "").strip()
               or (global_rows.get("ollama_model") or "").strip()
               or (global_rows.get("ollama_enabled") or "").strip()):
            rows = global_rows
            source = "global"
        else:
            rows = _read_settings(user_id)

    saved_url = (rows.get("ollama_url") or "").strip()
    saved_model = (rows.get("ollama_model") or "").strip()

    if saved_url:
        url = saved_url
    if saved_model:
        model = saved_model
    if saved_url and source != "global":
        source = "settings"

    cfg["url"] = url or DEFAULT_URL
    cfg["model"] = model or DEFAULT_MODEL
    cfg["has_url"] = bool(saved_url or env_url)

    enabled_str = rows.get("ollama_enabled", "").strip()
    enabled_in_settings = enabled_str == "1"
    env_set = bool(env_url or env_model)
    explicit_off = enabled_str == "0"
    cfg["enabled"] = ((enabled_in_settings or env_set)
                      and not explicit_off and is_available())
    cfg["source"] = source if cfg["enabled"] else ""

    return cfg



def call_ollama(prompt, url=None, model=None, timeout=REQUEST_TIMEOUT,
                max_output_tokens=MAX_OUTPUT_TOKENS, json_mode=True):
    """
    Send *prompt* to the Ollama server and return the raw response text.
    Raises OllamaError on any failure so callers can fall back gracefully.
    """
    url = (url or DEFAULT_URL).strip()
    if not url:
        raise OllamaError(
            "No Ollama URL configured. Set it in Settings or "
            "set the OLLAMA_URL environment variable."
        )

    model = (model or DEFAULT_MODEL).strip()

    # Ollama REST API: POST /api/generate
    endpoint = url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "num_predict": max_output_tokens,
        },
    }
    # "format": "json" forces JSON output on supporting models, but it can
    # cause HTTP 400 on older models/servers — only send it when asked.
    if json_mode:
        payload["format"] = "json"
    request = urllib.request.Request(
        endpoint,
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
            detail = str(error_body.get("error", ""))[:300]
        except Exception:
            pass
        _note_failure("HTTP %s" % exc.code)
        raise OllamaError(
            f"Ollama API error (HTTP {exc.code}): {detail or exc.reason}"
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _note_failure("network")
        raise OllamaError(
            f"Cannot reach Ollama at {url}. "
            f"Is the server running? (start it with: ollama serve) Error: {exc}"
        )
    except json.JSONDecodeError as exc:
        _note_failure("bad json")
        raise OllamaError(f"Ollama returned an invalid JSON response: {exc}")

    text = body.get("response", "")
    if not text:
        _note_failure("empty")
        raise OllamaError("Ollama returned an empty response.")
    _note_success()
    return text.strip()


def extract_text(body):
    """Pull the generated text out of an Ollama /api/generate response body."""
    try:
        return (body.get("response") or "").strip()
    except Exception:
        return ""



def parse_json_block(text):
    """
    Robustly extract the first JSON array/object from model output.
    Handles ```json fences and prose around the JSON.  Raises ValueError.
    Reuses gemini_ai.parse_json_block when available for consistency.
    """
    try:
        from gemini_ai import parse_json_block as _gparse
        return _gparse(text)
    except ImportError:
        pass

    if not text or not text.strip():
        raise ValueError("Empty text -- no JSON to parse.")

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


def test_connection(url=None, model=None):
    """Quick connectivity test.  Returns (ok: bool, message: str)."""
    url = (url or DEFAULT_URL).strip()
    model = (model or DEFAULT_MODEL).strip()
    if not url:
        return False, "No Ollama URL provided. Example: http://localhost:11434"
    try:
        reply = call_ollama("Reply with exactly: OK", url=url, model=model,
                            timeout=30, max_output_tokens=512, json_mode=False)
        return True, (
            f"Connected to Ollama at {url} (model: {model})! "
            f"Model replied: {reply[:80]}"
        )
    except OllamaError as exc:
        return False, str(exc)


def list_models(url=None):
    """Return (ok, models_or_message). Uses GET /api/tags."""
    url = (url or DEFAULT_URL).strip() or DEFAULT_URL
    endpoint = url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return False, f"Cannot reach Ollama at {url}: {exc}"
    try:
        models = [m.get("name", "") for m in (body.get("models") or []) if m.get("name")]
        return True, models
    except Exception as exc:
        return False, f"Unexpected /api/tags response: {exc}"


def chat(prompt, url=None, model=None, timeout=REQUEST_TIMEOUT,
         max_output_tokens=MAX_OUTPUT_TOKENS, json_mode=False):
    """Simple chat-style helper.

    Same as call_ollama() but defaults to plain-text output (json_mode=False)
    so short models like qwen2.5:1.5b answer without HTTP 400 errors, e.g.::

        import ollama_ai
        print(ollama_ai.chat("Say hello!"))
    """
    return call_ollama(prompt, url=url, model=model, timeout=timeout,
                       max_output_tokens=max_output_tokens, json_mode=json_mode)
