"""
AI Provider Abstraction Layer
==============================
Primary:  Groq API (fast, free tier)
Fallback: Gemini API (optional — only used if GEMINI_API_KEY is set and Groq fails)

Usage:
    from ai_providers import call_ai, call_ai_json
    result = call_ai(prompt, max_tokens=4000)
    data   = call_ai_json(prompt, max_tokens=4000)
"""

import os
import json
import re

# ── Provider config ────────────────────────────────────────────
AI_PROVIDER   = os.environ.get("AI_PROVIDER", "groq")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL    = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Public error string for the last failure
LAST_ERROR = ""


def _set_error(msg: str):
    global LAST_ERROR
    LAST_ERROR = msg


# ── JSON cleaning helper ──────────────────────────────────────
def clean_json_response(raw: str) -> str:
    """
    Strip markdown code fences that AI sometimes wraps around JSON.
    Handles: ```json { ... } ``` and ``` { ... } ```
    """
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw)
    return raw.strip()


# ── Groq ──────────────────────────────────────────────────────
def call_groq(prompt: str, max_tokens: int = 4000, temperature: float = 0.2) -> str | None:
    """Call Groq API. Returns raw text or None on failure."""
    if not GROQ_API_KEY:
        _set_error("GROQ_API_KEY is not configured.")
        return None

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        error_str = str(e).lower()
        if "rate" in error_str or "429" in error_str or "limit" in error_str:
            _set_error("Groq rate limit hit. Please try again in a minute.")
        elif "context" in error_str or "token" in error_str or "too long" in error_str:
            _set_error("Tender text is too long for Groq. Try pasting a shorter section or request manual review.")
        else:
            _set_error(f"Groq API error: {e}")
        return None


# ── Gemini ─────────────────────────────────────────────────────
def call_gemini(prompt: str, max_tokens: int = 8000, temperature: float = 0.2) -> str | None:
    """Call Gemini API. Returns raw text or None on failure. Only used if key is set."""
    if not GEMINI_API_KEY:
        _set_error("GEMINI_API_KEY is not configured.")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text.strip()

    except Exception as e:
        _set_error(f"Gemini API error: {e}")
        return None


# ── Main entry point ──────────────────────────────────────────
def call_ai(prompt: str, max_tokens: int = 4000, temperature: float = 0.2,
            model_preference: str | None = None) -> str | None:
    """
    Call AI with automatic fallback.

    Priority:
      1. model_preference if given (e.g. "gemini" for long texts)
      2. AI_PROVIDER env var (default: groq)
      3. Fallback to other provider if primary fails

    Returns raw text response, or None if all providers fail.
    Check ai_providers.LAST_ERROR for the last error message.
    """
    _set_error("")

    provider = model_preference or AI_PROVIDER

    if provider == "gemini":
        primary_fn, fallback_fn = call_gemini, call_groq
        primary_name, fallback_name = "Gemini", "Groq"
    else:
        primary_fn, fallback_fn = call_groq, call_gemini
        primary_name, fallback_name = "Groq", "Gemini"

    # Try primary
    print(f"[ai_providers] Trying {primary_name}...")
    result = primary_fn(prompt, max_tokens=max_tokens, temperature=temperature)
    if result is not None:
        return result

    # Primary failed — try fallback (only if other key exists)
    if (fallback_fn == call_gemini and not GEMINI_API_KEY) or \
       (fallback_fn == call_groq and not GROQ_API_KEY):
        print(f"[ai_providers] {fallback_name} not configured, skipping fallback.")
        return None

    print(f"[ai_providers] {primary_name} failed, trying {fallback_name}...")
    result = fallback_fn(prompt, max_tokens=max_tokens, temperature=temperature)
    if result is not None:
        return result

    _set_error("AI service is temporarily busy. Please try again in a few minutes or request manual review.")
    return None


# ── JSON-specific call with auto-retry ────────────────────────
def call_ai_json(prompt: str, max_tokens: int = 4000, retries: int = 1) -> dict | None:
    """
    Call AI and parse response as JSON.
    If response is not valid JSON, retries once with strict reminder.
    Returns parsed dict or None.
    """
    result = call_ai(prompt, max_tokens=max_tokens)
    if result is None:
        return None

    cleaned = clean_json_response(result)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Retry once with strict instruction
    if retries > 0:
        print("[ai_providers] JSON parse failed, retrying with strict instruction...")
        retry_prompt = (
            prompt +
            "\n\nIMPORTANT: Return ONLY valid JSON. No markdown. No explanation. No code fences. "
            "No extra text before or after the JSON object."
        )
        result = call_ai(retry_prompt, max_tokens=max_tokens)
        if result is None:
            return None
        cleaned = clean_json_response(result)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            _set_error(f"AI returned invalid JSON even after retry: {e}")
            return None

    _set_error("AI returned invalid JSON. Please try again.")
    return None
