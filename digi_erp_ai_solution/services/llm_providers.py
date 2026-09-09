# -*- coding: utf-8 -*-
"""LLM provider adapters for DIGI-ERP AI.

Each remote provider has its own request / response / tool-use shape. The
controller speaks one uniform protocol; adapters here translate to and from
the OpenAI-style messages list:

    [
        {"role": "system", "content": "..."},
        {"role": "user",   "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [
            {"id": "...", "function": {"name": "...", "arguments": {...}}},
        ]},
        {"role": "tool", "name": "...", "tool_call_id": "...", "content": "..."},
    ]

A user message may also carry an image, in the self-hosted /api/chat shape:

        {"role": "user", "content": "Read this page.", "images": ["<base64>"]}

Each adapter translates that into its vendor's own multimodal format — see
`_iter_images` below.

`call_chat(provider, messages, tools)` returns:

    {"ok": True,  "message": {"content": str, "tool_calls": [...]}}
    {"ok": False, "error": str}

`tool_calls` items always look like:
    {"id": str, "function": {"name": str, "arguments": dict}}

Provider types handled:
    - openai           — https://api.openai.com/v1
    - anthropic        — https://api.anthropic.com/v1
    - gemini           — https://generativelanguage.googleapis.com/v1beta
    - xai              — https://api.x.ai/v1   (OpenAI-compatible)
    - openai_compat    — caller-supplied base_url, OpenAI-compatible
"""
import base64
import json
import logging
import re
import uuid

import requests

from odoo import _

# Endpoint defaults live on the model, next to the provider_type
# selection they belong to. They used to be duplicated as literals in
# each adapter below — same five URLs, two places to keep in step.
from ..models.llm_provider import PROVIDER_DEFAULT_BASE_URL

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 500

# ── ANSI helpers (mirror controller) ──────────────────────────────────────────
_C = {
    "reset":  "\033[0m", "bold":   "\033[1m",
    "cyan":   "\033[96m", "green":  "\033[92m",
    "yellow": "\033[93m", "red":    "\033[91m",
    "blue":   "\033[94m", "magenta": "\033[95m",
}


def _cprint(color, label, msg=""):
    # Routed through the Odoo logger so output lands in the configured
    # logfile. A bare print() would only reach stdout/journald, which
    # split DIGI-ERP AI output across two places on a prod deployment.
    # `color` is kept for call-site compatibility but is no longer used.
    _logger.info("[%s] %s", label, msg)


def _new_call_id():
    return f"call_{uuid.uuid4().hex[:16]}"


# Credentials that can still end up inside an error string: a base_url the
# admin typed with `?key=` or `user:pass@` in it, a provider echoing the key
# back in a 401 body. Nothing here should ever reach a log line or a chat
# bubble, so every error path below goes through this first.
_SECRET_PATTERNS = (
    re.compile(r"((?:api[-_]?key|key|access_token|token)=)[^&\s\"']+", re.I),
    re.compile(r"(://[^/\s:@]+):[^/\s@]+@"),
    re.compile(r"\b(sk-|sk-ant-|AIza|xai-|gsk_)[A-Za-z0-9_\-]{8,}"),
)


def _scrub(text):
    """Redact anything that looks like a credential."""
    out = str(text)
    out = _SECRET_PATTERNS[0].sub(r"\1***", out)
    out = _SECRET_PATTERNS[1].sub(r"\1:***@", out)
    out = _SECRET_PATTERNS[2].sub(r"\1***", out)
    return out


