# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class DigiErpAiConversation(models.Model):
    _name = "digi_erp.model.chat.conversation"
    _description = "DIGI-ERP AI — Conversation"
    _order = "last_message_at desc, id desc"

    # NOTE the default is a plain lambda, not a bare string: `_()` at module
    # import time would freeze the English text into the column default. This
    # way each new conversation is named in the CREATING USER's language.
    name = fields.Char(
        string="Title",
        default=lambda self: _("New conversation"),
        required=True,
    )
    # True until the thread has been auto-named from its first user message
    # (or renamed by hand). Replaces the old `name == "Nouvelle conversation"`
    # string comparison, which broke the moment the default was translated —
    # a French UI would never have auto-named anything once the literal moved
    # to English, and vice versa. A flag is language-independent.
    is_unnamed = fields.Boolean(
        string="Awaiting a title",
        default=True,
        help="Set until the conversation has been named from its first "
             "message or renamed by the user.",
    )
    user_id = fields.Many2one(
        "res.users", string="User",
        required=True, ondelete="cascade",
        default=lambda self: self.env.user,
        index=True,
    )
    message_ids = fields.One2many(
        "digi_erp.model.chat.message", "conversation_id", string="Messages",
    )
    # NB the label must differ from message_ids' ("Messages") — two fields on
    # one model sharing a label makes Odoo log a warning at install.
    message_count = fields.Integer(
        string="Message count", compute="_compute_message_count")
    last_message_at = fields.Datetime(string="Last message", index=True)

    # Where the conversation was started. 'portal' = the full-page chat at
    # /my/ai-solution; 'embedded' = the context-aware sidebar shown across
    # the Odoo backend. Both share this model so history is unified, but the
    # source lets each UI list its own threads and lets us tag embedded
    # threads with the screen context they were born on.
    source = fields.Selection(
        [("portal", "Full-page chat"),
         ("embedded", "Embedded sidebar")],
        default="portal", required=True, index=True,
    )

    # Auto-router stickiness. Once this conversation has used a model of
    # tier T (lightweight < balanced < heavy), every subsequent turn
    # must use a model of tier >= T. Prevents the classifier from
    # silently demoting mid-thread when a short follow-up like
    # "name and email" would, on its own, have been routed to a smaller
    # model. We promote freely (a harder follow-up bumps the ceiling)
    # but never demote inside the same conversation.
    auto_tier_ceiling = fields.Selection(
        [("lightweight", "Lightweight"),
         ("balanced",    "Balanced"),
         ("heavy",       "Heavy")],
        string="Auto tier ceiling",
        help="Highest auto-routed tier ever used in this conversation. "
             "The Auto picker never goes below this value on a follow-up "
             "turn — keeps a thread on the same (or stronger) model.",
    )

    # ── Schema-RAG cache (per conversation) ─────────────────────────────
    # The schema block injected into the system prompt (see
    # controllers.main._build_system_prompt / services.schema_rag) is
    # expensive to build: it re-serialises field lists for ~28 models via
    # N `ir.model.fields` reads, plus an optional 7B re-rank LLM call —
    # EVERY turn, even when a thread stays on the same business topic. We
    # cache the assembled block here and reuse it on a follow-up turn when
    # the newly-retrieved model set is a subset of the cached one (a cheap
    # single query-embed still validates, so correctness is preserved).
    #   • schema_cache_models — CSV of the model technical names the cached
    #     block covers. Used to test "did the topic drift?".
    #   • schema_cache_block  — the serialised block text, ready to inject.
    schema_cache_models = fields.Char(
        string="Cached schema models",
        help="CSV of model names the cached schema block covers.",
    )
    schema_cache_block = fields.Text(
        string="Cached schema block",
        help="Serialised schema-RAG block reused across turns while the "
             "conversation stays on the same models.",
    )

    @api.depends("message_ids")
    def _compute_message_count(self):
        for conv in self:
            conv.message_count = len(conv.message_ids)

    def _promote_auto_tier(self, new_tier):
        """Bump `auto_tier_ceiling` if `new_tier` is strictly stronger
        than what's already recorded. Called by the Auto router after
        every pick."""
        self.ensure_one()
        rank = {"lightweight": 0, "balanced": 1, "heavy": 2}
        cur = rank.get(self.auto_tier_ceiling, -1)
        new = rank.get(new_tier, -1)
        if new > cur:
            self.sudo().auto_tier_ceiling = new_tier

    # Odoo 19 has no `name_get`: `display_name` is a real field computed by
    # `_compute_display_name`, and nothing in core calls the old hook — an
    # override there is simply never run.
    @api.depends("name")
    def _compute_display_name(self):
        for conv in self:
            conv.display_name = conv.name or _("Conversation #%s", conv.id)



class DigiErpAiMessage(models.Model):
    _name = "digi_erp.model.chat.message"
    _description = "DIGI-ERP AI — Message"
    _order = "id asc"

    conversation_id = fields.Many2one(
        "digi_erp.model.chat.conversation",
        required=True, ondelete="cascade", index=True,
    )
    user_id = fields.Many2one(
        related="conversation_id.user_id", store=True, index=True,
    )
    role = fields.Selection(
        [
            ("user", "User"),
            ("assistant", "Assistant"),
            ("tool_call", "Tool call"),
            ("tool_result", "Tool result"),
            ("system", "System"),
        ],
        required=True,
    )
    content = fields.Text(required=True)
    tool_name = fields.Char()
    # Compact JSON receipt of the tool calls that produced an assistant answer
    # (see controllers.main._summarize_tool_calls). Lets the chat UI re-render
    # the "what I did" activity chip after a page reload without replaying the
    # heavy tool_result rows. Only set on `assistant` messages.
    activity_json = fields.Text(
        string="Activity receipt",
        help="Serialised compact summary of the tool calls behind this "
             "answer, shown as an activity chip in the chat UI.",
    )
    # Friendly name of the model that produced this answer (e.g. "Claude
    # work", "Self-hosted"). Shown next to the timestamp so the user can tell
    # which model replied — especially useful in Auto mode, where the router
    # picks a different provider per message. Only set on `assistant` rows.
    model_label = fields.Char(
        string="Answered by",
        help="Display name of the AI model that generated this answer.",
    )
    attachment_filename = fields.Char()
    # The actual file the user uploaded with this message. Stored as an
    # ir.attachment so the chat UI can re-display it (preview / download)
    # both immediately and after the conversation is reloaded.
    attachment_id = fields.Many2one(
        "ir.attachment", string="Attached file",
        ondelete="set null", index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        msgs = super().create(vals_list)
        # Bump last_message_at so the conversation list re-orders correctly.
        for conv in msgs.mapped("conversation_id"):
            conv.last_message_at = fields.Datetime.now()
            # Auto-name a new conversation from its first user message.
            if conv.is_unnamed:
                first_user_msg = conv.message_ids.filtered(lambda m: m.role == "user")[:1]
                if first_user_msg:
                    title = (first_user_msg.content or "").strip().splitlines()[0]
                    if title:
                        conv.name = (title[:60] + "…") if len(title) > 60 else title
                        conv.is_unnamed = False
        return msgs
