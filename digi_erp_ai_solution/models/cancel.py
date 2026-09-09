# -*- coding: utf-8 -*-
"""Cross-worker cancellation signal for in-flight chat turns.

Problem: a chat turn (POST /ai-solution/ask) runs inside ONE Odoo worker and
can stay busy for a long time — the tool loop iterates and, above all, the
call to the local model blocks while it generates. When the user
clicks Stop, the browser aborts its socket, but that worker never notices and
keeps the model running to completion.

The Stop click therefore fires a SECOND, tiny request (POST
/ai-solution/cancel) which is handled by a DIFFERENT worker/process. The only
channel those two processes reliably share is the database. This model is that
channel — a one-row-per-conversation "cancel mailbox".

The running turn peeks at the mailbox between stream chunks and between
tool-loop iterations (see `is_cancelled`). The moment it finds its own
conversation flagged, it hangs up on the server (closing the socket stops the
model) and returns an "interrupted" result.

Critical detail: both `request_cancel` and `clear` COMMIT in their own short
transaction. Without the commit, the flag would sit uncommitted in the cancel
request's transaction and the busy worker's fresh SELECT would never see it.
"""
from odoo import fields, models


class DigiErpAiChatCancel(models.Model):
    _name = "digi_erp.model.chat.cancel"
    _description = "DIGI-ERP AI — Cancellation signal (cross-worker mailbox)"

    conversation_id = fields.Many2one(
        "digi_erp.model.chat.conversation",
        required=True, ondelete="cascade", index=True,
    )
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True,
    )
    requested_at = fields.Datetime(default=fields.Datetime.now)

    # One live cancel request per conversation is all we ever need.
    _conversation_uniq = models.Constraint(
        "unique(conversation_id)",
        "A cancel signal already exists for this conversation.",
    )

    # ── Producer side (the /cancel request) ──────────────────────────────────

    def request_cancel(self, conversation_id, user_id):
        """Drop a cancel note for `conversation_id` and COMMIT immediately so
        the busy worker running that turn can see it on its next poll.

        Idempotent: a duplicate note for a conversation already flagged is a
        no-op (the unique constraint would raise; we swallow it)."""
        if not conversation_id:
            return False
        # Use a raw INSERT ... ON CONFLICT DO NOTHING so a second Stop click
        # (or a race with the first) can't raise. `create()` would hit the
        # Python-level unique constraint and roll back the whole cursor.
        self.env.cr.execute(
            """
            INSERT INTO digi_erp_model_chat_cancel
                (conversation_id, user_id, requested_at,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, now() at time zone 'UTC',
                    %s, %s, now() at time zone 'UTC', now() at time zone 'UTC')
            ON CONFLICT (conversation_id) DO NOTHING
            """,
            (conversation_id, user_id, self.env.uid, self.env.uid),
        )
        # Commit in this request's own transaction so the OTHER worker sees it.
        self.env.cr.commit()
        return True

    # ── Consumer side (the running /ask turn) ────────────────────────────────

    def is_cancelled(self, conversation_id):
        """Fresh read: has a cancel been requested for this conversation?

        Runs a direct SQL SELECT rather than the ORM so it always hits the
        database (no stale ORM cache) — the flag was written and committed by
        a *different* transaction, so we must bypass any in-memory cache."""
        if not conversation_id:
            return False
        self.env.cr.execute(
            "SELECT 1 FROM digi_erp_model_chat_cancel "
            "WHERE conversation_id = %s LIMIT 1",
            (conversation_id,),
        )
        return bool(self.env.cr.fetchone())

    def clear(self, conversation_id):
        """Remove the cancel note (turn finished, whether normally or aborted).
        Commits so the row is gone before the next turn on this conversation."""
        if not conversation_id:
            return
        self.env.cr.execute(
            "DELETE FROM digi_erp_model_chat_cancel WHERE conversation_id = %s",
            (conversation_id,),
        )
        self.env.cr.commit()