def _friendly_http_error(provider, status, body):
    """Turn a provider's HTTP failure into a sentence a person can act on.

    The raw body is a vendor-specific JSON blob. Showing it in the chat is
    how a normal user ends up reading `{"error":{"message":"Authentication
    Fails, Your api key: ****cd is invalid"...` — noise they cannot act on,
    from a setting they cannot even see. The blob still goes to the Odoo log
    for whoever administers the models; the user gets the one sentence that
    tells them who to go to.
    """
    name = provider.name or provider.model_name or _("the AI model")
    _cprint("red", "LLM HTTP %s" % status,
            "%s: %s" % (name, _scrub(body)[:400]))
    _logger.warning("DIGI-ERP AI: %s returned HTTP %s: %s",
                    name, status, _scrub(body)[:400])
    if status in (401, 403):
        return _("%(name)s refused the connection: its API key is missing, "
                 "wrong or expired. An administrator can fix it under "
                 "DIGI-ERP AI \u2192 AI Models.", name=name)
    if status == 404:
        return _("%(name)s does not recognise the model "
                 "\u201c%(model)s\u201d. An administrator can correct the "
                 "model name under DIGI-ERP AI \u2192 AI Models.",
                 name=name, model=provider.model_name or "")
    if status == 429:
        return _("%(name)s is busy or its usage limit has been reached. "
                 "Please try again in a moment.", name=name)
    if status in (402, 413):
        return _("%(name)s rejected the request: the account has no credit "
                 "left, or the message is too large. An administrator can "
                 "check the account.", name=name)
    if 500 <= status <= 599:
        return _("%(name)s is having trouble on its side right now. Please "
                 "try again in a moment.", name=name)
    return _("%(name)s could not answer this request (error %(status)s). The "
             "details are in the Odoo log.", name=name, status=status)


def _friendly_transport_error(provider, exc, kind="reach"):
    """Same idea for a connection that never got an answer at all."""
    name = provider.name or provider.model_name or _("the AI model")
    _logger.warning("DIGI-ERP AI: cannot %s %s: %s: %s",
                    kind, name, type(exc).__name__, _scrub(exc)[:300])
    if kind == "timeout":
        return _("%(name)s took too long to answer. Try a shorter question, "
                 "or raise the answer timeout under Settings \u2192 "
                 "DIGI-ERP AI.", name=name)
    return _("Could not reach %(name)s. Check that it is running and that "
             "its address is correct under DIGI-ERP AI \u2192 AI Models.",
             name=name)


# ── Images ────────────────────────────────────────────────────────────────────
#
# One protocol in, three wire formats out. The uniform protocol borrows the
# self-hosted /api/chat shape — `images: ["<base64>"]` on a user turn — so the
# local path passes it straight through and only the cloud adapters translate:
#
#   OpenAI-compatible  content: [{"type": "text", ...},
#                                {"type": "image_url",
#                                 "image_url": {"url": "data:<mime>;base64,..."}}]
#   Anthropic          content: [{"type": "image",
#                                 "source": {"type": "base64",
#                                            "media_type": <mime>, "data": ...}},
#                                {"type": "text", ...}]
#   Gemini             parts:   [{"text": ...},
#                                {"inlineData": {"mimeType": <mime>,
#                                                "data": ...}}]
#
# Anthropic and Gemini REQUIRE the media type and we get the bytes with no
# filename attached, hence the magic-number sniff below.

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_mime(b64):
    """Best-effort media type for a base64 image.

    24 base64 characters decode to exactly 18 bytes with no padding games,
    which covers every magic number we care about. Defaults to JPEG: that is
    what `_ocr_normalize_image` re-encodes everything it can open into, i.e.
    the overwhelmingly common case on this path.
    """
    try:
        head = base64.b64decode(b64[:24])
    except (ValueError, TypeError):
        return "image/jpeg"
    for magic, mime in _IMAGE_MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _iter_images(message):
    """Yield `(base64_data, media_type)` for one message's `images` key.

    Accepts a bare base64 string or a full `data:<mime>;base64,...` URI, so a
    caller that already built one for the browser doesn't have to take it
    apart again.
    """
    for raw in message.get("images") or []:
        if not raw:
            continue
        data, mime = raw, None
        if isinstance(data, str) and data.startswith("data:"):
            header, sep, payload = data.partition(",")
            if not sep:
                continue
            mime = header[5:].split(";")[0] or None
            data = payload
        yield data, (mime or _sniff_image_mime(data))


