# -*- coding: utf-8 -*-
"""DIGI-ERP AI — settings page (Settings → DIGI-ERP AI).

Stores every LLM configurable as an ir.config_parameter so admins
can change URL, model name, timeout, and context window without redeploying.

The actual lookup happens through ``services.ai_config.get_ai_config(env)``,
which is what the controller and services call at request time.
"""
from odoo import fields, models


class DigiErpAiConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── Built-in local AI server ─────────────────────────────────────────────
    digi_erp_ai_local_base_url = fields.Char(
        string="Local AI server URL",
        config_parameter="digi_erp_ai.local_base_url",
        default="http://localhost:11434",
        help="Root URL of the self-hosted AI server used by the built-in "
             "option. Examples:\n"
             "  • http://localhost:11434  (running on the Odoo host)\n"
             "  • http://192.168.1.42:11434  (another machine on the LAN)\n"
             "No trailing slash, no /api suffix.\n"
             "The built-in path posts to /api/chat, /api/generate and "
             "/api/embed on this server, so it must expose those endpoints. "
             "For any other service — OpenAI, Claude, Gemini, Groq, "
             "OpenRouter or any OpenAI-compatible API — leave this alone and "
             "add an entry under AI Models instead.",
    )

    digi_erp_ai_chat_model = fields.Char(
        string="Chat model",
        config_parameter="digi_erp_ai.chat_model",
        default="llama3.2",
        help="Name of the model that answers, as the local server knows it. "
             "It must already be downloaded there. "
             "Examples: llama3.2, qwen2.5:7b, qwen2.5vl:3b.",
    )

    digi_erp_ai_embed_model = fields.Char(
        string="Embedding model",
        config_parameter="digi_erp_ai.embed_model",
        default="nomic-embed-text",
        help="Model used to index your Odoo data structure, so the "
             "assistant only looks at the parts relevant to each question. "
             "It must be downloaded on the local AI server.",
    )

    # ── HTTP / context tuning ────────────────────────────────────────────────
    digi_erp_ai_request_timeout = fields.Integer(
        string="Request timeout (seconds)",
        config_parameter="digi_erp_ai.request_timeout",
        default=500,
        help="How long to wait for an answer before giving up. A local "
             "server can need 500 s or more on big documents.",
    )

    digi_erp_ai_num_ctx = fields.Integer(
        string="Context window (tokens)",
        config_parameter="digi_erp_ai.num_ctx",
        default=32768,
        help="How much the model can hold in mind at once: the question, the "
             "data it looked up, and the conversation so far. 32768 leaves "
             "room for a large data structure plus a long document without "
             "truncation; many models support far more. Raise it if answers "
             "come back cut off.",
    )

    # ── Model routing: RAG → 7B re-rank pre-pass ─────────────────────────────
    digi_erp_ai_rerank_enabled = fields.Boolean(
        string="Re-rank retrieved models with a light model",
        config_parameter="digi_erp_ai.rerank_enabled",
        default=False,
        help="Before the main call, ask a small, cheap model which of the "
             "retrieved Odoo models are actually relevant, and show only "
             "those to the main model. Slightly slower, more precise on busy "
             "databases. Pick the model below, or leave it empty to use the "
             "first active model set to 'Quick questions'; with neither, this "
             "toggle does nothing.",
    )
    digi_erp_ai_rerank_provider_id = fields.Many2one(
        "digi_erp.model.llm.provider",
        string="Model used for the narrowing step",
        config_parameter="digi_erp_ai.rerank_provider_id",
        help="Which model performs the narrowing pass. Leave empty to fall "
             "back on the first active model whose 'Use this model for' is "
             "'Quick questions'. Pick a small, fast model: this runs before "
             "every data question, so a heavy one cancels out the speed the "
             "step is meant to buy.",
    )

    # ── OCR: a dedicated AI model that reads documents/images ───────────────
    # When set, THIS provider does the OCR (image → text), independently of
    # whichever model answers in the chat. When left empty, OCR falls back
    # to the model selected in the chat: if that model is vision-capable the
    # image + prompt are sent to it in ONE call (it reads AND answers, no
    # separate OCR step); otherwise the classic OCR engine below is used.
    digi_erp_ai_ocr_provider_id = fields.Many2one(
        "digi_erp.model.llm.provider",
        string="OCR AI model",
        config_parameter="digi_erp_ai.ocr_provider_id",
        domain="[('supports_vision', '=', True)]",
        help="A model dedicated to reading uploaded documents and images — "
             "cloud (GPT-4o, Claude, Gemini) or self-hosted (e.g. qwen2.5vl). "
             "When set, it turns pages into text and the chat model then "
             "works on that text. Leave empty to let the chat model handle "
             "images itself when it can see them.",
    )

    # ── OCR: which engine leads the document-reading chain ──────────────────
    digi_erp_ai_ocr_primary = fields.Selection(
        selection=[
            ("llm_vision", "AI vision model — nothing to install, needs a "
                           "model that can read images"),
            ("paddleocr", "PaddleOCR — fast on printed text "
                          "(must be installed on your server)"),
            ("surya", "Surya — best on tables and multi-column layouts "
                      "(must be installed on your server)"),
            ("tesseract", "Tesseract — classic OCR "
                          "(must be installed on your server, with its "
                          "language packs)"),
        ],
        string="Document reader",
        config_parameter="digi_erp_ai.ocr_primary",
        default="llm_vision",
        help="Which reader turns uploaded documents and images into text "
             "FIRST. The others stay available as automatic fallbacks if the "
             "first one returns too little text, errors, or isn't installed.\n"
             "'AI vision model' sends the pages straight to a vision model "
             "and needs nothing installed on the server. The three classic "
             "engines are faster and run offline, but each has to be "
             "installed on the Odoo server before it can be selected here.",
    )

    # ── Embedding endpoint (defaults to the same base as the chat) ──────────
    digi_erp_ai_embed_base_url_override = fields.Char(
        string="Embeddings base URL (optional)",
        config_parameter="digi_erp_ai.embed_base_url",
        help="Override only if your embedding server runs on a different "
             "host than the chat server. Leave blank to reuse the chat URL.",
    )
