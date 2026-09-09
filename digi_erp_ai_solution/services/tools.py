# -*- coding: utf-8 -*-
"""Boîte à outils générique d'accès à Odoo pour DIGI-ERP AI.

Pas de helpers métier figés : l'IA choisit elle-même le modèle Odoo et
construit son domain. Toutes les requêtes traversent `request.env` —
l'utilisateur connecté → Odoo applique ses ACL / record rules / champs
restreints automatiquement.
"""
import base64
import logging

from odoo import _
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# ── ANSI colours for console tracing ─────────────────────────────────────────
_C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "cyan":   "\033[96m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "blue":   "\033[94m",
    "magenta":"\033[95m",
}

def _cprint(color, label, msg=""):
    # Routed through the Odoo logger so output lands in the configured
    # logfile. A bare print() would only reach stdout/journald, which
    # split DIGI-ERP AI output across two places on a prod deployment.
    # `color` is kept for call-site compatibility but is no longer used.
    _logger.info("[%s] %s", label, msg)

# Cap on the size of a tool result that gets fed BACK to the model
# as `tool` message content for the next iteration. If the tool
# returned 100 KB of rows and we only hand back 8 KB, the model only
# "sees" the first slice and its final answer only covers what it saw
# — which is why users complain "it only listed a few records".
#
# 32000 chars is a balance: enough to cover ~150-200 typical Odoo rows
# (name + a handful of fields) without bloating context so much that
# small models stall on prefill. Increase further if your typical
# queries return more rows; decrease if you're on very constrained
# hardware and stalls become a problem.
MAX_TOOL_RESULT_LEN = 32000

# Méthodes interdites par la passerelle, même si Odoo les permettrait pour
# l'utilisateur. Filet de sécurité pour qu'un modèle ne supprime pas en masse.
DENIED_METHODS = {"unlink", "execute", "execute_kw", "exec_workflow"}


# ── Confirmation gate : classification des appels qui MODIFIENT la base ──────
#
# Toute écriture proposée par l'IA (create / write / action de bouton qui change
# l'état d'un enregistrement) doit passer par une confirmation humaine AVANT
# exécution. On ne veut PAS geler les lectures ni la génération de fichiers.
#
# Méthodes de mutation "directe" (champ → valeur) : on peut calculer un avant/
# après précis.
_MUTATING_FIELD_METHODS = {"create", "write", "copy"}

# Méthodes-boutons qui changent l'état métier d'un enregistrement (poste une
# facture, confirme une commande, valide un règlement…). Impossible d'en tirer
# un diff champ-par-champ fiable, mais l'utilisateur doit quand même approuver.
# La liste est indicative : le préfixe `action_` / `button_` attrape le reste.
_STATE_CHANGE_METHODS = {
    "action_post", "action_confirm", "action_done", "action_validate",
    "action_cancel", "action_draft", "action_approve", "action_refuse",
    "button_confirm", "button_validate", "button_draft", "button_cancel",
    "post", "confirm", "validate", "approve", "reconcile", "action_invoice_sent",
}


def is_write_call(name, params):
    """True si l'appel d'outil `name`/`params` va MODIFIER des données Odoo.

    Seul `odoo_call` peut écrire (les autres outils lisent ou produisent des
    fichiers). On regarde donc la `method` demandée : mutation de champs,
    méthode d'état connue, ou préfixe `action_`/`button_` (hors lecture pure).
    """
    if name != "odoo_call":
        return False
    method = (params.get("method") or "").strip()
    if not method or method.startswith("_"):
        return False
    if method in _MUTATING_FIELD_METHODS or method in _STATE_CHANGE_METHODS:
        return True
    # Boutons de vue génériques : action_xxx / button_xxx changent quasi
    # toujours l'état. On les gate par prudence (l'utilisateur confirme).
    if method.startswith(("action_", "button_", "toggle_", "set_")):
        return True
    return False


# Champs de plomberie qu'on n'affiche pas dans un aperçu avant/après : ils
# n'ont pas de sens métier pour l'utilisateur qui confirme.
_PREVIEW_HIDDEN_FIELDS = {
    "id", "create_uid", "create_date", "write_uid", "write_date",
    "__last_update", "display_name",
}


def _humanize_field_label(env, model, fname):
    """A user-friendly label for a field — NEVER its technical name.

    Goes through `fields_get`, not `_fields[fname].string`: the latter is the
    label as written in the Python source and is NOT translated, so a French
    or Arabic user was reading English field names in an otherwise translated
    confirmation card. `fields_get` applies the env's language.

    If the field is unknown or has no label, `amount_total` becomes
    "Amount total", so the card never shows a raw technical identifier.
    """
    try:
        described = env[model].fields_get([fname], ["string"]).get(fname) or {}
        if described.get("string"):
            return described["string"]
        fld = env[model]._fields.get(fname)
        if fld is not None and fld.string:
            return fld.string
    except Exception:
        pass
    return fname.replace("_id", "").replace("_", " ").strip().capitalize() or fname


def _preview_format_value(env, model, fname, value):
    """Rend une valeur de champ lisible pour l'humain dans l'aperçu.

    Résout les many2one (id → nom), aplati les many2many/o2m en compte, et
    laisse les scalaires tels quels. Jamais d'exception : l'aperçu ne doit
    pas faire échouer la confirmation."""
    try:
        field = env[model]._fields.get(fname)
        if field is None:
            return value
        ftype = field.type
        if ftype == "many2one":
            if not value:
                return "—"
            rec_id = value[0] if isinstance(value, (list, tuple)) else value
            try:
                rel = env[field.comodel_name].browse(int(rec_id))
                return f"{rel.display_name} (#{rec_id})"
            except Exception:
                return value
        if ftype in ("many2many", "one2many"):
            try:
                return _("%s item(s)", len(value))
            except Exception:
                return value
        if ftype == "boolean":
            return _("Yes") if value else _("No")
        return value
    except Exception:
        return value


def build_action_preview(env, params):
    """Construit un aperçu 'avant → après' pour un appel d'écriture proposé.

    Retourne un dict destiné au frontend :
        {
          "kind": "create" | "write" | "method",
          "model": "account.move",
          "model_label": "Journal Entries",
          "method": "write",
          "record_ids": [42],
          "summary": "Update 1 \u201cJournal Entries\u201d record",
          "changes": [                      # write/create : diff par champ
             {"field": "amount_total", "label": "Total",
              "before": "1 000,00", "after": "1 200,00"},
             ...
          ],
          "note": "…"                       # method : explication (pas de diff)
        }

    Best-effort et défensif : toute erreur retombe sur un aperçu minimal —
    la confirmation reste possible même si le diff n'a pas pu être calculé.
    """
    model = (params.get("model") or "").strip()
    method = (params.get("method") or "").strip()
    args = params.get("args") or []
    kwargs = params.get("kwargs") or {}

    model_label = model
    try:
        model_label = env["ir.model"]._get(model).name or model
    except Exception:
        pass

    base = {
        "model": model,
        "model_label": model_label,
        "method": method,
        "record_ids": [],
        "changes": [],
        "note": "",
    }

    # ── CREATE : pas d'"avant", on montre les valeurs qui seront écrites. ──
    if method == "create":
        vals_list = args[0] if args else kwargs.get("vals_list") or kwargs.get("values") or {}
        rows = vals_list if isinstance(vals_list, list) else [vals_list]
        changes = []
        for vals in rows:
            if not isinstance(vals, dict):
                continue
            for fname, value in vals.items():
                if fname in _PREVIEW_HIDDEN_FIELDS:
                    continue
                changes.append({
                    "field": fname,
                    "label": _humanize_field_label(env, model, fname),
                    "before": "—",
                    "after": _preview_format_value(env, model, fname, value),
                })
        n = len(rows)
        base.update({
            "kind": "create",
            "changes": changes,
            "summary": _("Create %(count)s \u201c%(model)s\u201d",
                         count=n, model=model_label) if n > 1
                       else _("Create a new \u201c%s\u201d", model_label),
        })
        return base

    # ── WRITE : on lit l'état actuel des ids ciblés puis on diffe. ──
    if method == "write":
        ids = args[0] if args else kwargs.get("ids") or []
        vals = args[1] if len(args) > 1 else (kwargs.get("vals") or kwargs.get("values") or {})
        if isinstance(ids, int):
            ids = [ids]
        ids = [int(i) for i in (ids or []) if str(i).lstrip("-").isdigit()]
        if not isinstance(vals, dict):
            vals = {}
        changes = []
        try:
            recs = env[model].browse(ids).exists()
        except Exception:
            recs = env[model].browse([])
        # Diff sur le PREMIER enregistrement (représentatif). Si plusieurs,
        # on le signale dans le résumé.
        sample = recs[:1]
        for fname, new_value in vals.items():
            if fname in _PREVIEW_HIDDEN_FIELDS:
                continue
            before = "—"
            try:
                if sample and fname in sample._fields:
                    before = _preview_format_value(env, model, fname, sample[fname])
            except Exception:
                pass
            changes.append({
                "field": fname,
                "label": _humanize_field_label(env, model, fname),
                "before": before,
                "after": _preview_format_value(env, model, fname, new_value),
            })
        names = ", ".join(recs[:3].mapped("display_name")) if recs else ""
        n = len(ids)
        if n == 1:
            target = names or (f"#{ids[0]}" if ids else "")
            summary = _("Update \u201c%(model)s\u201d: %(record)s",
                        model=model_label, record=target)
        else:
            summary = _("Update %(count)s \u201c%(model)s\u201d record(s)",
                        count=n, model=model_label)
        base.update({
            "kind": "write",
            "record_ids": ids,
            "changes": changes,
            "summary": summary,
        })
        return base

    # ── MÉTHODE D'ÉTAT (action_post, confirm…) : pas de diff champ, on
    #     explique l'effet et on liste les enregistrements touchés. ──
    ids = args[0] if args else kwargs.get("ids") or []
    if isinstance(ids, int):
        ids = [ids]
    ids = [int(i) for i in (ids or []) if str(i).lstrip("-").isdigit()]
    names = ""
    try:
        recs = env[model].browse(ids).exists()
        names = ", ".join(recs[:3].mapped("display_name"))
    except Exception:
        pass
    # Traduit le nom technique de la méthode en verbe métier lisible. Les
    # boutons courants ont un libellé dédié ; le reste est nettoyé du préfixe
    # action_/button_ et présenté en clair (jamais le nom technique brut).
    _METHOD_VERBS = {
        "action_post": _("Post"), "post": _("Post"),
        "action_confirm": _("Confirm"), "button_confirm": _("Confirm"),
        "confirm": _("Confirm"),
        "action_cancel": _("Cancel"), "button_cancel": _("Cancel"),
        "action_draft": _("Reset to draft"), "button_draft": _("Reset to draft"),
        "action_validate": _("Validate"), "button_validate": _("Validate"),
        "validate": _("Validate"),
        "action_approve": _("Approve"), "approve": _("Approve"),
        "action_refuse": _("Refuse"),
        "action_done": _("Mark as done"),
        "reconcile": _("Reconcile"),
    }
    verb = _METHOD_VERBS.get(method)
    if not verb:
        verb = method
        for pref in ("action_", "button_", "toggle_", "set_"):
            if verb.startswith(pref):
                verb = verb[len(pref):]
                break
        verb = verb.replace("_", " ").strip().capitalize() or _("Run the action")
    base.update({
        "kind": "method",
        "record_ids": ids,
        "summary": (_("%(verb)s: %(count)s \u201c%(model)s\u201d record(s)",
                      verb=verb, count=len(ids), model=model_label)
                    + (" — " + names if names else "")),
        "note": _("This action changes the state of the record and may be "
                  "hard to undo. Check it before confirming."),
    })
    return base


def _truncate(s, n=300):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + "…"


def _coerce_domain(d):
    """Le modèle peut envoyer un domain en JSON (listes au lieu de tuples).
    On normalise sans rejeter."""
    if d is None:
        return []
    if not isinstance(d, list):
        return []
    out = []
    for clause in d:
        if isinstance(clause, str):
            out.append(clause)  # opérateurs '&', '|', '!'
        elif isinstance(clause, (list, tuple)) and len(clause) == 3:
            out.append(tuple(clause))
        else:
            out.append(clause)
    return out