def messages_carry_images(messages):
    """True when at least one message in the list has an image attached."""
    return any((m.get("images") for m in messages or []))


# ── Public entry point ────────────────────────────────────────────────────────

def call_chat(provider, messages, tools=None):
    """Dispatch to the right adapter based on provider.provider_type.

    sudo() on entry, deliberately. Every adapter below reads `api_key`, which
    is restricted to group_ai_manager on the model — so a chat turn run by a
    plain AI User would otherwise raise AccessError here, on a field they are
    right to be denied but that the SERVER still needs to reach the API. Doing
    it once at the single choke point beats relying on each caller to remember.
    The key never leaves this module: it goes into an outbound header.
    """
    provider = provider.sudo()
    pt = provider.provider_type
    label = f"{pt}:{provider.model_name or '?'}"

    # An image on a text-only model is not a transport problem — every vendor
    # answers it differently (a 400, a polite "I can't see images", or worse,
    # a confident hallucination about a picture it never received). Refuse it
    # here, once, with something the admin can act on.
    if messages_carry_images(messages) and not provider.supports_vision:
        _cprint("yellow", "LLM NO VISION", f"{provider.name} [{label}]")
        return {"ok": False, "error": _(
            "%(name)s is not set up to read images. Tick \"Can read images "
            "(OCR / vision)\" on that model if it supports vision, or pick "
            "another one to read documents under Settings → DIGI-ERP AI.",
            name=provider.name)}
    _cprint("blue", "LLM →", f"{provider.name} [{label}]  msgs={len(messages)}  "
            f"tools={'yes' if tools else 'no'}")
    try:
        if pt == "openai":
            out = _call_openai_compatible(
                provider, messages, tools,
                base_url=provider.base_url or PROVIDER_DEFAULT_BASE_URL["openai"],
                auth_header=("Authorization", f"Bearer {provider.api_key}"),
            )
        elif pt == "deepseek":
            out = _call_openai_compatible(
                provider, messages, tools,
                base_url=provider.base_url or PROVIDER_DEFAULT_BASE_URL["deepseek"],
                auth_header=("Authorization", f"Bearer {provider.api_key}"),
            )
        elif pt == "xai":
            out = _call_openai_compatible(
                provider, messages, tools,
                base_url=provider.base_url or PROVIDER_DEFAULT_BASE_URL["xai"],
                auth_header=("Authorization", f"Bearer {provider.api_key}"),
            )
        elif pt == "openai_compat":
            base = (provider.base_url or "").rstrip("/")
            if not base:
                return {"ok": False,
                        "error": _(
                            "%(name)s has no Base URL. An administrator can "
                            "add it under DIGI-ERP AI \u2192 AI Models.",
                            name=provider.name or "This model")}
            out = _call_openai_compatible(
                provider, messages, tools,
                base_url=base,
                auth_header=("Authorization", f"Bearer {provider.api_key}"),
            )
        elif pt == "anthropic":
            out = _call_anthropic(provider, messages, tools)
        elif pt == "gemini":
            out = _call_gemini(provider, messages, tools)
        else:
            return {"ok": False, "error": _(
                "%(name)s is set to a provider DIGI-ERP AI does not know "
                "(%(kind)s). An administrator can correct it under "
                "DIGI-ERP AI \u2192 AI Models.",
                name=provider.name or "This model", kind=pt or "?")}
    except requests.exceptions.Timeout as e:
        _cprint("red", "LLM TIMEOUT", f"{label}")
        return {"ok": False,
                "error": _friendly_transport_error(provider, e, "timeout")}
    except (requests.exceptions.ConnectionError,
            requests.exceptions.InvalidURL,
            requests.exceptions.MissingSchema,
            requests.exceptions.URLRequired) as e:
        _cprint("red", "LLM CONN ERR", f"{label}: {_scrub(e)}")
        return {"ok": False, "error": _friendly_transport_error(provider, e)}
    except Exception as e:
        _logger.exception("LLM provider error (%s)", label)
        _cprint("red", "LLM ERR", f"{label}: {type(e).__name__}: {_scrub(e)}")
        return {"ok": False, "error": _(
            "%(name)s could not be reached. The details are in the Odoo log "
            "(Settings \u2192 Technical \u2192 Logging).",
            name=provider.name or label)}

    if out.get("ok"):
        msg = out.get("message") or {}
        tc = msg.get("tool_calls") or []
        preview = (msg.get("content") or "")[:80]
        _cprint("green", "LLM OK", f"{label}  tool_calls={len(tc)}  content={preview!r}")
    else:
        _cprint("red", "LLM FAIL", f"{label}: {out.get('error')}")
    return out


