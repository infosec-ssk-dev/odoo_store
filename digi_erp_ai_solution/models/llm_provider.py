# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


# Remote-API provider types only. Self-hosted models are controlled by the
# `is_local` boolean — the dispatcher routes is_local=True records through
# `_call_local_provider`, otherwise it picks the adapter by provider_type.
PROVIDER_TYPES = [
    ("openai", "OpenAI (GPT-4o, GPT-4-turbo, …)"),
    ("anthropic", "Anthropic (Claude)"),
    ("gemini", "Google Gemini"),
    ("deepseek", "DeepSeek (deepseek-chat, deepseek-reasoner)"),
    ("xai", "xAI (Grok)"),
    ("openai_compat", "Other / OpenAI-compatible (Groq, Together, OpenRouter, …)"),
]

# Short brand names for `display_name`. The selection labels above are
# deliberately verbose — they teach a new admin which brand is which while
# they pick from the dropdown — but "Fast (OpenAI (GPT-4o, GPT-4-turbo, …))"
# is unreadable as a record name, so the picker uses these instead.
PROVIDER_SHORT_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "xai": "Grok",
    "openai_compat": "OpenAI-compatible",
}

# Default base URL per provider type. When set, the user does NOT need to
# fill in Base URL — the adapter falls back to this. Only 'openai_compat'
# has no default (the endpoint is genuinely caller-specific).
PROVIDER_DEFAULT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