# ── Outils ────────────────────────────────────────────────────────────────────

def _whoami(env, params):
    u = env.user
    # In Odoo 19 the user-group field was renamed from `groups_id` to
    # `group_ids`. Fall back so we work on both versions.
    groups_field = (
        u.group_ids if hasattr(u, "group_ids")
        else u.groups_id if hasattr(u, "groups_id")
        else u.browse()
    )
    return {
        "id": u.id,
        "name": u.name,
        "login": u.login,
        "email": u.email,
        "company": u.company_id.name,
        "company_id": u.company_id.id,
        "groups": groups_field.mapped("full_name")[:50],
    }


def _odoo_models_search(env, params):
    """Cherche les modèles Odoo par mot-clé. Aide l'IA à trouver le bon model."""
    query = (params.get("query") or "").strip()
    limit = max(1, min(int(params.get("limit") or 20), 100))
    domain = []
    if query:
        domain = ["|", ("model", "ilike", query), ("name", "ilike", query)]
    rows = env["ir.model"].search_read(
        domain, ["model", "name", "info"], limit=limit, order="model",
    )
    return {"count": len(rows), "results": rows}


def _odoo_fields_get(env, params):
    """Schéma d'un modèle pour construire des requêtes correctes.

    Par défaut on NE renvoie PAS le `help` (souvent plusieurs lignes par
    champ) : sur un modèle riche comme project.task le résultat explosait
    le contexte d'un petit modèle et le faisait boucler. On garde le
    strict nécessaire pour bâtir un domaine / choisir des champs. Le
    `help` reste disponible si l'appelant le demande explicitement via
    `attributes`."""
    model = (params.get("model") or "").strip()
    if not model:
        return {"error": "Parameter `model` is required."}
    if model not in env:
        return {"error": f"Unknown model: {model}"}
    attributes = params.get("attributes") or [
        "string", "type", "required", "selection", "relation",
    ]
    raw = env[model].fields_get(attributes=attributes)
    # Compact each field to a short token so the payload stays small:
    #   "stage_id": "many2one→project.task.type"
    #   "state":    "selection[draft,done]"
    #   "name":     "char (Titre)"
    compact = {}
    for fname, meta in raw.items():
        ttype = meta.get("type", "")
        if meta.get("relation"):
            desc = f"{ttype}→{meta['relation']}"
        elif meta.get("selection"):
            vals = ",".join(str(v[0]) for v in (meta.get("selection") or [])[:12])
            desc = f"selection[{vals}]"
        else:
            desc = ttype
        label = meta.get("string") or ""
        if label and label.lower() != fname.lower():
            desc = f"{desc} ({label})"
        if meta.get("required"):
            desc += " *required"
        compact[fname] = desc
    return {"model": model, "field_count": len(compact), "fields": compact}


# Field-name patterns that are almost never useful in a chat answer or a
# generated report, but bloat the payload (and the model's context) when
# search_read returns ALL fields. Dropped only when the model didn't ask
# for specific fields.
_NOISE_FIELD_PREFIXES = (
    "message_", "activity_", "website_message_", "__",
)
_NOISE_FIELD_EXACT = {
    "display_name", "create_uid", "create_date", "write_uid", "write_date",
    "__last_update", "access_token", "access_url", "access_warning",
    "my_activity_date_deadline", "activity_exception_decoration",
    "activity_exception_icon", "activity_state", "activity_summary",
    "rating_ids", "message_follower_ids", "message_partner_ids",
    "message_ids", "message_is_follower", "message_main_attachment_id",
}


def _strip_noise_fields(row):
    """Drop heavy/internal fields from a search_read row so the payload
    stays focused on real business data (see _odoo_search_read)."""
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if k == "id":
            out[k] = v
            continue
        if k in _NOISE_FIELD_EXACT:
            continue
        if any(k.startswith(p) for p in _NOISE_FIELD_PREFIXES):
            continue
        out[k] = v
    return out


# ── Secret redaction ────────────────────────────────────────────────────────
# Field-name patterns whose VALUE is a credential and must NEVER reach the LLM
# (which would then echo it into the chat, as it did with a live Anthropic key).
# Matched case-insensitively as a substring of the field name, so this catches
# `api_key`, `openai_api_key`, `smtp_password`, `secret_token`, etc. across ANY
# model — the redaction is at the read layer, not per-model.
_SECRET_FIELD_SUBSTRINGS = (
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "private_key", "client_secret", "access_key", "auth_key",
)
_SECRET_MASK = "••• (masked for security)"


def _redact_secrets(row):
    """Replace credential-like field VALUES with a mask so the model never
    sees (and never leaks) API keys, passwords or tokens. A non-empty secret
    becomes the mask; an empty/False one is passed through as-is so the model
    can still tell the user \"no key is set\"."""
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        lk = k.lower()
        if any(sub in lk for sub in _SECRET_FIELD_SUBSTRINGS):
            out[k] = _SECRET_MASK if v else v
        else:
            out[k] = v
    return out


# Longest a single string value may be before we truncate it, so one fat
# field (a long HTML description that slipped into an explicitly-requested
# column) can't inflate the payload. The model still gets exactly the FIELDS
# it asked for — only oversized VALUES are shortened.
_AUTO_VALUE_MAX_CHARS = 300


def _truncate_values(row):
    """Cap any oversized string value in a row so one fat field can't blow
    up the token count. Non-strings pass through untouched."""
    if not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and len(v) > _AUTO_VALUE_MAX_CHARS:
            out[k] = v[:_AUTO_VALUE_MAX_CHARS] + "… (truncated)"
        else:
            out[k] = v
    return out


def _odoo_search_read(env, params):
    """Lecture générique. Outil principal pour répondre aux questions.

    RÉSILIENT AUX CHAMPS INVALIDES : si le modèle demande des champs qui
    n'existent pas (ex. `progress`, `responsible_user` sur project.project),
    on les ÉCARTE silencieusement au lieu de lever une erreur. Sinon le
    LLM recevait une exception, n'avait aucune donnée, et FABRIQUAIT des
    enregistrements. On renvoie toujours des lignes réelles + la liste des
    champs ignorés pour que le modèle s'autocorrige."""
    model = (params.get("model") or "").strip()
    if not model:
        return {"error": "Parameter `model` is required."}
    if model not in env:
        return {"error": f"Unknown model: {model}"}
    domain = _coerce_domain(params.get("domain"))
    requested = params.get("fields") or []
    limit = params.get("limit")
    if limit is not None:
        limit = max(1, min(int(limit), 200))
    offset = max(0, int(params.get("offset") or 0))
    order = params.get("order") or None

    dropped = []
    fields = requested
    if requested:
        valid = set(env[model]._fields.keys())
        fields = [f for f in requested if (f.split(".")[0] in valid)]
        dropped = [f for f in requested if f not in fields]

    # No usable fields → DON'T fetch every column (that's the cost bomb on
    # data-heavy models). Ask the model to choose: return NO rows plus the
    # list of available field names so it re-calls with EXACTLY the fields it
    # needs — 1 or 10, its call, never capped. The model already knows the
    # schema (odoo_fields_get); this is just an explicit nudge when it forgot.
    if not fields:
        names = sorted(
            fn for fn in env[model]._fields
            if fn not in _NOISE_FIELD_EXACT
            and not any(fn.startswith(p) for p in _NOISE_FIELD_PREFIXES)
        )
        return {
            "model": model,
            "needs_fields": True,
            "available_fields": names,
            "note": (
                f"No field requested. Choose EXACTLY the fields this "
                f"answer needs (as many as you want: 1, 5 or 10) and call "
                f"odoo_search_read again with `fields`. Do not ask for "
                f"everything — take only what is useful. For a statistic or "
                f"aggregation, take the grouping field plus the measured "
                f"field. Exact types via odoo_fields_get({model})."
            ),
        }

    try:
        rows = env[model].search_read(
            domain, fields, limit=limit, offset=offset, order=order,
        )
    except Exception as e:
        # Last-resort: a bad order/domain field. Retry with no order but
        # keep the bounded auto-projection so we still don't dump all
        # columns; the model still gets REAL data, never nothing.
        _cprint("yellow", "SEARCH_READ RETRY",
                f"{type(e).__name__}: {e} — retrying without order")
        try:
            # Keep the model's chosen fields; only drop the (possibly bad)
            # order so it still gets exactly the columns it asked for.
            rows = env[model].search_read(domain, fields, limit=limit,
                                          offset=offset)
        except Exception as e2:
            return {"error": f"search_read failed: {type(e2).__name__}: {e2}"}

    # Truncate any oversized string value so a single fat field can't inflate
    # the payload, and mask credential-like values before they reach the LLM.
    if rows:
        rows = [_redact_secrets(_truncate_values(r)) for r in rows]

    out = {"model": model, "count": len(rows), "results": rows}
    if dropped:
        out["ignored_fields"] = dropped
        note = (
            f"Non-existent fields ignored on {model}: {dropped}. "
            f"Call odoo_fields_get({model}) for the exact names. "
            f"NEVER invent values for those fields."
        )
        out["note"] = (out.get("note", "") + " " + note).strip()
    return out


def _odoo_read(env, params):
    """Lecture d'enregistrements précis par ids."""
    model = (params.get("model") or "").strip()
    if not model:
        return {"error": "Parameter `model` is required."}
    if model not in env:
        return {"error": f"Unknown model: {model}"}
    ids = params.get("ids") or []
    if not isinstance(ids, list):
        return {"error": "`ids` must be a list of integers."}
    ids = [int(i) for i in ids]
    fields = params.get("fields") or []
    rows = env[model].browse(ids).read(fields)
    rows = [_redact_secrets(r) for r in rows]
    return {"model": model, "count": len(rows), "results": rows}


def _odoo_search_count(env, params):
    model = (params.get("model") or "").strip()
    if not model:
        return {"error": "Parameter `model` is required."}
    if model not in env:
        return {"error": f"Unknown model: {model}"}
    domain = _coerce_domain(params.get("domain"))
    return {"model": model, "count": env[model].search_count(domain)}


_MD_INLINE = __import__("re").compile(
    r"\*\*(?P<bold>[^*\n]+?)\*\*"
    r"|__(?P<bold2>[^_\n]+?)__"
    r"|(?<![\w*])\*(?P<italic>[^*\n]+?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<italic2>[^_\n]+?)_(?![\w_])"
    r"|`(?P<code>[^`\n]+?)`"
)


def _add_markdown_runs(paragraph, text):
    """Walk inline markdown (**bold**, *italic*, `code`) and emit styled
    runs on `paragraph` — so the markers don't leak as literal text."""
    pos = 0
    for m in _MD_INLINE.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        if m.group("bold") or m.group("bold2"):
            run = paragraph.add_run(m.group("bold") or m.group("bold2"))
            run.bold = True
        elif m.group("italic") or m.group("italic2"):
            run = paragraph.add_run(m.group("italic") or m.group("italic2"))
            run.italic = True
        elif m.group("code"):
            run = paragraph.add_run(m.group("code"))
            run.font.name = "Consolas"
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _gen_docx_blob(content):
    """Markdown-aware DOCX. The model's responses are typically
    Markdown (`### heading`, `**bold**`, `- bullets`, ```` ```code ``` ````)
    — we parse them so the .docx has real Word headings, bold/italic
    runs, bullet/numbered lists and monospaced code blocks instead of
    literal `*` and `#` characters."""
    import io as _io
    import re as _re
    from docx import Document
    doc = Document()
    in_code = False
    code_buf = []
    for raw in (content or "").split("\n"):
        line = raw.rstrip()
        # Triple-backtick fence
        if line.lstrip().startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_buf))
                run.font.name = "Consolas"
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue
        # Heading — # ... ######
        m = _re.match(r"^\s*(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            level = min(len(m.group(1)), 9)
            doc.add_heading(m.group(2), level=level)
            continue
        # Horizontal rule — --- / *** / ___
        if _re.match(r"^\s*([-*_])\s*\1\s*\1\s*$", line):
            doc.add_paragraph("―" * 30)
            continue
        # Bullet — -, *, +
        m = _re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _add_markdown_runs(p, m.group(1))
            continue
        # Numbered — 1. text
        m = _re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _add_markdown_runs(p, m.group(1))
            continue
        # Blank line → blank paragraph for spacing
        if not line.strip():
            doc.add_paragraph()
            continue
        # Default: paragraph with inline formatting
        p = doc.add_paragraph()
        _add_markdown_runs(p, line)
    # Unclosed code block — dump what we have.
    if code_buf:
        p = doc.add_paragraph()
        run = p.add_run("\n".join(code_buf))
        run.font.name = "Consolas"
    buf = _io.BytesIO()
    doc.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document",
    )