# ── OpenAI-compatible (OpenAI, xAI/Grok, Groq, Together, OpenRouter, …) ─────

def _call_openai_compatible(provider, messages, tools, base_url, auth_header):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        auth_header[0]: auth_header[1],
    }
    payload = {
        "model": provider.model_name,
        "messages": _to_openai_messages(messages),
        "stream": False,
        "temperature": provider.temperature,
    }
    if provider.max_tokens:
        payload["max_tokens"] = provider.max_tokens
    if tools and provider.supports_tools:
        payload["tools"] = tools  # already in OpenAI shape

    resp = requests.post(url, headers=headers, json=payload,
                         timeout=provider.timeout_seconds or DEFAULT_TIMEOUT)
    if not resp.ok:
        return {"ok": False,
                "error": _friendly_http_error(provider, resp.status_code, resp.text)}
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({
            "id": tc.get("id") or _new_call_id(),
            "function": {"name": fn.get("name") or "", "arguments": args},
        })
    return {"ok": True, "message": {
        "content": msg.get("content") or "",
        "tool_calls": tool_calls,
    }}


def _to_openai_messages(messages):
    """Pass through — uniform protocol IS the OpenAI shape, with two tweaks:
    (1) tool_calls arguments are dict in our protocol but must be a JSON string
        on the wire.
    (2) tool messages need tool_call_id (we synthesize one if missing)."""
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                tcs.append({
                    "id": tc.get("id") or _new_call_id(),
                    "type": "function",
                    "function": {"name": fn.get("name") or "", "arguments": args or "{}"},
                })
            out.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": tcs,
            })
        elif m.get("role") == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or _new_call_id(),
                "name": m.get("name") or "",
                "content": m.get("content") or "",
            })
        elif m.get("images"):
            # Multimodal user turn: content becomes a parts array. The image
            # travels as a data: URI rather than a public URL — the blob only
            # ever existed in this process.
            parts = []
            text = m.get("content") or ""
            if text:
                parts.append({"type": "text", "text": text})
            for data, mime in _iter_images(m):
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                })
            out.append({"role": m.get("role") or "user", "content": parts})
        else:
            out.append({
                "role": m.get("role"),
                "content": m.get("content") or "",
            })
    return out


# ── Anthropic ─────────────────────────────────────────────────────────────────

# Default output cap for Claude. The Anthropic API requires max_tokens on every
# request and has no "provider default" — 4096 truncates a long report,
# so we default high. All current Claude models (Opus 4.x, Sonnet 4.6/5, Haiku
# 4.5) accept at least 8192; the newer ones go to 128k (streaming recommended
# past ~16k, which we don't do here — long single reports stay well under it).
_ANTHROPIC_DEFAULT_MAX_TOKENS = 16000

# Claude model families that REJECT sampling parameters (temperature/top_p/top_k)
# with a 400. Sending temperature to these breaks the request. Detected by
# substring so aliases and dated snapshots both match.
#   claude-opus-4-7, claude-opus-4-8, claude-sonnet-5, claude-fable-5, claude-mythos-5
_ANTHROPIC_NO_SAMPLING = (
    "opus-4-7", "opus-4-8", "sonnet-5", "fable-5", "mythos-5",
)


