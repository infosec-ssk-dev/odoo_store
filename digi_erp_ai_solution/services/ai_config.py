# -*- coding: utf-8 -*-
"""DIGI-ERP AI — runtime config lookup.

Single source of truth for the server URL / model / timeout / num_ctx that
the controller and the schema_rag service need. Reads ir.config_parameter
on every call (with defaults), so admins changing the settings page take
effect on the next request without an Odoo restart.
"""

# Sane defaults if no ir.config_parameter row exists yet. These match what
# was hardcoded in the codebase before the settings page existed.
_DEFAULTS = {
    "local_base_url":  "http://localhost:11434",
    "chat_model":       "llama3.2",
    "embed_model":      "nomic-embed-text",
    "request_timeout":  500,
    "num_ctx":          32768,
    "embed_base_url":   "",  # blank → reuse local_base_url
    # Which reader runs first. Matches the Settings default, so an unwritten
    # parameter behaves exactly like the value shown on the page.
    "ocr_primary":      "llm_vision",
}

# The OCR engines the admin may pick as the PRIMARY one, in the order they
# should be tried. Whichever is chosen leads the chain; the remaining
# engines follow as automatic fallbacks (a missing optional dependency in
# any step degrades gracefully to the next). "llm_vision" is always the
# last-resort catch-all, so it is never re-inserted ahead of itself.
_OCR_ENGINE_ORDER = ("paddleocr", "surya", "tesseract", "llm_vision")


def resolve_ocr_strategy(env, chat_provider_id=None):
    """Decide HOW an uploaded image should be read, given the chat model.

    Returns a dict describing the strategy so the controller can branch:

        {"mode": "dedicated",    "provider_id": <int>}
            → a dedicated OCR AI model is configured in Settings. Use it to
              turn the image into text; the chat model then works on that
              text (two models, two calls).

        {"mode": "single_call",  "provider_id": <int>}
            → no dedicated OCR model, and the chat-selected model can read
              images. Send the image + the user's prompt to it in ONE call:
              it does its own OCR AND answers. No separate OCR step.

        {"mode": "classic",      "provider_id": <int|None>}
            → no dedicated OCR model and the chat model is text-only (or
              unknown / Auto). Fall back to the classic OCR engine chain to
              extract text first; the chat model then answers on the text.
              `provider_id` is passed through for the llm_vision fallback
              step so it still targets the right endpoint.

    Args:
        env: Odoo environment.
        chat_provider_id: the provider the chat is currently using (the
            picker selection). May be None / "auto".
    """
    Provider = env["digi_erp.model.llm.provider"].sudo()

    # 1. Dedicated OCR model wins whenever it's set and still valid.
    raw = env["ir.config_parameter"].sudo().get_param("digi_erp_ai.ocr_provider_id")
    if raw:
        try:
            ocr_prov = Provider.browse(int(raw))
            if ocr_prov.exists() and ocr_prov.active:
                return {"mode": "dedicated", "provider_id": ocr_prov.id}
        except (TypeError, ValueError):
            pass  # bad stored value → fall through to the chat-model logic

    # 2. No dedicated model → let the chat-selected model handle it IF it
    #    can see images. "auto"/blank means we don't know which concrete
    #    model will answer, so we can't assume vision → classic chain.
    if chat_provider_id and str(chat_provider_id).lower() != "auto":
        try:
            chat_prov = Provider.browse(int(chat_provider_id))
            if chat_prov.exists() and chat_prov.supports_vision:
                return {"mode": "single_call", "provider_id": chat_prov.id}
        except (TypeError, ValueError):
            pass

    # 3. Text-only / unknown chat model → classic OCR engine chain, keeping
    #    the chat provider for the llm_vision fallback step.
    pid = None
    if chat_provider_id and str(chat_provider_id).lower() != "auto":
        try:
            pid = int(chat_provider_id)
        except (TypeError, ValueError):
            pid = None
    return {"mode": "classic", "provider_id": pid}


def get_ocr_chain(env):
    """Build the ordered OCR engine chain from the admin's primary choice.

    Reads ``digi_erp_ai.ocr_primary`` and returns a tuple with that engine
    first, every other known engine following as a fallback. Unknown /
    blank values fall back to the PaddleOCR-first default so OCR never
    breaks on a bad config value.
    """
    primary = (env["ir.config_parameter"].sudo()
               .get_param("digi_erp_ai.ocr_primary")
               or _DEFAULTS["ocr_primary"])
    if primary not in _OCR_ENGINE_ORDER:
        primary = _DEFAULTS["ocr_primary"]
    # Primary first, then the rest of the known engines in their canonical
    # order (skipping the primary so it isn't listed twice).
    return (primary,) + tuple(e for e in _OCR_ENGINE_ORDER if e != primary)


def build_ocr_chain(env, primary):
    """Return an ordered OCR chain led by ``primary``.

    Same shape as :func:`get_ocr_chain` (primary engine first, the other
    known engines following as automatic fallbacks), but the leading
    engine is supplied by the caller instead of read from the global
    ``digi_erp_ai.ocr_primary`` setting. This lets a feature module (PO /
    vendor-bill import) drive OCR from its OWN "Primary OCR engine"
    choice. A blank / unknown ``primary`` falls back to the global admin
    default so OCR never breaks on an unset per-module value.
    """
    if not primary or primary not in _OCR_ENGINE_ORDER:
        return get_ocr_chain(env)
    return (primary,) + tuple(e for e in _OCR_ENGINE_ORDER if e != primary)


def get_ai_config(env):
    """Return a dict of runtime DIGI-ERP AI config.

    Caller is expected to be running inside a request (so env is available).
    Values are read from ir.config_parameter; integers are coerced.
    """
    p = env["ir.config_parameter"].sudo()
    cfg = {
        "local_base_url":  (p.get_param("digi_erp_ai.local_base_url")  or _DEFAULTS["local_base_url"]).rstrip("/"),
        "chat_model":        p.get_param("digi_erp_ai.chat_model")        or _DEFAULTS["chat_model"],
        "embed_model":       p.get_param("digi_erp_ai.embed_model")       or _DEFAULTS["embed_model"],
        "request_timeout":  _to_int(p.get_param("digi_erp_ai.request_timeout"), _DEFAULTS["request_timeout"]),
        "num_ctx":          _to_int(p.get_param("digi_erp_ai.num_ctx"),         _DEFAULTS["num_ctx"]),
        "embed_base_url":  (p.get_param("digi_erp_ai.embed_base_url") or "").rstrip("/"),
    }
    # Embed URL falls back to the chat URL when blank.
    if not cfg["embed_base_url"]:
        cfg["embed_base_url"] = cfg["local_base_url"]
    return cfg


def _to_int(raw, default):
    """Coerce an ir.config_parameter value to a positive int, else `default`.

    Two traps this exists to avoid, both of which produced a 0:

    1. `get_param` returns **False** for a key that was never written — not
       None, not "". `False is None` is False, `False == ""` is False, and
       `int(False)` is 0 and raises nothing, so the obvious guard let a
       never-configured setting through as 0. On a fresh install that meant
       `request_timeout=0` (every HTTP call dies with "connect timeout to 0")
       and `num_ctx=0`, until somebody opened Settings and pressed Save.
    2. A literal "0" stored by hand. Neither of these settings has a
       meaningful zero — a zero timeout and a zero context window are just
       broken — so both are treated as "not set".
    """
    if raw is None or raw is False or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