def _strip_md_markers(s):
    """Strip the most common Markdown emphasis markers so they don't
    leak as literal characters into XLSX cells / PPTX slides."""
    import re as _re
    if not s:
        return s
    s = _re.sub(r"^\s*#{1,6}\s*", "", s)            # leading heading hashes
    s = _re.sub(r"\*\*(.+?)\*\*", r"\1", s)        # **bold**
    s = _re.sub(r"__(.+?)__", r"\1", s)            # __bold__
    s = _re.sub(r"(?<![\w*])\*(.+?)\*(?![\w*])", r"\1", s)  # *italic*
    s = _re.sub(r"(?<![\w_])_(.+?)_(?![\w_])", r"\1", s)    # _italic_
    s = _re.sub(r"`(.+?)`", r"\1", s)              # `code`
    s = _re.sub(r"^\s*[-*+]\s+", "• ", s)          # bullets → •
    return s


def _parse_markdown_table(content):
    """If `content` contains a Markdown table, return its rows as a list
    of lists of cell strings (header included, markers stripped, separator
    row removed). Return None when `content` isn't a Markdown table.

    Handles the exact shapes LLMs emit:
      | ID | Name | Email |
      |----|------|-------|
      | 6  | Abby | a@x   |
    — leading/trailing pipes, the `|---|:--:|` separator row, and ragged
    spacing are all normalised."""
    import re as _re
    lines = [ln for ln in (content or "").split("\n") if ln.strip()]
    # Need at least a header + a separator row to call it a table.
    pipe_lines = [ln for ln in lines if ln.strip().startswith("|") or "|" in ln]
    if len(pipe_lines) < 2:
        return None

    def _split_row(ln):
        s = ln.strip()
        # Drop the outer border pipes so we don't get empty edge cells.
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [_strip_md_markers(c.strip()) for c in s.split("|")]

    # A separator row is all dashes/colons/spaces between pipes.
    sep_re = _re.compile(r"^\s*\|?[\s:\-\|]+\|?\s*$")
    rows = []
    saw_separator = False
    for ln in lines:
        if "|" not in ln:
            # A non-table line breaks the table block once we've started.
            if rows:
                break
            continue
        if sep_re.match(ln) and set(ln.replace("|", "").replace(" ", "")) <= set(":-"):
            saw_separator = True
            continue
        rows.append(_split_row(ln))
    # Require the separator to be confident it's really a table.
    if not saw_separator or len(rows) < 1:
        return None
    return rows


def _gen_xlsx_blob(content):
    """XLSX built from `content`. Priority:
      1. Markdown table (most common LLM output) → parsed into real
         columns, header + rows, no border-pipe junk, no separator row.
      2. Delimited rows — auto-detect tab > '|' > comma.
    Markdown markers in cells are stripped so '**Total**' becomes 'Total'
    instead of leaking asterisks."""
    import io as _io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active

    table = _parse_markdown_table(content)
    if table:
        rows = table
    else:
        # Fallback: delimiter detection on raw lines.
        sample = (content or "")[:2000]
        if "\t" in sample:
            delim = "\t"
        elif "|" in sample:
            delim = "|"
        else:
            delim = ","
        rows = []
        for line in (content or "").split("\n"):
            if not line.strip():
                continue
            cells = [c.strip() for c in line.split(delim)]
            # Strip empty border cells produced by leading/trailing delim.
            if delim == "|":
                if cells and cells[0] == "":
                    cells = cells[1:]
                if cells and cells[-1] == "":
                    cells = cells[:-1]
            rows.append([_strip_md_markers(c) for c in cells])

    for r in rows:
        ws.append(r)

    # Light styling: bold header, frozen first row, autosized columns.
    if rows:
        header_fill = PatternFill("solid", fgColor="DDEBF7")
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        # Column widths from the longest cell in each column (capped).
        widths = {}
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = min(max(widths.get(i, 10), len(str(c)) + 2), 60)
        for i, w in widths.items():
            ws.column_dimensions[chr(65 + i) if i < 26 else "A"].width = w

    buf = _io.BytesIO()
    wb.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet",
    )


def _gen_pptx_blob(content, fallback_title="Slide"):
    """PPTX from sectioned content. Sections delimited by lines of the
    form `=== Title ===` (whitespace tolerant). Each section becomes a
    title+content slide. If no section markers are present, the whole
    content is one slide."""
    import io as _io
    import re as _re
    from pptx import Presentation
    prs = Presentation()
    layout = prs.slide_layouts[1]   # 'Title and Content'
    sections = []
    current = {"title": fallback_title, "lines": []}
    for line in (content or "").split("\n"):
        stripped = line.strip()
        m = _re.match(r"^=+\s*(.+?)\s*=+$", stripped)
        if m:
            if current["lines"] or current["title"] != fallback_title:
                sections.append(current)
            current = {"title": m.group(1) or fallback_title, "lines": []}
        elif stripped:
            current["lines"].append(stripped)
    if current["lines"] or current["title"] != fallback_title:
        sections.append(current)
    if not sections:
        sections = [{"title": fallback_title, "lines": [content or ""]}]
    for s in sections:
        slide = prs.slides.add_slide(layout)
        if slide.shapes.title:
            slide.shapes.title.text = _strip_md_markers(s["title"])
        # Body placeholder.
        body_ph = None
        for ph in slide.placeholders:
            if ph.placeholder_format.idx == 1:
                body_ph = ph
                break
        if body_ph is not None:
            cleaned = [_strip_md_markers(ln) for ln in s["lines"]]
            body_ph.text = "\n".join(cleaned) or " "
    buf = _io.BytesIO()
    prs.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation",
    )


def _looks_like_html(content):
    """Heuristic: does `content` look like an HTML document/fragment the
    model produced for a rich layout (vs plain Markdown)?"""
    import re as _re
    if not content:
        return False
    s = content.strip()
    if s[:200].lower().lstrip().startswith(("<!doctype", "<html")):
        return True
    # A handful of block-level tags is a strong signal it's real HTML and
    # not Markdown that merely mentions a `<` somewhere.
    tags = _re.findall(
        r"</?(?:html|head|body|div|table|tr|td|th|thead|tbody|h[1-6]|"
        r"section|article|header|footer|ul|ol|li|p|span|style|img)\b",
        s, _re.IGNORECASE)
    return len(tags) >= 3


# Default stylesheet wrapped around model HTML when it didn't bring a full
# document. Gives clean typography, styled tables, page margins, and a
# footer page number — so even a bare fragment renders nicely.
_HTML_PDF_BASE_CSS = """
@page { size: A4; margin: 1.8cm 1.8cm 2.2cm 1.8cm;
        @bottom-center { content: counter(page) " / " counter(pages);
                         font-size: 9px; color: #888; } }
* { box-sizing: border-box; }
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-size: 11px; line-height: 1.5; color: #222; }
h1 { font-size: 22px; color: #1F3864; margin: 0 0 .3em; }
h2 { font-size: 16px; color: #2F5496; border-bottom: 2px solid #DDEBF7;
     padding-bottom: 3px; margin: 1.2em 0 .4em; }
h3 { font-size: 13px; color: #2F5496; margin: 1em 0 .3em; }
p { margin: .4em 0; }
ul, ol { margin: .4em 0 .4em 1.2em; }
table { border-collapse: collapse; width: 100%; margin: .6em 0;
        font-size: 10px; }
th { background: #2F5496; color: #fff; text-align: left; padding: 6px 8px;
     font-weight: bold; }
td { border: 0.5px solid #B8C4D9; padding: 5px 8px; vertical-align: top; }
tr:nth-child(even) td { background: #F2F6FC; }
code, pre { font-family: 'DejaVu Sans Mono', monospace; font-size: 9px;
            background: #F4F4F4; }
pre { padding: 8px; border: 0.5px solid #E0E0E0; white-space: pre-wrap; }
.rtl, [dir="rtl"] { direction: rtl; text-align: right; }
"""


# Same navy-led palette as the chat's canvas renderer (static/src/js/
# dai_charts.js) so a report looks identical in the chat and in the PDF.
_CHART_PALETTE = [
    "#2F5496", "#4472C4", "#5B9BD5", "#70AD47", "#FFC000",
    "#ED7D31", "#C00000", "#7030A0", "#00B0F0", "#264478",
]


def _fmt_num(v, unit=""):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1000:
        s = f"{f:,.0f}".replace(",", " ")  # thin-space thousands
    else:
        s = f"{f:g}"
    return f"{s} {unit}".strip() if unit else s


def _nice_max(v):
    import math
    if v <= 0:
        return 1.0
    pow10 = 10 ** math.floor(math.log10(v))
    n = v / pow10
    step = 1 if n <= 1 else 2 if n <= 2 else 5 if n <= 5 else 10
    return step * pow10