def _anthropic_rejects_temperature(model_name):
    m = (model_name or "").lower()
    return any(tag in m for tag in _ANTHROPIC_NO_SAMPLING)


def _call_anthropic(provider, messages, tools):
    url = (provider.base_url
           or PROVIDER_DEFAULT_BASE_URL["anthropic"]).rstrip("/") + "/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
    }
    system_prompt, anth_messages = _to_anthropic_messages(messages)

    payload = {
        "model": provider.model_name,
        "max_tokens": provider.max_tokens or _ANTHROPIC_DEFAULT_MAX_TOKENS,
        "messages": anth_messages,
    }
    # Newer Claude models (Opus 4.7/4.8, Sonnet 5, Fable 5) reject temperature
    # with a 400 — only send it to models that still accept sampling params.
    if not _anthropic_rejects_temperature(provider.model_name):
        payload["temperature"] = provider.temperature

    # ── Prompt caching ────────────────────────────────────────────────────────
    # Our assistant runs an agentic tool loop: one user question triggers up to
    # MAX_TOOL_ITERATIONS calls, and EACH call resends the *same* system prompt
    # (with schema/RAG context) and the *same* tool definitions. Marking those
    # two stable, front-of-prompt blocks with `cache_control: ephemeral` lets
    # Claude reuse them from cache on iterations 2..N at ~10% of the input price
    # instead of re-reading them at full price every time. The first call writes
    # the cache (small surcharge); every later call in the ~5-min window hits it.
    #
    # We place breakpoints on the two largest identical prefixes:
    #   1. the system block (schema/RAG + instructions)
    #   2. the LAST tool in the array — Anthropic caches everything up to and
    #      including a marked block, so one marker on the final tool caches the
    #      whole tool schema.
    # Anything that changes per-turn (latest user message, tool_results) lives
    # AFTER these blocks and is billed normally — as it should be.
    if system_prompt:
        payload["system"] = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
    if tools and provider.supports_tools:
        anth_tools = [_openai_tool_to_anthropic(t) for t in tools]
        if anth_tools:
            # Cache the whole tool array: mark the last tool as the breakpoint.
            anth_tools[-1] = {**anth_tools[-1],
                              "cache_control": {"type": "ephemeral"}}
        payload["tools"] = anth_tools

    # ── Rolling message cache breakpoint ──────────────────────────────────────
    # The system + tools breakpoints above cache the STABLE prefix. But our
    # agentic loop re-sends an ever-GROWING `messages` array every iteration
    # (conversation history + each new tool_result). With no breakpoint on the
    # messages, ALL of it is re-billed at full input price on every pass —
    # quadratic cost, the dominant driver on multi-step turns. Marking the LAST
    # content block of the LAST message makes Anthropic cache the whole
    # conversation prefix incrementally: iteration N reads iterations 1..N-1
    # from cache (~10% price) and pays full price only for the newest turn.
    # That's 3 of Anthropic's 4 allowed breakpoints (system + tools + this).
    # Degrades gracefully — if the prefix is under the ~1k-token cache minimum
    # the marker is simply ignored.
    _mark_last_message_cacheable(anth_messages)

    resp = requests.post(url, headers=headers, json=payload,
                         timeout=provider.timeout_seconds or DEFAULT_TIMEOUT)
    if not resp.ok:
        return {"ok": False,
                "error": _friendly_http_error(provider, resp.status_code, resp.text)}
    data = resp.json()

    # Surface prompt-cache effectiveness. `cache_read_input_tokens` are the
    # tokens served from cache at ~10% price (the win); `cache_creation_input_
    # tokens` are the one-time write on the first call of the loop. Seeing reads
    # climb across a tool loop is the proof caching is actually engaged.
    usage = data.get("usage") or {}
    cache_read = usage.get("cache_read_input_tokens") or 0
    cache_write = usage.get("cache_creation_input_tokens") or 0
    if cache_read or cache_write:
        _cprint("cyan", "LLM CACHE",
                f"{provider.model_name}: read={cache_read} write={cache_write} "
                f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}")

    # A safety refusal returns HTTP 200 with stop_reason "refusal" and (usually)
    # empty content — surface it as an error rather than an empty message.
    stop_reason = data.get("stop_reason")
    if stop_reason == "refusal":
        details = data.get("stop_details") or {}
        reason = details.get("explanation") or details.get("category") or ""
        return {"ok": False,
                "error": _("Claude declined to answer (safety reason)%s.",
                           f": {reason}" if reason else "")}

    content_text = []
    tool_calls = []
    for block in data.get("content") or []:
        btype = block.get("type")
        if btype == "text":
            content_text.append(block.get("text") or "")
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id") or _new_call_id(),
                "function": {
                    "name": block.get("name") or "",
                    "arguments": block.get("input") or {},
                },
            })

    text = "".join(content_text)
    # If Claude hit the output cap mid-report, warn in the log — the caller
    # gets the partial text but the truncation is otherwise invisible.
    if stop_reason == "max_tokens":
        _cprint("yellow", "LLM TRUNCATED",
                f"{provider.model_name}: reply cut at max_tokens="
                f"{payload['max_tokens']} — raise 'Max output tokens'.")

    return {"ok": True, "message": {
        "content": text,
        "tool_calls": tool_calls,
    }}


