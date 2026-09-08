# -*- coding: utf-8 -*-
"""
Builds and caches a compact Odoo schema snapshot injected into the LLM
system prompt so the model knows exact field names before any tool call —
eliminating hallucinations like `partner_name` instead of `partner_id`.

The snapshot is built once from ir.model + ir.model.fields and cached for
_CACHE_TTL seconds at module level (shared across all workers in the same
process).
"""
import ast
import logging
import time

_logger = logging.getLogger(__name__)

_cache = {"text": None, "ts": 0.0}
_CACHE_TTL = 3600  # seconds — rebuild once per hour

# Model name prefixes excluded from the snapshot (too technical / too noisy)
_SKIP_MODEL_PREFIXES = (
    "base.import", "base.language", "base.setup",
    "bus.",
    "ir.actions", "ir.cron", "ir.default", "ir.exports",
    "ir.filters", "ir.logging", "ir.translation", "ir.ui",
    "mail.followers", "mail.notification", "mail.push", "mail.thread",
    "report.",
    "res.lang",
    "resource.calendar.leaves",
    "web.",
)

# Field names always skipped (chatter / audit / internal noise)
_SKIP_FIELDS = frozenset({
    "__last_update", "display_name",
    "create_uid", "write_uid", "create_date", "write_date",
    "message_ids", "message_follower_ids", "message_is_follower",
    "message_needaction", "message_needaction_counter",
    "message_has_error", "message_has_error_counter",
    "message_attachment_count", "message_main_attachment_id",
    "activity_ids", "activity_state", "activity_user_id",
    "activity_type_id", "activity_date_deadline", "activity_summary",
    "activity_exception_icon", "activity_exception_decoration",
    "website_message_ids", "has_message",
})

_SKIP_FIELD_PREFIXES = ("message_", "activity_has_")

# Field types with no value for domain building / query construction
_SKIP_TTYPES = frozenset({"binary", "html", "serialized"})

MAX_FIELDS_PER_MODEL = 30
MAX_MODELS = 500
MAX_SCHEMA_CHARS = 80_000


# ── helpers ───────────────────────────────────────────────────────────────────

def _skip_model(name):
    for p in _SKIP_MODEL_PREFIXES:
        if name.startswith(p):
            return True
    return False


def _skip_field(fname, ttype):
    if fname in _SKIP_FIELDS:
        return True
    for p in _SKIP_FIELD_PREFIXES:
        if fname.startswith(p):
            return True
    return ttype in _SKIP_TTYPES


def _sel_keys(sel_str):
    """Extract first 8 selection keys as a comma-separated string."""
    if not sel_str:
        return ""
    try:
        pairs = ast.literal_eval(sel_str) if isinstance(sel_str, str) else list(sel_str)
        return ",".join(str(k) for k, _ in pairs[:8])
    except Exception:
        return ""


# ── public API ────────────────────────────────────────────────────────────────

def build_schema_snapshot(env):
    """Return (possibly cached) compact schema text for all business models.

    Thread-safety: worst case two workers rebuild simultaneously — harmless,
    last writer wins and the result is deterministic.
    """
    global _cache
    now = time.monotonic()
    if _cache["text"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["text"]

    _logger.info("DIGI-ERP AI: rebuilding Odoo schema snapshot…")
    try:
        text = _build(env)
    except Exception as exc:
        _logger.warning("DIGI-ERP AI: schema build failed: %s", exc)
        text = ""

    _cache = {"text": text, "ts": now}
    return text


def invalidate_schema_cache():
    """Force a rebuild on next request (call after module install/upgrade)."""
    global _cache
    _cache = {"text": None, "ts": 0.0}


# ── builder ───────────────────────────────────────────────────────────────────

def _build(env):
    IrModel = env["ir.model"]
    IrFields = env["ir.model.fields"]

    models = IrModel.search_read(
        [("transient", "=", False)],
        ["id", "model", "name"],
        order="model",
    )

    lines = [
        "=== ODOO SCHEMA — technical names of the models and their fields ===\n"
        "Format: model.name (Label)\n"
        "  field:type  field:many2one→res.partner  field:selection[val1,val2]\n\n"
    ]
    total = sum(len(l) for l in lines)
    count = 0

    for m in models:
        mname = m["model"]
        if _skip_model(mname):
            continue
        # Only include models actually loaded in this Odoo instance
        if mname not in env:
            continue

        frows = IrFields.search_read(
            [("model_id", "=", m["id"])],
            ["name", "ttype", "relation", "selection"],
            order="name",
            limit=MAX_FIELDS_PER_MODEL + 20,
        )

        parts = []
        for f in frows:
            if len(parts) >= MAX_FIELDS_PER_MODEL:
                break
            fname, ttype = f["name"], f["ttype"]
            if _skip_field(fname, ttype):
                continue

            token = f"{fname}:{ttype}"
            rel = f.get("relation") or ""
            if rel:
                token += f"→{rel}"
            elif ttype == "selection":
                keys = _sel_keys(f.get("selection") or "")
                if keys:
                    token += f"[{keys}]"
            parts.append(token)

        if not parts:
            continue

        line = f"{mname} ({m['name']})\n  " + "  ".join(parts) + "\n"

        if total + len(line) > MAX_SCHEMA_CHARS:
            lines.append(
                f"[… {count} models included — schema truncated at {MAX_SCHEMA_CHARS} chars]\n"
            )
            break

        lines.append(line)
        total += len(line)
        count += 1
        if count >= MAX_MODELS:
            break

    text = "".join(lines)
    _logger.info("DIGI-ERP AI: schema snapshot built — %d models, %d chars", count, total)
    return text