def _svg_chart_from_spec(spec):
    """Build a static inline <svg> from the SAME {type,labels,series,unit}
    JSON the chat canvas uses. WeasyPrint renders SVG natively, so the PDF
    chart is drawn from the model's real fetched numbers — never hand-written
    bar widths (which the model tended to fabricate). Returns '' on bad spec."""
    from xml.sax.saxutils import escape as _xesc

    if not isinstance(spec, dict):
        return ""
    labels = spec.get("labels")
    if not isinstance(labels, list) or not labels:
        return ""
    series = spec.get("series")
    if not isinstance(series, list) or not series:
        data = spec.get("data")
        if isinstance(data, list):
            series = [{"name": spec.get("name", ""), "data": data}]
        else:
            return ""
    # Validate & coerce series data to floats.
    clean = []
    for s in series:
        d = s.get("data") if isinstance(s, dict) else None
        if not isinstance(d, list) or not d:
            return ""
        try:
            clean.append({"name": s.get("name", ""),
                          "data": [float(x or 0) for x in d]})
        except (TypeError, ValueError):
            return ""
    ctype = str(spec.get("type", "bar")).lower()
    unit = spec.get("unit", "") or ""
    title = spec.get("title", "") or ""
    W, H = 720, 300
    padL, padR, padT, padB = 70, 16, 40 if title else 16, 46
    plotW, plotH = W - padL - padR, H - padT - padB

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'font-family="Helvetica,Arial,sans-serif">']
    if title:
        parts.append(f'<text x="{padL}" y="22" font-size="14" '
                     f'font-weight="bold" fill="#1F3864">{_xesc(title)}</text>')

    if ctype in ("pie", "doughnut"):
        import math
        data = clean[0]["data"]
        total = sum(data) or 1.0
        cx, cy = W / 2, padT + plotH / 2
        r = min(plotW, plotH) / 2 - 6
        inner = r * 0.58 if ctype == "doughnut" else 0
        a0 = -math.pi / 2
        for i, v in enumerate(data):
            frac = v / total
            a1 = a0 + frac * 2 * math.pi
            large = 1 if (a1 - a0) > math.pi else 0
            x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
            x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
            color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
            parts.append(
                f'<path d="M {cx:.1f} {cy:.1f} L {x0:.1f} {y0:.1f} '
                f'A {r:.1f} {r:.1f} 0 {large} 1 {x1:.1f} {y1:.1f} Z" '
                f'fill="{color}"/>')
            if frac >= 0.06:
                mid = (a0 + a1) / 2
                lr = (inner + r) / 2 if inner else r * 0.62
                parts.append(
                    f'<text x="{cx + math.cos(mid) * lr:.1f}" '
                    f'y="{cy + math.sin(mid) * lr:.1f}" font-size="12" '
                    f'fill="#fff" font-weight="bold" text-anchor="middle" '
                    f'dominant-baseline="middle">{round(frac * 100)}%</text>')
            a0 = a1
        if inner:
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{inner:.1f}" '
                         f'fill="#fff"/>')
        # legend
        ly = padT
        for i, lab in enumerate(labels):
            color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
            parts.append(f'<rect x="{padL}" y="{ly}" width="10" height="10" '
                         f'rx="2" fill="{color}"/>')
            parts.append(f'<text x="{padL + 16}" y="{ly + 9}" font-size="11" '
                         f'fill="#333">{_xesc(str(lab))}</text>')
            ly += 18
        parts.append("</svg>")
        return "".join(parts)

    # bar / line — shared grid.
    mx = 0.0
    for s in clean:
        mx = max(mx, max(s["data"]))
    mx = _nice_max(mx)
    ticks = 4
    for i in range(ticks + 1):
        val = mx / ticks * i
        y = padT + plotH - plotH * i / ticks
        parts.append(f'<line x1="{padL}" y1="{y:.1f}" x2="{padL + plotW}" '
                     f'y2="{y:.1f}" stroke="#e2e6ec" stroke-width="1"/>')
        parts.append(f'<text x="{padL - 8}" y="{y + 3:.1f}" font-size="10" '
                     f'fill="#5b6b7c" text-anchor="end">'
                     f'{_xesc(_fmt_num(val, unit))}</text>')
    n = len(labels)
    if ctype == "line":
        stepX = plotW / (n - 1) if n > 1 else plotW
        for si, s in enumerate(clean):
            color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
            pts = []
            for i, v in enumerate(s["data"][:n]):
                px = padL + (i * stepX if n > 1 else plotW / 2)
                py = padT + plotH - (v / mx) * plotH
                pts.append((px, py))
            if pts:
                dpath = " ".join(
                    (f"M {x:.1f} {y:.1f}" if k == 0 else f"L {x:.1f} {y:.1f}")
                    for k, (x, y) in enumerate(pts))
                parts.append(f'<path d="{dpath}" fill="none" stroke="{color}" '
                             f'stroke-width="2.5"/>')
                for x, y in pts:
                    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" '
                                 f'fill="{color}"/>')
    else:  # bar
        group_w = plotW / n
        ns = len(clean)
        gap = group_w * 0.18
        bar_w = (group_w - gap) / ns
        for gi in range(n):
            for si, s in enumerate(clean):
                v = s["data"][gi] if gi < len(s["data"]) else 0
                bh = (v / mx) * plotH
                x = padL + gi * group_w + gap / 2 + si * bar_w
                y = padT + plotH - bh
                color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
                parts.append(f'<rect x="{x + 1:.1f}" y="{y:.1f}" '
                             f'width="{max(bar_w - 2, 1):.1f}" '
                             f'height="{max(bh, 1):.1f}" rx="3" fill="{color}"/>')
    # x labels
    group_w = plotW / n
    for i, lab in enumerate(labels[:n]):
        x = padL + i * group_w + group_w / 2
        txt = str(lab)
        if len(txt) > 10:
            txt = txt[:9] + "…"
        parts.append(f'<text x="{x:.1f}" y="{padT + plotH + 16:.1f}" '
                     f'font-size="10" fill="#5b6b7c" text-anchor="middle">'
                     f'{_xesc(txt)}</text>')
    # legend for multi-series
    if len(clean) > 1:
        lx = padL
        for si, s in enumerate(clean):
            color = _CHART_PALETTE[si % len(_CHART_PALETTE)]
            parts.append(f'<rect x="{lx}" y="{H - 12}" width="10" height="10" '
                         f'rx="2" fill="{color}"/>')
            nm = _xesc(str(s.get("name", "")))
            parts.append(f'<text x="{lx + 14}" y="{H - 3}" font-size="10" '
                         f'fill="#333">{nm}</text>')
            lx += 40 + len(str(s.get("name", ""))) * 6
    parts.append("</svg>")
    return "".join(parts)


def _inline_chart_blocks(html):
    """Replace ```chart {json}``` fenced blocks (and <pre>-wrapped variants
    the model sometimes emits inside HTML) with a server-rendered <svg>, so
    the PDF chart uses the model's real JSON data — the SAME source as the
    chat's canvas chart. Bad JSON is left as-is (harmless code block)."""
    import re as _re
    import json as _json

    def _sub(m):
        raw = m.group("json").strip()
        try:
            spec = _json.loads(raw)
        except Exception:
            return m.group(0)
        svg = _svg_chart_from_spec(spec)
        if not svg:
            return m.group(0)
        return (f'<div style="margin:10px 0;padding:10px 12px;border:1px solid '
                f'#DDE3EC;border-radius:8px;background:#fff">{svg}</div>')

    # ```chart ... ``` possibly wrapped by <pre><code> after HTML escaping.
    pattern = _re.compile(
        r"(?:<pre[^>]*>\s*(?:<code[^>]*>)?\s*)?"
        r"```chart\s*(?P<json>\{.*?\})\s*```"
        r"(?:\s*(?:</code>)?\s*</pre>)?",
        _re.DOTALL | _re.IGNORECASE,
    )
    return pattern.sub(_sub, html)


def _gen_pdf_from_html_blob(content):
    """Render the model's HTML/CSS to PDF with WeasyPrint — the rich path
    that unlocks arbitrary layouts (multi-column, colours, headers/footers,
    images, SVG charts, page breaks). Raises ImportError when WeasyPrint
    isn't installed so the caller can fall back to the Markdown renderer."""
    import io as _io
    from weasyprint import HTML, CSS  # ImportError → caller falls back

    s = (content or "").strip()
    # Turn any ```chart JSON blocks into real inline SVG charts first.
    s = _inline_chart_blocks(s)
    # Wrap a bare fragment in a full document so our base CSS applies.
    low = s[:200].lower().lstrip()
    if not low.startswith(("<!doctype", "<html")):
        s = (f"<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
             f"<body>{s}</body></html>")

    buf = _io.BytesIO()
    HTML(string=s).write_pdf(
        buf, stylesheets=[CSS(string=_HTML_PDF_BASE_CSS)])
    return buf.getvalue(), "application/pdf"


def _gen_pdf_blob(content):
    """PDF dispatcher.

    • If `content` looks like HTML and WeasyPrint is available → render
      the HTML/CSS (rich, arbitrary layout).
    • Otherwise → the Markdown renderer below (reportlab platypus).

    The HTML path never breaks the chat: any WeasyPrint failure (not
    installed, bad markup, missing system libs) falls back to Markdown.
    """
    if _looks_like_html(content):
        try:
            return _gen_pdf_from_html_blob(content)
        except ImportError:
            _cprint("yellow", "PDF HTML",
                    "WeasyPrint not installed — falling back to the "
                    "Markdown PDF renderer. `pip install weasyprint` for "
                    "rich HTML layouts.")
        except Exception as e:
            _cprint("yellow", "PDF HTML",
                    f"WeasyPrint render failed ({type(e).__name__}: {e}) "
                    f"— falling back to Markdown renderer.")
    return _gen_pdf_from_markdown_blob(content)