def _to_anthropic_messages(messages):
    """Anthropic requires:
    - `system` as a separate field
    - alternating user/assistant only
    - tool_use blocks inside assistant messages
    - tool_result blocks inside user messages (NOT a 'tool' role)
    """
    system_chunks = []
    out = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system_chunks.append(m.get("content") or "")
            continue
        if role == "tool":
            # Anthropic groups consecutive tool_results inside one user message.
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or _new_call_id(),
                "content": m.get("content") or "",
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks = []
            text = m.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or _new_call_id(),
                    "name": fn.get("name") or "",
                    "input": args or {},
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        if m.get("images"):
            # Image blocks go BEFORE the text block: Anthropic's own guidance
            # is that the model follows an instruction better when it has
            # already seen what the instruction is about.
            blocks = []
            for data, mime in _iter_images(m):
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64",
                               "media_type": mime,
                               "data": data},
                })
            text = m.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            out.append({"role": "user", "content": blocks})
            continue
        # plain user / assistant text
        out.append({"role": role or "user", "content": m.get("content") or ""})
    return ("\n\n".join(c for c in system_chunks if c).strip(), out)


def _mark_last_message_cacheable(anth_messages):
    """Put a rolling `cache_control` breakpoint on the last content block of
    the last message, so Anthropic caches the growing conversation prefix
    across the tool loop (see the call site in `_call_anthropic`).

    Handles both content shapes our converter produces:
      • a list of blocks (assistant tool_use / user tool_result) → mark the
        last block (all of text/tool_use/tool_result support cache_control);
      • a plain string (a first-turn user/assistant text) → wrap it in a
        single text block so there's a block to hang the marker on.
    No-op on an empty message list or empty content."""
    if not anth_messages:
        return
    last = anth_messages[-1]
    content = last.get("content")
    cc = {"type": "ephemeral"}
    if isinstance(content, list):
        if content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = cc
    elif isinstance(content, str) and content:
        last["content"] = [{"type": "text", "text": content,
                            "cache_control": cc}]