class DigiErpAiModel(models.Model):
    _name = "digi_erp.model.llm.provider"
    _description = "DIGI-ERP AI — AI Model"
    _order = "name asc, id asc"

    name = fields.Char(
        required=True,
        help="The name users pick from in the chat (e.g. 'Fast', 'Best quality', 'Claude').",
    )
    # Top-level switch: self-hosted vs remote API. Drives the form view
    # visibility. NOTE it is NOT written back into provider_type — there is
    # no onchange doing that, despite what an earlier comment here claimed.
    # Readers normalise instead: `to_public_dict` reports 'local' when this
    # is set, and the transport dispatch tests `is_local` first.
    #
    # Defaults to OFF: a cloud API is what most installs connect first (it
    # needs nothing but a key), while a self-hosted model is the deliberate
    # choice of someone who already runs one. Keeping it False also lines the
    # default up with provider_type's own 'openai' default, so a brand-new
    # record is internally consistent before anyone touches it.
    is_local = fields.Boolean(
        string="Local model",
        default=False,
        help="ON: connect to a model you host yourself, on this machine or "
             "on your network — no API key needed.\n"
             "OFF: use a provider's API — pick which one in the connection "
             "section below.",
    )
    provider_type = fields.Selection(
        PROVIDER_TYPES,
        string="API provider",
        default="openai",
        help="Which remote API to call when this provider is not local. "
             "Ignored when 'Local model' is on.",
    )
    # `groups=` is what actually enforces the promise in the help text. A
    # `password="True"` widget only masks the input: the value still travels
    # in the web_read payload and is readable from the network tab. With a
    # field-level group the ORM strips it server-side for everyone else.
    #
    # Consequence to respect: every runtime read of this field must be
    # sudo()'d, or the chat would raise AccessError for plain users. See
    # controllers.main._resolve_provider / list_providers.
    api_key = fields.Char(
        string="API key",
        groups="digi_erp_ai_solution.group_ai_manager",
        help="The key issued by your AI provider. Stored in the database and "
             "readable only by DIGI-ERP AI Administrators — a plain user can "
             "select this model in the chat but never sees or edits the key.",
    )
    model_name = fields.Char(
        string="Model",
        help="The model name given by your provider. Examples:\n"
             "• OpenAI: gpt-4o, gpt-4o-mini\n"
             "• Anthropic: claude-sonnet-4-6, claude-opus-4-8\n"
             "• Google: gemini-2.0-flash\n"
             "• DeepSeek: deepseek-chat, deepseek-reasoner\n"
             "• xAI: grok-2-latest\n"
             "• Self-hosted: the name your own server knows it by "
             "(e.g. llama3.2, qwen2.5:7b).",
    )
    base_url = fields.Char(
        string="Base URL",
        help="API endpoint. Leave blank to use the standard URL of the "
             "selected provider — you only need to fill it in for a custom / "
             "self-hosted endpoint (Groq, Together, OpenRouter, or a local "
             "self-hosted server on another machine, …).",
    )
    supports_tools = fields.Boolean(
        string="Supports tools",
        default=True,
        help="If checked, Odoo tools (search, create records) will be sent "
             "to this provider. Uncheck for chat-only models.",
    )
    supports_vision = fields.Boolean(
        string="Can read images (OCR / vision)",
        default=False,
        help="Check this for multimodal models that can look at an image "
             "(e.g. qwen2.5vl, gpt-4o, claude, gemini, glm-4v). When this "
             "model answers a chat and no dedicated OCR model is set in "
             "Settings, an uploaded image is sent straight to it WITH the "
             "question — it reads the image and answers in a single call, "
             "no separate OCR step. Leave unchecked for text-only models "
             "(e.g. deepseek-chat): those fall back to the classic OCR "
             "engine (PaddleOCR/Surya) to turn the image into text first.",
    )
    active = fields.Boolean(default=True)

    # The kanban subtitle used to print `provider_type` itself, which renders
    # the full selection label — "DeepSeek (deepseek-chat, deepseek-reasoner)"
    # — and was clipped to "DeepSeek (Deepseek-Chat, Deepseek-Reas…" on the
    # card. This is the same short brand name `display_name` uses.
    provider_label = fields.Char(
        string="Provider",
        compute="_compute_provider_label",
        help="Short brand name of the connection, for compact displays.",
    )

    @api.depends("provider_type", "is_local")
    def _compute_provider_label(self):
        for rec in self:
            if rec.is_local:
                rec.provider_label = _("Self-hosted")
            else:
                rec.provider_label = PROVIDER_SHORT_LABELS.get(
                    rec.provider_type, rec.provider_type or "")

    # ── Auto-routing tier ────────────────────────────────────────────────────
    # When the user picks "Auto" in the chat dropdown, DIGI-ERP AI classifies
    # the prompt and looks up the first ACTIVE provider whose tier matches.
    # When the user picks a specific provider, this field is ignored — the
    # picked provider is always used.
    auto_route_tier = fields.Selection(
        [
            ("lightweight", "Quick questions — greetings and short answers"),
            ("balanced",    "Everyday work — questions about your data"),
            ("heavy",       "Complex work — analysis, reports, creating records"),
            ("image",       "Image generation"),
        ],
        string="Use this model for",
        help="What the assistant should send to this model when a user leaves "
             "the chat picker on Auto. You do not need one model per line: a "
             "complex-work model also covers everyday work, and an everyday "
             "model also covers quick questions, so a single model can handle "
             "everything. Leave empty to keep this model out of Auto — it "
             "stays selectable by hand in the chat.",
    )

    # ── Per-provider tuning (used in addition to model_name) ─────────────────

    temperature = fields.Float(
        string="Temperature",
        default=0.2, digits=(3, 2),
        help="Sampling temperature. 0.0 = deterministic, 1.0 = creative. "
             "0.1–0.3 is recommended for structured extraction.",
    )
    max_tokens = fields.Integer(
        string="Max output tokens",
        default=0,
        help="Maximum number of tokens the model is allowed to generate. "
             "0 = provider default (OpenAI/Anthropic typically cap at 4096; "
             "a self-hosted model generates until the context window is "
             "exhausted).",
    )
    timeout_seconds = fields.Integer(
        string="Timeout (s)",
        default=0,
        help="HTTP timeout for calls to this provider. 0 = use the global "
             "timeout from Settings → DIGI-ERP AI.",
    )

    # ── Self-hosted models only ──────────────────────────────────────────────

    num_ctx = fields.Integer(
        string="Context window",
        default=0,
        help="Context window in tokens for this self-hosted model. 0 = use "
             "the global value from Settings → DIGI-ERP AI. Override per "
             "model if you need more room for long documents — many models "
             "support 128k or more.",
    )
    keep_alive = fields.Char(
        string="Keep alive",
        default="",
        help="How long the local server keeps this model loaded in memory "
             "after a request. Examples: '30m', '1h', '-1' to keep it "
             "loaded, '0' to unload immediately. Blank = the server's own "
             "default.",
    )

    # Resolved endpoint of a LOCAL model, shown as a clickable link on the
    # kanban card. base_url is often left blank (the adapter then falls back
    # to the global server URL from Settings), so reading the raw field would
    # show nothing for the most common setup — we resolve the effective URL
    # here instead. Empty for remote providers: their endpoint is the vendor's
    # API, not something the user can usefully open in a browser.
    local_url = fields.Char(
        string="Local endpoint",
        compute="_compute_local_url",
        help="The endpoint this self-hosted model actually answers on — "
             "either this record's Base URL or, when that is blank, the "
             "global one from Settings → DIGI-ERP AI.",
    )

    @api.depends("is_local", "base_url")
    def _compute_local_url(self):
        global_url = ""
        for rec in self:
            if not rec.is_local:
                rec.local_url = ""
                continue
            if rec.base_url:
                rec.local_url = rec.base_url.strip()
                continue
            # Resolve the global fallback once per recordset, not per record.
            if not global_url:
                from ..services.ai_config import get_ai_config
                global_url = (get_ai_config(rec.env) or {}).get(
                    "local_base_url") or ""
            rec.local_url = global_url

    # Providers are shared across all DIGI-ERP AI users, so names must be
    # globally unique or the chat picker would show duplicates.
    _name_uniq = models.Constraint(
        "unique(name)",
        "An AI model with this name already exists.",
    )

    @api.constrains("is_local", "api_key", "model_name")
    def _check_required(self):
        for rec in self:
            # Every provider needs a model identifier so the chat can route
            # to a specific model. For a self-hosted model this is the tag
            # it was pulled under (e.g. 'llama3.2'); for a remote one it is
            # the provider's model id (e.g. 'gpt-4o').
            if not rec.model_name:
                raise ValidationError(
                    "A model identifier is required (e.g. 'gpt-4o', "
                    "'llama3.2', 'qwen2.5vl:3b')."
                )
            # API key is only mandatory for remote providers; a self-hosted server
            # has no auth so api_key stays empty.
            if not rec.is_local and not rec.api_key:
                raise ValidationError(
                    "An API key is required for remote API providers."
                )

    # Odoo 19 dropped `name_get`: `display_name` is a real stored-less field
    # computed by `_compute_display_name`, and nothing in core calls the old
    # hook any more. The previous override here was dead code — the picker
    # was meant to read "Fast (OpenAI)" and silently showed "Fast".
    @api.depends("name", "provider_type", "is_local")
    def _compute_display_name(self):
        for rec in self:
            if rec.is_local:
                kind = _("self-hosted")
            else:
                kind = PROVIDER_SHORT_LABELS.get(rec.provider_type,
                                                 rec.provider_type)
            rec.display_name = f"{rec.name} ({kind})" if kind else rec.name

    # ── Connection test ───────────────────────────────────────────────────────

    def action_test_connection(self):
        """Send a tiny 'ping' completion to the configured provider and report
        whether it answered, so anyone can check that a model is reachable
        before relying on it in the chat.

        Open to any DIGI-ERP AI user, deliberately. The result is a yes/no plus
        the model's own reply — it never echoes the key, and being able to say
        "the assistant is down" without waiting for an administrator is the
        whole point of the button.

        The `sudo()` below is what makes that safe AND possible: `api_key` is
        restricted to group_ai_manager, so a plain user reading it through
        `self` would raise AccessError. We read it as superuser purely to test
        for its PRESENCE and to let the transport send it; the value never
        reaches the caller."""
        self.ensure_one()
        from ..services.llm_providers import call_chat

        if not self.model_name:
            return self._test_notification(
                False, _("Set a model identifier first."))
        if not self.is_local and not self.sudo().api_key:
            return self._test_notification(
                False, _("This remote provider needs an API key."))

        # English on purpose: the ping goes to the MODEL, not to a person, and
        # every model handles English. It is not a translatable string.
        messages = [{"role": "user", "content": "Ping. Reply with only: pong."}]

        if self.is_local or self.provider_type not in dict(PROVIDER_TYPES):
            # Self-hosted — route through the same low-level dispatcher the
            # chat uses, so the test exercises the real path.
            try:
                from ..controllers.main import _call_local_provider
                result = _call_local_provider(self, messages, tools=None)
            except Exception as exc:  # pragma: no cover - defensive
                return self._test_notification(False, str(exc))
        else:
            result = call_chat(self, messages, tools=None)

        if result and result.get("ok"):
            reply = ((result.get("message") or {}).get("content") or "").strip()
            preview = (reply[:120] + "…") if len(reply) > 120 else reply
            return self._test_notification(
                True, _("Connected. The model answered: %s",
                        preview or _("(empty)")))
        error = (result or {}).get("error") or _("Unknown error")
        return self._test_notification(False, _("Failed: %s", error))

    def _test_notification(self, ok, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": ("✅ %s" % self.name) if ok else ("❌ %s" % self.name),
                "message": message,
                "type": "success" if ok else "danger",
                "sticky": not ok,
            },
        }

    # ── Public serialization (used by the JS dropdown) ────────────────────────

    def to_public_dict(self):
        """Safe dict for the chat UI — never includes the API key."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "is_local": self.is_local,
            # provider_type stays only for branding/colour in the dropdown;
            # it's meaningless when is_local is True.
            "provider_type": "local" if self.is_local else self.provider_type,
            "model_name": self.model_name or "",
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "auto_route_tier": self.auto_route_tier or "",
        }