def _gen_pdf_from_markdown_blob(content):
    """Markdown-aware PDF built with reportlab's platypus engine.

    Same Markdown vocabulary as `_gen_docx_blob`:
      • `#`…`######` → H1…H6
      • `**bold**` / `__bold__` → bold runs
      • `*italic*` / `_italic_` → italic runs
      • `` `code` `` → inline monospaced
      • ```` ``` `` → code block
      • `- item` / `* item` / `+ item` → bullet list
      • `1. item` → numbered list
      • `---` / `***` / `___` → horizontal rule

    Arabic / Hebrew / Persian text is reshaped + bidi'd when
    `arabic_reshaper` and `python-bidi` are installed; otherwise the
    raw codepoints go in (letters appear disconnected but the file
    still opens — never corrupt)."""
    import io as _io
    import re as _re
    from html import escape as _esc
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib import colors as _colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
        Preformatted, HRFlowable, Table, TableStyle,
    )
    from .structured_doc import _is_rtl_text

    # Soft RTL shaping — best-effort. Without the libs the PDF still
    # opens (just with disconnected Arabic letterforms).
    try:
        import arabic_reshaper as _ar
        from bidi.algorithm import get_display as _bidi
        def _shape(text):
            return _bidi(_ar.reshape(text)) if _is_rtl_text(text) else text
    except ImportError:
        def _shape(text):
            return text

    # Inline markdown → reportlab mini-HTML. Order matters: code first
    # (so * inside `code` isn't mistaken for emphasis), bold before italic.
    def _inline(text):
        s = _esc(text or "")
        s = _re.sub(r"`([^`]+?)`",
                    r'<font face="Courier">\1</font>', s)
        s = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = _re.sub(r"__(.+?)__",     r"<b>\1</b>", s)
        s = _re.sub(r"(?<![\w*])\*(.+?)\*(?![\w*])", r"<i>\1</i>", s)
        s = _re.sub(r"(?<![\w_])_(.+?)_(?![\w_])",   r"<i>\1</i>", s)
        return s

    base = getSampleStyleSheet()["BodyText"]
    body_style = ParagraphStyle(
        "Body", parent=base, fontName="Helvetica", fontSize=11,
        leading=15, spaceAfter=4, alignment=TA_LEFT,
    )
    body_rtl = ParagraphStyle(
        "BodyRTL", parent=body_style, alignment=TA_RIGHT,
    )
    heading_sizes = {1: 22, 2: 18, 3: 15, 4: 13, 5: 12, 6: 11}
    code_style = ParagraphStyle(
        "Code", parent=base, fontName="Courier", fontSize=9,
        leading=12, leftIndent=12, backColor="#F4F4F4",
        borderColor="#E0E0E0", borderWidth=0.5, borderPadding=6,
        spaceBefore=4, spaceAfter=6,
    )

    def _para(text):
        # Pick LTR/RTL alignment per-paragraph so a mixed-language
        # document doesn't get glued to one side.
        style = body_rtl if _is_rtl_text(text) else body_style
        return Paragraph(_inline(_shape(text)), style)

    flow = []
    in_code = False
    code_buf = []
    list_buf = []       # accumulates consecutive bullets / numbers
    list_kind = None    # 'bullet' or 'number' or None
    table_buf = []      # accumulates consecutive markdown-table rows

    def _flush_list():
        nonlocal list_buf, list_kind
        if not list_buf:
            return
        items = [ListItem(_para(t)) for t in list_buf]
        flow.append(ListFlowable(
            items,
            bulletType="bullet" if list_kind == "bullet" else "1",
            leftIndent=18, bulletFontSize=11,
        ))
        list_buf = []
        list_kind = None

    # Header style for table cells (white, bold) and body cells.
    cell_style = ParagraphStyle(
        "Cell", parent=body_style, fontSize=9, leading=12, spaceAfter=0)
    cell_head = ParagraphStyle(
        "CellHead", parent=cell_style, textColor=_colors.white,
        fontName="Helvetica-Bold")

    def _flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = _parse_markdown_table("\n".join(table_buf))
        table_buf = []
        if not rows:
            return
        # Wrap every cell in a Paragraph so long text wraps instead of
        # overflowing the page width.
        data = []
        for ri, row in enumerate(rows):
            style = cell_head if ri == 0 else cell_style
            data.append([Paragraph(_inline(_shape(str(c))), style) for c in row])
        # Even column widths across the printable area (A4 - margins ≈ 17cm).
        ncols = max(len(r) for r in data)
        avail = 17 * cm
        col_w = [avail / ncols] * ncols
        tbl = Table(data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _colors.HexColor("#2F5496")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [_colors.white, _colors.HexColor("#F2F6FC")]),
            ("GRID", (0, 0), (-1, -1), 0.4, _colors.HexColor("#B8C4D9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(tbl)
        flow.append(Spacer(1, 6))

    def _is_table_line(ln):
        # A markdown table row has at least one interior pipe.
        s = ln.strip()
        return "|" in s and not s.startswith("```")

    for raw in (content or "").split("\n"):
        line = raw.rstrip()
        # Inside a table block: keep accumulating pipe lines, flush on exit.
        if table_buf and not _is_table_line(line):
            _flush_table()
        # Triple-backtick fence
        if line.lstrip().startswith("```"):
            _flush_list()
            _flush_table()
            if in_code:
                flow.append(Preformatted(
                    "\n".join(code_buf) or " ", code_style))
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue
        # Heading
        m = _re.match(r"^\s*(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            _flush_list()
            level = len(m.group(1))
            text = m.group(2)
            style = ParagraphStyle(
                f"H{level}", parent=body_style,
                fontName="Helvetica-Bold",
                fontSize=heading_sizes.get(level, 11),
                leading=heading_sizes.get(level, 11) + 4,
                spaceBefore=10, spaceAfter=6,
                alignment=TA_RIGHT if _is_rtl_text(text) else TA_LEFT,
            )
            flow.append(Paragraph(_inline(_shape(text)), style))
            continue
        # Horizontal rule
        if _re.match(r"^\s*([-*_])\s*\1\s*\1\s*$", line):
            _flush_list()
            flow.append(HRFlowable(
                width="100%", color="#888888", spaceBefore=6, spaceAfter=6))
            continue
        # Markdown table row — accumulate; rendered as a real table on flush.
        if _is_table_line(line):
            _flush_list()
            table_buf.append(line)
            continue
        # Bullet
        m = _re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            if list_kind not in (None, "bullet"):
                _flush_list()
            list_kind = "bullet"
            list_buf.append(m.group(1))
            continue
        # Numbered
        m = _re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            if list_kind not in (None, "number"):
                _flush_list()
            list_kind = "number"
            list_buf.append(m.group(1))
            continue
        # Blank line → spacer
        if not line.strip():
            _flush_list()
            flow.append(Spacer(1, 6))
            continue
        # Default paragraph
        _flush_list()
        flow.append(_para(line))

    _flush_list()
    _flush_table()
    if code_buf:    # unterminated fence
        flow.append(Preformatted("\n".join(code_buf), code_style))

    if not flow:
        flow.append(Spacer(1, 1))

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="DIGI-ERP AI", author="DIGI-ERP AI",
    )
    doc.build(flow)
    return buf.getvalue(), "application/pdf"


_GENERATE_TEXT_MIME = {
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".json": "application/json", ".xml": "application/xml",
    ".html": "text/html", ".htm": "text/html",
    ".py": "text/x-python", ".js": "text/javascript",
    ".css": "text/css", ".yml": "text/yaml", ".yaml": "text/yaml",
    ".tsv": "text/tab-separated-values",
}


def _generate_file(env, params):
    """Crée un fichier (ir.attachment) et retourne son URL de
    téléchargement. Supporte les formats texte (`.txt`, `.md`, `.csv`,
    `.json`, `.xml`, `.html`, `.py`, `.js`, `.css`, `.yml`, `.tsv`) ET
    les formats Office binaires (`.docx`, `.xlsx`, `.pptx`, `.pdf`).

    Pour PDF : `content` = Markdown OU HTML+CSS complet (mise en page
        riche via WeasyPrint, détecté automatiquement ; fallback
        Markdown si WeasyPrint absent).
    Pour DOCX : `content` = Markdown (`# titres`, `**gras**`,
        `*italique*`, `` `code` ``, `- listes`, `1. listes`, `---`).
    Pour XLSX : `content` = lignes séparées par TAB, `|` ou virgule.
    Pour PPTX : sections délimitées par `=== Titre ===`.

    Paramètres :
      - filename : nom du fichier avec extension (ex: 'translated.docx').
      - content  : contenu (texte ou structure selon l'extension).
      - mimetype : optionnel ; deviné depuis l'extension si absent.
    """
    filename = (params.get("filename") or "file.txt").strip()
    content  = params.get("content") or ""
    mimetype = (params.get("mimetype") or "").strip()

    ext = ("." + filename.rsplit(".", 1)[-1].lower()
           if "." in filename else "")

    try:
        if ext == ".docx":
            blob, mt = _gen_docx_blob(content)
        elif ext in (".xlsx", ".xlsm"):
            blob, mt = _gen_xlsx_blob(content)
        elif ext == ".pptx":
            base = filename.rsplit(".", 1)[0] or "Slide"
            blob, mt = _gen_pptx_blob(content, fallback_title=base)
        elif ext == ".pdf":
            blob, mt = _gen_pdf_blob(content)
        else:
            blob = content.encode("utf-8")
            mt = mimetype or _GENERATE_TEXT_MIME.get(
                ext, "application/octet-stream")
    except ImportError as e:
        return {
            "ok": False,
            "error": (
                _("Cannot generate %(filename)s — missing dependency: "
                  "%(error)s. Install the required packages (python-docx for "
                  ".docx, openpyxl for .xlsx, python-pptx for .pptx, "
                  "reportlab for .pdf).", filename=filename, error=e)
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"File generation failed: "
                     f"{type(e).__name__}: {e}",
        }

    if mimetype:
        mt = mimetype     # caller-provided MIME wins

    encoded = base64.b64encode(blob).decode()
    attach = env["ir.attachment"].create({
        "name":      filename,
        "datas":     encoded,
        "mimetype":  mt,
        "res_model": "res.users",
        "res_id":    env.uid,
    })
    _cprint("green", "TOOL generate_file",
            f"attachment id={attach.id}  name={filename}  "
            f"{len(blob)} bytes  mime={mt}")
    return {
        "ok": True,
        "attachment_id": attach.id,
        "filename": filename,
        "mimetype": mt,
        "size_bytes": len(blob),
        "download_url": f"/ai-solution/download/{attach.id}",
        "message": (
            f"The file **{filename}** was generated successfully. "
            f"[Download](/ai-solution/download/{attach.id})"
        ),
    }


_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": ("png",  "image/png"),
    b"\xff\xd8\xff":      ("jpg",  "image/jpeg"),
    b"GIF87a":            ("gif",  "image/gif"),
    b"GIF89a":            ("gif",  "image/gif"),
    b"RIFF":              ("webp", "image/webp"),  # also matches WAV but
                                                    # image-gen never returns audio
    b"BM":                ("bmp",  "image/bmp"),
}


def _sniff_image(blob):
    """Return (ext, mime) for `blob` from its magic bytes, or
    (None, None) if it doesn't look like a known image format."""
    if not blob:
        return (None, None)
    for magic, (ext, mime) in _IMAGE_MAGIC.items():
        if blob.startswith(magic):
            return (ext, mime)
    return (None, None)


def _decode_generated_image(payload, raw_bytes):
    """Image-gen models don't share one response envelope yet —
    this walks the most common shapes and returns the first decoded
    blob it can extract. Returns (bytes, mime) or (None, None)."""
    import re as _re

    # Shape A: native binary response (the raw HTTP body is the image)
    if raw_bytes:
        ext, mime = _sniff_image(raw_bytes)
        if ext:
            return raw_bytes, mime

    if not isinstance(payload, dict):
        return (None, None)

    # Shape B: {"images": ["<base64>", ...]} — the vision-input
    # field reused for output by several community models.
    imgs = payload.get("images")
    if isinstance(imgs, list) and imgs:
        for entry in imgs:
            if not isinstance(entry, str) or not entry:
                continue
            blob = _b64_decode_lax(entry)
            if blob:
                ext, mime = _sniff_image(blob)
                if ext:
                    return blob, mime

    # Shape C: {"response": "<base64 or data-URL>"} — most common when
    # the model crams the image into the text field.
    resp = payload.get("response")
    if isinstance(resp, str) and resp:
        m = _re.match(r"^data:(image/[\w.+-]+);base64,(.+)$",
                      resp.strip(), _re.S)
        if m:
            blob = _b64_decode_lax(m.group(2))
            if blob:
                ext, mime = _sniff_image(blob)
                return blob, (mime or m.group(1))
        # Bare base64 with no data-URL wrapper.
        blob = _b64_decode_lax(resp)
        if blob:
            ext, mime = _sniff_image(blob)
            if ext:
                return blob, mime

    # Shape D: {"image": "..."} singular variant.
    one = payload.get("image")
    if isinstance(one, str) and one:
        blob = _b64_decode_lax(one)
        if blob:
            ext, mime = _sniff_image(blob)
            if ext:
                return blob, mime

    return (None, None)


def _b64_decode_lax(s):
    """Best-effort base64 decode that tolerates whitespace, data-URL
    prefixes, and missing padding. Returns bytes on success or None."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    s = "".join(s.split())  # drop whitespace / newlines
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        return None


def _fmt_cell(v):
    """Render an ORM field value as a flat string for a table cell."""
    if v is False or v is None:
        return ""
    if v is True:
        return _("Yes")
    if isinstance(v, (list, tuple)):
        # many2one => (id, "Name"); x2many => list of ids/records.
        if len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
            return v[1]
        return ", ".join(_fmt_cell(x) for x in v) if v else ""
    return str(v)


def _pick_export_fields(env, model, limit=10):
    """Choose a sensible, human-meaningful set of columns for an export
    when the caller didn't specify fields. Skips noise/internal fields and
    heavy text/binary; prefers identity + business scalars + key m2o."""
    Model = env[model]
    fields_meta = Model.fields_get()
    preferred_first = ("name", "display_name", "complete_name", "reference",
                       "code", "number", "ref")
    good_types = {"char", "text", "selection", "date", "datetime",
                  "integer", "float", "monetary", "boolean", "many2one"}
    chosen = []
    # Identity column first.
    for f in preferred_first:
        if f in fields_meta and f not in _NOISE_FIELD_EXACT:
            chosen.append(f)
            break
    for fname, meta in fields_meta.items():
        if fname in chosen:
            continue
        if fname in _NOISE_FIELD_EXACT:
            continue
        if any(fname.startswith(p) for p in _NOISE_FIELD_PREFIXES):
            continue
        if meta.get("type") not in good_types:
            continue
        # Skip very long text bodies — they wreck table layout.
        if meta.get("type") == "text":
            continue
        chosen.append(fname)
        if len(chosen) >= limit:
            break
    return chosen or ["display_name"]


def _export_records_to_file(env, params):
    """DETERMINISTIC data export: fetch records and build the file IN CODE.

    The model only chooses `model` + `filename` (+ optional domain/fields/
    title/layout). The rows NEVER pass through the LLM's text generation —
    so the file is always complete (every matching record), correct (real
    ORM data, no fabrication) and on-topic (the model param decides the
    content, so a follow-up about employees can't reuse a previous projects
    report). This is the reliable path for "generate a file with all X".

    Params:
      model    (str, required)  — technical model, e.g. 'hr.employee'.
      filename (str, required)  — with extension (.pdf/.xlsx/.docx/.csv).
      domain   (list, optional) — Odoo domain to filter.
      fields   (list, optional) — columns; sensible default if omitted.
      title    (str, optional)  — document title.
      layout   (str, optional)  — 'table' (default) or 'detailed'
                                  (one section per record, for analyses).
      limit    (int, optional)  — max records (default 500).
    """
    model = (params.get("model") or "").strip()
    if not model:
        return {"error": "Parameter `model` is required."}
    if model not in env:
        return {"error": f"Unknown model: {model}"}
    filename = (params.get("filename") or "").strip() or f"{model}.pdf"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ".pdf"
    domain = _coerce_domain(params.get("domain"))
    limit = max(1, min(int(params.get("limit") or 500), 2000))
    layout = (params.get("layout") or "table").lower()
    title = params.get("title") or model.replace(".", " ").title()

    requested = params.get("fields") or []
    if requested:
        valid = set(env[model]._fields.keys())
        fields = [f for f in requested if f.split(".")[0] in valid]
    else:
        fields = _pick_export_fields(env, model)
    if not fields:
        fields = ["display_name"]

    try:
        rows = env[model].search_read(domain, fields, limit=limit)
    except Exception as e:
        return {"error": f"search_read failed: {type(e).__name__}: {e}"}

    if not rows:
        return {"ok": False,
                "error": f"No record found on {model} for this filter — "
                         f"nothing to export. Check the domain or the "
                         f"model."}

    # Column order: keep the requested/derived field order, id last-ish.
    cols = [f for f in fields if any(f in r for r in rows)]
    headers = [env[model]._fields[c].string if c in env[model]._fields else c
               for c in cols]

    # Build the document body. CSV/XLSX → table; PDF/DOCX → title + table
    # (or per-record sections for a 'detailed' analysis layout).
    if ext in (".xlsx", ".xlsm", ".csv"):
        md = ["| " + " | ".join(headers) + " |",
              "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in rows:
            md.append("| " + " | ".join(_fmt_cell(r.get(c)) for c in cols) + " |")
        content = "\n".join(md)
    elif layout == "detailed":
        parts = [f"# {title}",
                 _("Total: %s record(s).", len(rows)), ""]
        for r in rows:
            label = (_fmt_cell(r.get("name")) or _fmt_cell(r.get("display_name"))
                     or f"#{r.get('id')}")
            parts.append(f"## {label}")
            parts.append("| %s | %s |" % (_("Field"), _("Value")))
            parts.append("|-------|--------|")
            for c, h in zip(cols, headers):
                parts.append(f"| {h} | {_fmt_cell(r.get(c))} |")
            parts.append("")
        content = "\n".join(parts)
    else:
        parts = [f"# {title}", _("Total: %s record(s).", len(rows)), "",
                 "| " + " | ".join(headers) + " |",
                 "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in rows:
            parts.append("| " + " | ".join(_fmt_cell(r.get(c)) for c in cols) + " |")
        content = "\n".join(parts)

    # Reuse the tested generators (markdown-table aware) to build the blob.
    try:
        if ext in (".xlsx", ".xlsm"):
            blob, mt = _gen_xlsx_blob(content)
        elif ext == ".pdf":
            blob, mt = _gen_pdf_blob(content)
        elif ext == ".docx":
            blob, mt = _gen_docx_blob(content)
        else:  # .csv / .txt / fallback
            blob = content.encode("utf-8")
            mt = "text/csv" if ext == ".csv" else "text/plain"
    except Exception as e:
        return {"error": f"File generation failed: "
                         f"{type(e).__name__}: {e}"}

    attach = env["ir.attachment"].create({
        "name": filename,
        "datas": base64.b64encode(blob).decode(),
        "mimetype": mt,
        "res_model": "res.users",
        "res_id": env.uid,
    })
    _cprint("green", "TOOL export_records",
            f"{model} → {filename}  {len(rows)} rows  {len(blob)} bytes")
    return {
        "ok": True,
        "attachment_id": attach.id,
        "filename": filename,
        "record_count": len(rows),
        "download_url": f"/ai-solution/download/{attach.id}",
        "message": _(
            "**%(filename)s** (%(count)s record(s)) was generated. "
            "[Download](/ai-solution/download/%(attachment_id)s)",
            filename=filename, count=len(rows), attachment_id=attach.id),
    }


def _generate_image(env, params):
    """Call the local image-generation model and save the result
    as an ir.attachment so the chat can preview / download it.

    Default model is `x/flux2-klein:4b` — override with `model` if you
    pull another image-gen model later. We accept whatever
    envelope shape the model uses (raw binary, `images[]`, `response`
    with base64 or data-URL) — see `_decode_generated_image`.
    """
    import requests as _rq
    from . import ai_config as _cfg_mod

    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False,
                "error": "Parameter `prompt` is required."}
    # Pick the image model in priority order:
    #   1. Explicit `model` kwarg from the LLM (rare).
    #   2. The first ACTIVE provider with auto_route_tier='image'.
    #      Use its base_url too — most image-gen models are hosted on
    #      a different server than the chat model.
    #   3. Hardcoded fallback (`x/flux2-klein:4b` on the global server).
    model = (params.get("model") or "").strip()
    image_base_url_override = ""
    if not model:
        image_provider = env["digi_erp.model.llm.provider"].sudo().search([
            ("active", "=", True),
            ("auto_route_tier", "=", "image"),
        ], limit=1)
        if image_provider and image_provider.model_name:
            model = image_provider.model_name.strip()
            if image_provider.base_url:
                image_base_url_override = image_provider.base_url.rstrip("/")
    if not model:
        model = "x/flux2-klein:4b"
    width    = int(params.get("width")  or 768)
    height   = int(params.get("height") or 768)
    steps    = int(params.get("steps")  or 4)
    negative = (params.get("negative_prompt") or "").strip()

    cfg = _cfg_mod.get_ai_config(env)
    base = image_base_url_override or cfg["local_base_url"]
    timeout = max(60, int(cfg.get("request_timeout") or 300))

    # We send the request via /api/generate — it's the lowest common
    # denominator for "one prompt → one response" models and is
    # what every community image-gen Modelfile I've seen builds on.
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "width":  width,
            "height": height,
            "num_inference_steps": steps,
        },
    }
    if negative:
        body["options"]["negative_prompt"] = negative

    # Verbose request trace — full body (minus the prompt for size) so
    # we can rebuild the exact curl when something breaks.
    body_trace = {k: v for k, v in body.items() if k != "prompt"}
    _logger.info(
        "[generate_image] POST %s/api/generate  model=%s  body=%s  "
        "prompt=%s  timeout=%ss",
        base, model, body_trace, _truncate(prompt, 200), timeout,
    )
    _cprint("magenta", "TOOL generate_image",
            f"→ model={model} {width}x{height} steps={steps} "
            f"prompt={_truncate(prompt, 80)}")

    try:
        r = _rq.post(f"{base}/api/generate", json=body, timeout=timeout)
    except _rq.exceptions.Timeout:
        _logger.warning(
            "[generate_image] TIMEOUT after %ss — model=%s base=%s",
            timeout, model, base,
        )
        return {"ok": False,
                "error": f"Timed out after {timeout}s — the image model "
                         f"is taking too long to answer."}
    except _rq.exceptions.ConnectionError as e:
        _logger.warning(
            "[generate_image] CONNECTION ERROR base=%s err=%s", base, e,
        )
        return {"ok": False,
                "error": f"Cannot reach the image server at {base}: {e}"}
    except Exception as e:
        _logger.exception("[generate_image] unexpected request error")
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}"}

    raw = r.content or b""
    ct = r.headers.get("Content-Type", "")
    # Always log the response envelope — status, content-type, length,
    # first 600 bytes — so a glance at the Odoo log explains any failure.
    _logger.info(
        "[generate_image] ← HTTP %s  Content-Type=%r  bytes=%s  "
        "first600=%r",
        r.status_code, ct, len(raw), raw[:600],
    )

    if r.status_code != 200:
        # The server puts the actual reason in the JSON `error` field when it
        # can — surface it verbatim instead of just the truncated text.
        server_err = ""
        try:
            j = r.json()
            server_err = j.get("error") or j.get("message") or ""
            _logger.warning(
                "[generate_image] HTTP %s — server JSON error: %s | "
                "full body: %s",
                r.status_code, server_err, _truncate(r.text, 2000),
            )
        except Exception:
            _logger.warning(
                "[generate_image] HTTP %s — non-JSON body: %s",
                r.status_code, _truncate(r.text, 2000),
            )
        return {
            "ok": False,
            "error": (
                f"The image server answered HTTP {r.status_code} "
                f"({ct or 'unknown content-type'}). "
                f"Detail: {server_err or _truncate(r.text, 500)}"
            ),
        }

    payload = None
    try:
        payload = r.json()
    except Exception:
        payload = None

    blob, mime = _decode_generated_image(payload, raw)
    if not blob:
        keys = list(payload.keys()) if isinstance(payload, dict) else []
        # Log a richer dump than what the chat sees — full keys, value
        # types and a peek at each string field so the operator can
        # identify the envelope shape and we can extend
        # `_decode_generated_image` accordingly.
        shape = {}
        if isinstance(payload, dict):
            for k, v in payload.items():
                if isinstance(v, str):
                    shape[k] = f"str[{len(v)}]={v[:80]!r}"
                elif isinstance(v, list):
                    shape[k] = (
                        f"list[{len(v)}]"
                        + (f" first={str(v[0])[:80]!r}" if v else "")
                    )
                else:
                    shape[k] = f"{type(v).__name__}={str(v)[:80]!r}"
        _logger.warning(
            "[generate_image] UNRECOGNIZED ENVELOPE  status=200  "
            "ct=%r  bytes=%s  json_keys=%s  shape=%s  raw_first40=%r",
            ct, len(raw), keys, shape, raw[:40],
        )
        return {
            "ok": False,
            "error": (
                f"The model's answer was not recognised as an image "
                f"(HTTP 200, {ct or 'no content-type'}, {len(raw)} "
                f"bytes). JSON keys: {keys or '∅'}. First bytes: "
                f"{raw[:40]!r}. Full detail in the Odoo log."
            ),
        }

    ext = _sniff_image(blob)[0] or "png"
    import uuid as _uuid
    filename = (params.get("filename")
                or f"image_{_uuid.uuid4().hex[:8]}.{ext}")
    att = env["ir.attachment"].create({
        "name":      filename,
        "datas":     base64.b64encode(blob),
        "mimetype":  mime or "image/png",
        "res_model": "res.users",
        "res_id":    env.uid,
    })
    _cprint("green", "TOOL generate_image",
            f"OK id={att.id} {filename} {len(blob)} bytes  mime={mime}")
    # Inline-renderable markdown so the chat bubble shows the picture
    # directly (the existing `![](url)` renderer handles it).
    return {
        "ok": True,
        "attachment_id": att.id,
        "filename": filename,
        "mimetype": mime or "image/png",
        "size_bytes": len(blob),
        "width": width,
        "height": height,
        "download_url": f"/ai-solution/download/{att.id}",
        "preview_url":  f"/web/content/{att.id}",
        "message": (
            f"![{prompt[:60]}](/web/content/{att.id})\n\n"
            f"[Download the image](/ai-solution/download/{att.id})"
        ),
    }


def _modify_document(env, params):
    """Universal in-place modification of an attached source file.

    Works for .docx, .xlsx (.xlsm), .pptx, .pdf — the user uploads
    something, asks for ANY text-level transformation (translate,
    rewrite, redact, anonymize, paraphrase, simplify, summarise per
    cell, etc.), and the tool produces the SAME file type with the
    original design preserved.

    Protocol — the LLM must respect this strictly:
      • The user prompt contains a `===BEGIN MARKED===` block listing
        every editable unit numbered `[1] …`, `[2] …`, etc.
      • `new_marked` MUST contain the SAME marker numbers, each on
        its own line, with the text replaced by the modified version.
      • Do NOT renumber. Do NOT skip markers (skipped ones keep the
        original text). Do NOT add markdown formatting — the source
        already carries the styling.

    Paramètres :
      - source_attachment_id : id de la pièce jointe d'origine
        (lu dans le prompt sous `ATTACHMENT_ID: <n>`).
      - new_marked : le contenu modifié avec marqueurs `[N] …`.
      - filename : nom du fichier produit (optionnel — déduit du
        source sinon).
      - language : code/nom de la langue cible si pertinent (active
        le RTL pour ar/he/fa/ur).
    """
    from . import structured_doc as _sd

    try:
        source_id = int(params.get("source_attachment_id") or 0)
    except (TypeError, ValueError):
        source_id = 0
    if not source_id:
        return {
            "ok": False,
            "error": (
                "`source_attachment_id` is required. Take it from the "
                "prompt sous la forme `ATTACHMENT_ID: <n>`."
            ),
        }

    new_marked = params.get("new_marked") or ""
    if not new_marked.strip():
        return {
            "ok": False,
            "error": (
                "`new_marked` is empty. Return the modified content with "
                "les marqueurs `[1] …` `[2] …` etc."
            ),
        }

    filename = (params.get("filename") or "").strip()
    language = params.get("language") or ""

    attach = env["ir.attachment"].browse(source_id)
    if not attach.exists():
        return {"ok": False,
                "error": f"Attachment {source_id} not found."}

    src_name = attach.name or "source"
    if not _sd.supported_for_modify(src_name):
        return {
            "ok": False,
            "error": (
                f"Unsupported file type for in-place modification: "
                f"`{src_name}`. Accepted formats: "
                f".docx, .xlsx, .xlsm, .pptx, .pdf. Pour produire "
                f"a different file from scratch, use "
                f"`generate_file`."
            ),
        }

    try:
        source_blob = base64.b64decode(attach.datas or b"")
    except Exception as e:
        return {"ok": False,
                "error": _("Could not read the source file: %s", e)}

    try:
        blob, mime, out_ext = _sd.apply_marked(
            src_name, source_blob, new_marked, language=language,
        )
    except ImportError as e:
        return {
            "ok": False,
            "error": (
                f"Missing Python dependency: {e}. "
                f"Installe `python-docx` (.docx), `openpyxl` (.xlsx), "
                f"`python-pptx` (.pptx), `pypdf` (.pdf)."
            ),
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        _logger.exception("modify_document apply crashed")
        return {
            "ok": False,
            "error": (
                f"Modification failed: "
                f"{type(e).__name__}: {e}"
            ),
        }

    # Pick the output filename. Default: <source_stem>_modified<ext>.
    if not filename:
        base = src_name.rsplit(".", 1)[0] or "modified"
        filename = f"{base}_modified{out_ext}"
    elif not filename.lower().endswith(out_ext):
        # Coerce the extension to whatever apply_marked produced — for
        # PDFs that means swapping `.pdf` → `.docx` (PDF in-place edit
        # is deferred). The user gets a sensible filename either way.
        filename = filename.rsplit(".", 1)[0] + out_ext

    encoded = base64.b64encode(blob).decode()
    new_att = env["ir.attachment"].create({
        "name":      filename,
        "datas":     encoded,
        "mimetype":  mime,
        "res_model": "res.users",
        "res_id":    env.uid,
    })
    _cprint("green", "TOOL modify_document",
            f"src id={source_id} ({src_name}) → dst id={new_att.id} "
            f"name={filename} {len(blob)} bytes  mime={mime}  "
            f"rtl={_sd._is_rtl(language)}")
    return {
        "ok": True,
        "attachment_id": new_att.id,
        "filename": filename,
        "mimetype": mime,
        "size_bytes": len(blob),
        "rtl": _sd._is_rtl(language),
        "download_url": f"/ai-solution/download/{new_att.id}",
        "message": (
            f"The file **{filename}** was produced while preserving "
            f"la mise en page d'origine. "
            f"[Download](/ai-solution/download/{new_att.id})"
        ),
    }


# The tool schema asks for RPC-shaped positional arguments — ids first for a
# method that runs on records. Models sometimes name them instead
# (`kwargs: {ids: [...], vals: {...}}`), and `build_write_preview` already
# reads that form when it builds the confirmation dialog. So the executor
# accepts it too: the dialog and the call that follows it must never disagree
# about which records are being written and with what.
_CALL_IDS_KWARGS = ("ids", "res_ids", "record_ids")
_CALL_VALS_KWARGS = ("vals", "values", "vals_list")


def _normalize_call_args(method, args, kwargs):
    """Move named ids / values back into the positional RPC shape."""
    if method == "create":
        if not args:
            for key in _CALL_VALS_KWARGS:
                if key in kwargs:
                    args = [kwargs.pop(key)]
                    break
        return args, kwargs

    ids = None
    for key in _CALL_IDS_KWARGS:
        if key in kwargs:
            ids = kwargs.pop(key)
            break
    if ids is not None and not (args and isinstance(args[0], (list, int))):
        args = [[ids] if isinstance(ids, int) else ids] + args

    if method == "write" and len(args) < 2:
        for key in _CALL_VALS_KWARGS:
            if key in kwargs:
                args = args + [kwargs.pop(key)]
                break
    return args, kwargs


def _odoo_call(env, params):
    """Invocation générique d'une méthode publique d'un modèle.

    Les ACL Odoo gouvernent ce qui est faisable. La passerelle bloque seulement
    les méthodes manifestement dangereuses (unlink, méthodes privées, exécution
    SQL/workflow brute).

    The call itself goes through `odoo.service.model.call_kw` — the same entry
    point the web client uses for every button press and every save. That is
    not a detail. Our schema (and `build_write_preview`, which builds the
    confirmation dialog out of the very same arguments) follows the RPC
    convention where `args[0]` is the list of record ids, and call_kw is what
    knows how to turn that into a BOUND recordset.

    Doing it by hand — `env[model].write([ids], vals)` on the empty recordset
    `env[model]` — put a third positional argument on `write` and raised
    "write() takes 2 positional arguments but 3 were given". On a method that
    happens to accept the extra argument it would have been quieter and worse:
    it would have run against the EMPTY recordset and changed nothing while
    reporting success. call_kw also knows which methods are `@api.model`
    (create, search_read, fields_get…) and must NOT be handed ids at all.
    """
    from odoo.service.model import call_kw

    model = (params.get("model") or "").strip()
    method = (params.get("method") or "").strip()
    if not model or not method:
        return {"error": _("Parameters `model` and `method` are required.")}
    if model not in env:
        return {"error": _("Unknown model: %s", model)}
    if method.startswith("_"):
        return {"error": _("Private method refused: %s", method)}
    if method in DENIED_METHODS:
        return {"error": _("Method blocked by the gateway: %s", method)}
    args = params.get("args") or []
    kwargs = params.get("kwargs") or {}
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return {"error": _("`args` must be a list and `kwargs` an object.")}

    args, kwargs = _normalize_call_args(method, list(args), dict(kwargs))

    try:
        result = call_kw(env[model], method, args, kwargs)
    except IndexError:
        # call_kw takes args[0] as the ids of a record method; an empty list
        # means the model never said WHICH records to act on.
        return {"error": _(
            "%(model)s.%(method)s runs on records: put the ids first, in "
            "args[0] — for example args: [[42], {\"name\": \"…\"}].",
            model=model, method=method)}
    except AttributeError:
        return {"error": _("Method %(model)s.%(method)s does not exist.",
                           model=model, method=method)}

    # call_kw has already reduced any recordset result to a list of ids.
    return {"model": model, "method": method, "result": result}


# ── Registre ─────────────────────────────────────────────────────────────────

# Descriptions are sent to the model verbatim on every turn that exposes the
# tool, so they are kept terse and in English (the system prompt's language):
# the same guidance in French cost ~35% more tokens for no behavioural gain.
# Anything the model needs about WHICH tool to pick lives in the system
# prompt; what a tool DOES and its parameters live here — said once, not both.
TOOLS = {
    "whoami": {
        "fn": _whoami,
        "description": (
            "Who the current user is: id, name, login, email, company, main "
            "groups. Use for \"who am I\", or to filter on the current user "
            "(e.g. domain [('user_id','=',<id>)])."
        ),
    },
    "odoo_models_search": {
        "fn": _odoo_models_search,
        "description": (
            "Find Odoo models by keyword on their technical name or label. Use "
            "it when unsure which model holds the data (invoice → account.move, "
            "time off → hr.leave, stock → stock.picking / stock.quant). "
            "Params: query (str), limit (default 20)."
        ),
    },
    "odoo_fields_get": {
        "fn": _odoo_fields_get,
        "description": (
            "Field schema of a model: type, label, relation, selection values, "
            "required. Use before odoo_search_read when unsure of a field name "
            "or its allowed values. Params: model (technical name, e.g. "
            "'account.move'), attributes (optional list; default string, type, "
            "required, readonly, selection, relation, help)."
        ),
    },
    "odoo_search_read": {
        "fn": _odoo_search_read,
        "description": (
            "The main read tool. Params: model (technical name), domain (e.g. "
            "[['state','=','posted']]), fields (list, empty = all), limit "
            "(default UNLIMITED — do NOT set one when the user says \"all\" / "
            "\"the list\" / \"everything\" or gives no number; hard cap 200), "
            "offset (default 0), order (e.g. 'id desc'). The user's ACL and "
            "record rules apply. Archived records are excluded unless the "
            "domain contains [['active','=',false]]."
        ),
    },
    "odoo_read": {
        "fn": _odoo_read,
        "description": (
            "Read specific records by id. "
            "Params: model, ids (list of int), fields (optional)."
        ),
    },
    "odoo_search_count": {
        "fn": _odoo_search_count,
        "description": (
            "Count the records matching a domain without fetching them. "
            "Params: model, domain."
        ),
    },
    "odoo_call": {
        "fn": _odoo_call,
        "description": (
            "Call a public method of a model (create, write, action_post, "
            "action_confirm, read_group…). ACL applies. Refused: unlink, "
            "private methods (`_*`), execute / exec_workflow. "
            "Params: model, method, args (list), kwargs (object). "
            "For any method that acts on EXISTING records, args[0] is the "
            "list of ids. "
            "Create: {model:'account.move', method:'create', "
            "args:[{partner_id:7, move_type:'out_invoice', "
            "invoice_line_ids:[[0,0,{name:'Conseil', price_unit:500}]]}]}. "
            "Write: {model:'res.partner', method:'write', "
            "args:[[42], {email:'a@b.com'}]}. "
            "Button: {model:'account.move', method:'action_post', "
            "args:[[7]]}."
        ),
    },
    "generate_file": {
        "fn": _generate_file,
        "description": (
            "Write a file from content YOU author — Markdown, or a full "
            "HTML+CSS document for a rich-layout .pdf (also .csv/.txt/.json). "
            "Saved as an Odoo attachment; returns a download link. For a file "
            "containing Odoo RECORDS use export_records_to_file instead. "
            "Params: filename (e.g. 'rapport.pdf'), content (str, the whole "
            "file), mimetype (optional)."
        ),
    },
    "export_records_to_file": {
        "fn": _export_records_to_file,
        "description": (
            "DETERMINISTIC EXPORT: fetches the records itself and builds the "
            "file from the REAL data — you never transcribe rows, so the "
            "result is complete and exact. Use it for any file containing "
            "Odoo data (\"a pdf/excel with all the employees / projects / "
            "invoices\", \"export…\", \"a detailed report of…\"). "
            "Params: model (required, e.g. 'hr.employee'), filename (required, "
            "with extension .pdf/.xlsx/.docx/.csv), domain (optional filter), "
            "fields (optional columns; auto-selected otherwise), title "
            "(optional), layout ('table' by default, or 'detailed' = one "
            "section per record), limit (optional, default 500)."
        ),
    },
    "generate_image": {
        "fn": _generate_image,
        "description": (
            "Generate an image from a text prompt using the locally hosted "
            "image model. Saved as an attachment; returns an inline preview "
            "plus a download link. Use for \"draw / generate / create an "
            "image, logo, illustration, poster, avatar…\". "
            "Params: prompt (English gives the best result), model (optional, "
            "default 'x/flux2-klein:4b'), width / height (default 768), steps "
            "(default 4 — it is a turbo model, 4-8 is enough), "
            "negative_prompt (optional), filename (optional)."
        ),
    },
    "modify_document": {
        "fn": _modify_document,
        "description": (
            "Rewrite an attached file KEEPING its design (fonts, headings, "
            "colours, images, tables, headers/footers, slides, sheets). Use it "
            "whenever a file is attached and the user asks for a text "
            "transformation: translation, rewrite, simplification, paraphrase, "
            "anonymisation, redaction, style fix. Formats: .docx, .xlsx "
            "(.xlsm), .pptx, .pdf (rewritten as .docx). "
            "STRICT PROTOCOL — the prompt carries a `===BEGIN MARKED===` block "
            "numbering every editable unit `[1] …`, `[2] …`. Return the SAME "
            "numbers in the SAME order in `new_marked`, one per line, with the "
            "modified text: do not renumber, skip, merge, or add markdown (the "
            "source already carries the formatting). "
            "Params: source_attachment_id (int, read from `ATTACHMENT_ID: "
            "<n>`), new_marked (str), filename (optional), language (optional "
            "— set it for ar/he/fa/ur to enable RTL)."
        ),
    },
}


def list_tools_for_prompt():
    return "\n".join(
        f"- **{name}** — {info['description']}"
        for name, info in TOOLS.items()
    )


# ── OpenAI-style schemas for /api/chat tool calling ──────────────────────────

_TOOL_PARAM_SCHEMAS = {
    "whoami": {"type": "object", "properties": {}},
    "odoo_models_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Keyword to look for in the technical name or the label."},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
    },
    "odoo_fields_get": {
        "type": "object",
        "properties": {
            "model": {"type": "string",
                      "description": "Technical model name, e.g. 'account.move'."},
            "attributes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["model"],
    },
    "odoo_search_read": {
        "type": "object",
        "properties": {
            "model": {"type": "string",
                      "description": "Technical Odoo model name."},
            "domain": {"type": "array",
                       "description": "Odoo domain: a list of [field, op, value] triples. "
                                      "Operators: =, !=, in, not in, >, >=, <, <=, ilike, like. "
                                      "Combinators '&' (default), '|' (OR), '!' (NOT), prefix form."},
            "fields": {"type": "array", "items": {"type": "string"},
                       "description": "ALWAYS list ONLY the fields you "
                                      "need for the answer (e.g. for a stat "
                                      "'per salesperson': the salesperson "
                                      "field plus the measured field). You "
                                      "already know the fields from "
                                      "odoo_fields_get — pick the 2-4 useful "
                                      "ones. Do NOT leave this empty: empty "
                                      "means EVERY field of EVERY row "
                                      "(descriptions, notes, relations…), "
                                      "which floods the context and is very "
                                      "expensive. Leave it empty only if the "
                                      "user explicitly asks for the full "
                                      "detail of one record."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0},
            "order": {"type": "string",
                      "description": "Ordre, ex: 'id desc' ou 'create_date desc, id desc'."},
        },
        "required": ["model"],
    },
    "odoo_read": {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "integer"}},
            "fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["model", "ids"],
    },
    "export_records_to_file": {
        "type": "object",
        "properties": {
            "model": {"type": "string",
                      "description": "Odoo model, e.g. 'hr.employee', "
                                     "'project.project', 'account.move'."},
            "filename": {"type": "string",
                         "description": "Name with extension: .pdf, .xlsx, "
                                        ".docx or .csv."},
            "domain": {"type": "array",
                       "description": "Optional Odoo filter [field,op,value]."},
            "fields": {"type": "array", "items": {"type": "string"},
                       "description": "Columns. Empty = automatic selection."},
            "title": {"type": "string"},
            "layout": {"type": "string", "enum": ["table", "detailed"],
                       "description": "'detailed' = one section per "
                                      "record (analysis)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        "required": ["model", "filename"],
    },
    "odoo_search_count": {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "domain": {"type": "array"},
        },
        "required": ["model"],
    },
    "odoo_call": {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "method": {"type": "string",
                       "description": "Public method. Refused: unlink, private methods (_*)."},
            "args": {"type": "array"},
            "kwargs": {"type": "object"},
        },
        "required": ["model", "method"],
    },
    "generate_file": {
        "type": "object",
        "description": (
            "Create a downloadable file and return its URL. Use it "
            "whenever the user asks you to produce a document "
            "(translation, rewrite, conversion, export). Supported "
            "formats: "
            "TEXT (.txt/.md/.csv/.json/.xml/.html/.py/.js/.css/.yml/"
            ".tsv); "
            "Word (.docx) — each line of `content` becomes a "
            "paragraph; "
            "Excel (.xlsx) — each line of `content` is a spreadsheet "
            "row, columns separated by TAB, `|` or comma; "
            "PowerPoint (.pptx) — sections delimited by "
            "`=== Slide title ===` (each section becomes a slide with "
            "a title and a body)."
        ),
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "File name with its extension, e.g. "
                    "'translated.docx', 'report.xlsx', 'pitch.pptx', "
                    "'export.json'."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The full content. DOCX/PDF: Markdown (# headings, "
                    "**bold**, lists, `| a | b |` tables). ADVANCED "
                    "PDF: you may also write complete HTML+CSS for a "
                    "rich layout (columns, colours, headers/footers, "
                    "base64 images, SVG charts) — the renderer detects "
                    "HTML automatically. XLSX: a Markdown table or "
                    "plain rows (columns separated by TAB, `|` or "
                    "comma). PPTX: sections separated by "
                    "`=== Title ===`. Text: raw content."
                ),
            },
            "mimetype": {
                "type": "string",
                "description": (
                    "Optional MIME type; guessed from the extension "
                    "when absent."
                ),
            },
        },
        "required": ["filename", "content"],
    },
    "generate_image": {
        "type": "object",
        "description": (
            "Generate an image with the local image-generation model. "
            "Returns an inline preview plus a download link."
        ),
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Description of the image. English is recommended "
                    "for better results."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Name of the image-generation model on the local "
                    "server. Default: 'x/flux2-klein:4b'."
                ),
            },
            "width":  {"type": "integer", "default": 768,
                       "minimum": 128, "maximum": 2048},
            "height": {"type": "integer", "default": 768,
                       "minimum": 128, "maximum": 2048},
            "steps":  {"type": "integer", "default": 4,
                       "minimum": 1, "maximum": 50,
                       "description": (
                           "Number of diffusion steps. 4 for turbo / "
                           "lightning models; up to 50 for maximum "
                           "quality."
                       )},
            "negative_prompt": {
                "type": "string",
                "description": (
                    "What must NOT appear in the image."
                ),
            },
            "filename": {"type": "string"},
        },
        "required": ["prompt"],
    },
    "modify_document": {
        "type": "object",
        "description": (
            "Modify an attached file while preserving its design. "
            "Covers translation / rewriting / redaction / "
            "anonymisation on .docx, .xlsx, .pptx, .pdf."
        ),
        "properties": {
            "source_attachment_id": {
                "type": "integer",
                "description": (
                    "Id of the source attachment. Read it from the "
                    "prompt under `ATTACHMENT_ID: <n>`."
                ),
            },
            "new_marked": {
                "type": "string",
                "description": (
                    "The modified content, carrying the SAME `[N] …` "
                    "markers as the MARKED block in the prompt. One "
                    "line per marker, same order, same numbering. "
                    "PLAIN TEXT — no markdown."
                ),
            },
            "filename": {
                "type": "string",
                "description": (
                    "Name of the produced file, with extension, e.g. "
                    "'guide_ar.docx', 'pricelist_en.xlsx'. "
                    "Derived from the source when omitted."
                ),
            },
            "language": {
                "type": "string",
                "description": (
                    "Langue cible (code ISO ou nom). Active le rendu "
                    "RTL pour ar/he/fa/ur."
                ),
            },
        },
        "required": ["source_attachment_id", "new_marked"],
    },
}


# Read-only tools — safe to expose to the chat assistant. Any tool that
# can write to Odoo data (odoo_call) is excluded by default. Record
# creation in Odoo lives in the Document Imports backend flow, not in
# the chat.
# `generate_file` is included even though it creates an ir.attachment:
# the file is scoped to the user (res_model=res.users, res_id=uid) and
# never touches real Odoo records — it just produces a downloadable
# document for the user (translation, export, conversion, etc.).
# Tools exposed to the chat assistant. `odoo_call` is included so the
# assistant can CREATE / WRITE / call action methods (e.g. create a
# task in a project, post an invoice, confirm a sale order). Safety
# net stays at the dispatcher level:
#   • `unlink` and `execute*` are blocked in DENIED_METHODS.
#   • Private methods (prefix `_`) are blocked.
#   • Every operation runs through `request.env` → the user's ACL and
#     record rules apply; no escalation possible.
# The constant name is kept for back-compat; "READ_ONLY" no longer
# fits the chat's capability surface but renaming it touches too
# many call sites.
READ_ONLY_TOOL_NAMES = frozenset({
    "whoami",
    "odoo_models_search",
    "odoo_fields_get",
    "odoo_search_read",
    "odoo_read",
    "odoo_search_count",
    "odoo_call",
    "generate_file",
    "export_records_to_file",
    "modify_document",
    "generate_image",
})

# ── Intent-scoped tool sets ──────────────────────────────────────────────────
# Sending ALL 11 tool schemas on every turn costs ~1500-2500 tokens of fixed
# prefill — including the four heavy file/image/modify schemas on a plain
# "list my invoices" lookup. We gate the tool set by the turn's intent so the
# model only ever sees the tools it could plausibly need. Fewer tools also
# means fewer wrong-tool mispicks by small models → more precise behaviour.
#
# The data-read core is always present (a "file" or "action" turn still has to
# fetch real rows first). `odoo_call` (write/method) is added for action + file
# because a DB-export file often issues create/method-shaped calls, and a
# lookup that escalates to a write mid-turn must not be left toolless.
_TOOL_CORE_READ = (
    "whoami", "odoo_models_search", "odoo_fields_get",
    "odoo_search_read", "odoo_read", "odoo_search_count",
)
_TOOLSETS_BY_INTENT = {
    # Greetings / identity questions need no DB and no file tools at all.
    "greeting": (),
    "identity": (),
    # A read: the model picks a model, builds a domain, reads. No writes,
    # no file generation.
    "lookup": _TOOL_CORE_READ,
    # A write action: read core + odoo_call for create/write/action methods.
    "action": _TOOL_CORE_READ + ("odoo_call",),
    # File work: read core + odoo_call (DB exports) + the four output tools.
    "file": _TOOL_CORE_READ + (
        "odoo_call", "export_records_to_file", "generate_file",
        "modify_document", "generate_image",
    ),
}


def tool_names_for_intent(intent):
    """Return the tuple of tool names to expose for a classified `intent`.

    Falls back to the full read-only set for any unknown intent so a
    misclassification never strands the model without tools."""
    if intent in _TOOLSETS_BY_INTENT:
        return _TOOLSETS_BY_INTENT[intent]
    return tuple(READ_ONLY_TOOL_NAMES)


def build_tools_schema(read_only=True, only=None):
    """Return the tools schema in the standard OpenAI tool-call format.

    `read_only=True` (default) filters to the safe-for-chat subset above.
    Pass `read_only=False` to get every tool — only do that for code that
    actually needs to write, like the doc-import publish path (which
    doesn't go through the LLM tool dispatcher anyway).

    `only` (optional) — an iterable of tool names. When given, the result is
    further restricted to those names (intersected with the read-only subset
    when `read_only`). Used by the chat loop to send an intent-scoped tool set
    instead of all 11 schemas every turn. An empty `only` returns [] (no tools).
    """
    allow = None if only is None else set(only)
    out = []
    for name, info in TOOLS.items():
        if read_only and name not in READ_ONLY_TOOL_NAMES:
            continue
        if allow is not None and name not in allow:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"],
                "parameters": _TOOL_PARAM_SCHEMAS.get(name, {"type": "object", "properties": {}}),
            },
        })
    return out


def dispatch(env, name, params):
    tool = TOOLS.get(name)
    if not tool:
        _cprint("red", "TOOL ERROR", f"Unknown tool: {name}")
        return {"error": _("Unknown tool: %(name)s. Valid tools: %(tools)s",
                           name=name, tools=list(TOOLS))}
    _cprint("cyan", "TOOL CALL", f"→ {name}  params={_truncate(str(params), 200)}")
    try:
        result = tool["fn"](env, params or {})
        _cprint("green", "TOOL RESULT", f"← {name}  result={_truncate(str(result), 300)}")
        return result
    except AccessError as e:
        _logger.warning(
            "DIGI-ERP AI ACCESS DENIED | uid=%s (%s) | tool=%s | params=%s | reason=%s",
            env.uid, env.user.name, name, _truncate(str(params), 200), _truncate(str(e), 300),
        )
        _cprint("red", "ACCESS DENIED",
                f"uid={env.uid} ({env.user.name}) tried {name} "
                f"params={_truncate(str(params), 150)} → {_truncate(str(e), 200)}")
        return {"error": f"Access denied: {_truncate(e)}"}
    except MissingError as e:
        _cprint("yellow", "TOOL MISSING", f"{name} → {_truncate(str(e))}")
        return {"error": _("Record not found: %s", _truncate(e))}
    except (UserError, ValidationError) as e:
        _cprint("yellow", "TOOL BIZZ ERR", f"{name} → {_truncate(str(e))}")
        return {"error": f"Business error: {_truncate(e)}"}
    except Exception as e:
        _logger.exception("Tool %s crashed", name)
        _cprint("red", "TOOL CRASH", f"{name} → {type(e).__name__}: {_truncate(str(e))}")
        return {"error": f"{type(e).__name__}: {_truncate(e)}"}