def _openai_tool_to_anthropic(tool):
    fn = tool.get("function") or {}
    return {
        "name": fn.get("name") or "",
        "description": fn.get("description") or "",
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


# ── Google Gemini ─────────────────────────────────────────────────────────────

def _call_gemini(provider, messages, tools):
    base = (provider.base_url
            or PROVIDER_DEFAULT_BASE_URL["gemini"]).rstrip("/")
    url = f"{base}/models/{provider.model_name}:generateContent"
    # The key travels as a HEADER, not as `?key=` in the query string. Google
    # documents both, but a URL ends up inside requests' own exception text
    # ("Max retries exceeded with url: …?key=SECRET"), which we log and hand
    # back to the user as an error message — a plain AI User would have read
    # the administrator's key off a failed Gemini call.
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": provider.api_key,
    }

    system_text, contents = _to_gemini_contents(messages)
    gen_config = {"temperature": provider.temperature}
    if provider.max_tokens:
        gen_config["maxOutputTokens"] = provider.max_tokens
    payload = {"contents": contents, "generationConfig": gen_config}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    if tools and provider.supports_tools:
        payload["tools"] = [{
            "functionDeclarations": [_openai_tool_to_gemini(t) for t in tools],
        }]

    resp = requests.post(url, headers=headers, json=payload,
                         timeout=provider.timeout_seconds or DEFAULT_TIMEOUT)
    if not resp.ok:
        return {"ok": False,
                "error": _friendly_http_error(provider, resp.status_code, resp.text)}
    data = resp.json()

    content_text = []
    tool_calls = []
    candidates = data.get("candidates") or []
    if not candidates:
        return {"ok": False, "error": _(
            "%(name)s returned an empty answer. Please try asking again.",
            name=provider.name or "Gemini")}
    parts = (candidates[0].get("content") or {}).get("parts") or []
    for part in parts:
        if "text" in part:
            content_text.append(part.get("text") or "")
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": _new_call_id(),
                "function": {
                    "name": fc.get("name") or "",
                    "arguments": fc.get("args") or {},
                },
            })
    return {"ok": True, "message": {
        "content": "".join(content_text),
        "tool_calls": tool_calls,
    }}


def _to_gemini_contents(messages):
    """Gemini wants `contents: [{role: 'user'|'model', parts: [...]}]` and
    a separate `systemInstruction`. Tool calls live inside `model` parts as
    `functionCall`; tool results go in `user` parts as `functionResponse`."""
    system_chunks = []
    out = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system_chunks.append(m.get("content") or "")
            continue
        if role == "tool":
            part = {
                "functionResponse": {
                    "name": m.get("name") or "",
                    "response": {"content": m.get("content") or ""},
                }
            }
            if out and out[-1]["role"] == "user":
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
            continue
        if role == "assistant":
            parts = []
            text = m.get("content") or ""
            if text:
                parts.append({"text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                parts.append({
                    "functionCall": {
                        "name": fn.get("name") or "",
                        "args": args or {},
                    }
                })
            if not parts:
                parts = [{"text": ""}]
            out.append({"role": "model", "parts": parts})
            continue
        # default: user — plus any attached image as an inlineData part.
        parts = []
        text = m.get("content") or ""
        if text:
            parts.append({"text": text})
        for data, mime in _iter_images(m):
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        out.append({"role": "user", "parts": parts or [{"text": ""}]})
    return ("\n\n".join(c for c in system_chunks if c).strip(), out)


def _openai_tool_to_gemini(tool):
    fn = tool.get("function") or {}
    params = fn.get("parameters") or {"type": "object", "properties": {}}
    # Gemini rejects `additionalProperties` in some SDK versions — strip recursively.
    return {
        "name": fn.get("name") or "",
        "description": fn.get("description") or "",
        "parameters": _strip_unsupported_schema_keys(params),
    }


def _strip_unsupported_schema_keys(node):
    if isinstance(node, dict):
        return {
            k: _strip_unsupported_schema_keys(v)
            for k, v in node.items()
            if k not in ("additionalProperties", "$schema", "definitions")
        }
    if isinstance(node, list):
        return [_strip_unsupported_schema_keys(x) for x in node]
    return node
