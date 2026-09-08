# -*- coding: utf-8 -*-
"""
RAG-based schema injection for DIGI-ERP AI.

Instead of dumping the full Odoo schema (~80 KB) into every system prompt —
which trips Cloudflare's 100 s timeout because the model has to attend over
20 K tokens before generating a single output — this module:

  1. Builds vector embeddings for each business model (technical name +
     human label + key field names + first lines of help text). Done once
     per (Odoo install + custom addons) signature.
  2. Persists the embeddings to disk so subsequent Odoo restarts are instant.
  3. At query time, embeds the user's prompt and retrieves the K most
     semantically relevant models, then formats only THOSE into a compact
     schema block injected into the system prompt.

Result: per-request schema is ~3-5 KB instead of 80 KB. The model can still
fall back to `odoo_models_search` / `odoo_fields_get` for anything missing.

Setup requirement: the embedding model must be available on the AI server
(for example `nomic-embed-text`).
"""
import ast
import hashlib
import json
import logging
import os
import time
from threading import Lock

import numpy as np
import requests

_logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Endpoint + model come from ir.config_parameter at request time
# (ai_config.get_ai_config). The constants below are legacy fallbacks
# only used in log lines / error messages before settings have been saved.
EMBED_BASE_URL_DEFAULT = "http://localhost:11434"
EMBED_MODEL_DEFAULT    = "nomic-embed-text"
EMBED_TIMEOUT  = 90       # per HTTP call — kept tight; embedding is fast
EMBED_BATCH    = 32       # models per /api/embed call

DEFAULT_TOP_K  = 18       # how many models to inject per query. A broad
                          # candidate list lets the 32B pick the right model in
                          # ONE shot instead of extra odoo_models_search /
                          # odoo_fields_get round-trips. Trimmed 28 → 18: the
                          # SCHEMA_BLOCK_TOKEN_BUDGET (6000 tok) already caps how
                          # many models actually fit, so the tail past ~18 was
                          # low-relevance noise that only inflated the cache
                          # WRITE on topic-drift misses without helping the pick.
                          # (History: 15 → 28 → 18; before that 5 → 10 → 15.)
                          # Previously bumped from 5 → 10: now that the chat can CREATE
                          # records via `odoo_call`, the model often
                          # needs the schema of a "context" model
                          # (project.project) AND the target model
                          # (project.task) AND a related lookup model
                          # (res.users for assignment, …). 5 was tight.
MIN_SIMILARITY = 0.18     # lowered from 0.25 — action queries like
                          # "create a task in project Acme" embed
                          # poorly because the embedding model treats
                          # the project name as a noisy proper noun.
                          # 0.18 still excludes unrelated models but
                          # lets the relevant ones through.

CACHE_DIR  = os.path.expanduser("~/.cache/digi_erp_ai")
INDEX_FILE = os.path.join(CACHE_DIR, "schema_index.npz")
META_FILE  = os.path.join(CACHE_DIR, "schema_index.json")

# Same skip lists used by the previous full-schema dumper. Models in these
# prefixes are too technical / too noisy for AI tool-use.
_SKIP_MODEL_PREFIXES = (
    "base.import", "base.language", "base.setup",
    "bus.",
    "ir.actions", "ir.cron", "ir.default", "ir.exports",
    "ir.filters", "ir.logging", "ir.translation", "ir.ui",
    "mail.followers", "mail.notification", "mail.push", "mail.thread",
    "report.", "res.lang", "resource.calendar.leaves", "web.",
)

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
_SKIP_TTYPES         = frozenset({"binary", "html", "serialized"})

MAX_FIELDS_PER_MODEL = 120  # bumped 60 → 120 for large-context models.
                            # Real business models (account.move,
                            # sale.order, hr.employee) have well over 60
                            # fields; capping at 60 hid the exact field the
                            # model needed for a `create`/`write`, forcing a
                            # follow-up odoo_fields_get. The big context +
                            # 32B recall make a full field list cheap and
                            # strictly better. (was 60, before that 35.)

# Hard ceiling on the SIZE of the injected schema block, in tokens. 28 models
# × 120 fields can serialise to >10k tokens, which — added to the tool
# definitions, screen context and history — can overflow a 16k-token context
# window ("maximum context length is 16384 tokens. However, your request has
# 17621 input tokens"). We now stop adding models once this budget
# is spent, so the block scales with the model, never overflows it. The most
# relevant models come first (sorted by similarity), so the ones dropped are
# the least relevant. ~1 token ≈ 4 chars (rough but safe for budgeting).
SCHEMA_BLOCK_TOKEN_BUDGET = 6000
_CHARS_PER_TOKEN = 4


# ── ANSI helpers (mirror the rest of the addon) ──────────────────────────────

_C = {
    "reset":   "\033[0m", "bold":   "\033[1m",
    "cyan":    "\033[96m", "green":  "\033[92m",
    "yellow":  "\033[93m", "red":    "\033[91m",
    "blue":    "\033[94m", "magenta": "\033[95m",
}

def _cprint(color, label, msg=""):
    # Routed through the Odoo logger so output lands in the configured
    # logfile. A bare print() would only reach stdout/journald, which
    # split DIGI-ERP AI output across two places on a prod deployment.
    # `color` is kept for call-site compatibility but is no longer used.
    _logger.info("[%s] %s", label, msg)


# ── In-memory index (per Odoo worker) ────────────────────────────────────────

# Short-lived circuit-breaker for embed failures. If the embed endpoint is
# down/connection-refused, we don't want EVERY subsequent chat call to wait
# the full HTTP timeout (~20 s). Once a failure happens, skip the next embed
# attempts for _EMBED_DOWN_COOLDOWN seconds.
_EMBED_DOWN_COOLDOWN = 60.0  # seconds
_embed_down_until = 0.0      # unix timestamp; 0 = healthy

_index_lock = Lock()
_index = {
    "hash":       None,   # sha256 of (model, [field:type, ...]) signature
    "models":     None,   # list of {"model": str, "label": str}
    "embeddings": None,   # np.ndarray (N, D), L2-normalised
}


# ── Filtering helpers ─────────────────────────────────────────────────────────

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
    if not sel_str:
        return ""
    try:
        pairs = ast.literal_eval(sel_str) if isinstance(sel_str, str) else list(sel_str)
        return ",".join(str(k) for k, _ in pairs[:8])
    except Exception:
        return ""


def _format_field_token(field):
    fname, ttype = field["name"], field["ttype"]
    token = f"{fname}:{ttype}"
    rel = field.get("relation") or ""
    if rel:
        token += f"→{rel}"
    elif ttype == "selection":
        keys = _sel_keys(field.get("selection") or "")
        if keys:
            token += f"[{keys}]"
    return token


# ── ORM scan ──────────────────────────────────────────────────────────────────

def _collect_models(env):
    """Yield (model_name, label, info, [field_dicts]) for every indexable model."""
    IrModel  = env["ir.model"]
    IrFields = env["ir.model.fields"]
    rows = IrModel.search_read(
        [("transient", "=", False)],
        ["id", "model", "name", "info"],
        order="model",
    )
    out = []
    for r in rows:
        mname = r["model"]
        if _skip_model(mname) or mname not in env:
            continue
        frows = IrFields.search_read(
            [("model_id", "=", r["id"])],
            ["name", "ttype", "relation", "selection", "field_description", "help"],
            order="name",
            limit=MAX_FIELDS_PER_MODEL + 30,
        )
        good = []
        for f in frows:
            if _skip_field(f["name"], f["ttype"]):
                continue
            good.append(f)
            if len(good) >= MAX_FIELDS_PER_MODEL:
                break
        if not good:
            continue
        out.append((mname, r["name"], r.get("info") or "", good))
    return out


def _build_document(model_name, label, info, fields):
    """Build the text that gets embedded for this model. The richer this is,
    the better semantic retrieval works (within reason — keep under 512 tok).

    Critically, we also unpack `selection` field values into their human
    labels. Without them, base Odoo models like `account.move` (which is
    labeled 'Journal Entry') have no embedded vocabulary for 'Invoice',
    'Bill', 'Credit Note' — those words only exist as selection values of
    the `move_type` field. Adding them here is the difference between
    'account.move' being retrieved for an invoice doc or being buried
    behind 6 PO-shaped models because both share the same field layout."""
    field_labels = " ".join(
        (f.get("field_description") or f["name"]) for f in fields[:40]
    )
    field_names = " ".join(f["name"] for f in fields[:40])
    info_clean  = (info or "").replace("\n", " ").strip()[:300]

    # Selection values are stored as a string repr of list-of-tuples in
    # ir.model.fields.selection. Parse safely; tolerate the field also
    # arriving as a python list already (Odoo cache quirks).
    import ast
    selection_labels = []
    for f in fields[:40]:
        sel = f.get("selection")
        if not sel:
            continue
        try:
            parsed = ast.literal_eval(sel) if isinstance(sel, str) else sel
        except (ValueError, SyntaxError):
            continue
        if not isinstance(parsed, (list, tuple)):
            continue
        for item in parsed:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                selection_labels.append(str(item[1]))
    # Cap to keep the embedded document under the encoder's window.
    selection_block = " ".join(selection_labels[:80])

    return (
        f"{model_name} | {label} | {info_clean} | "
        f"fields: {field_names} | {field_labels} | "
        f"types: {selection_block}"
    )


# Bump this when the document builder changes (e.g. adding selection
# labels) — it's mixed into the schema signature so the on-disk cache
# is treated as stale and rebuilt with the new descriptions.
_DOC_BUILDER_VERSION = b"v6-40fields-80sel"


def _signature(collected):
    """Stable hash of the schema layout — used to detect drift and re-embed."""
    h = hashlib.sha256()
    h.update(_DOC_BUILDER_VERSION)
    h.update(b"\n")
    for mname, _label, _info, fields in collected:
        h.update(mname.encode())
        h.update(b"\0")
        for f in fields:
            h.update(f["name"].encode())
            h.update(b":")
            h.update(f["ttype"].encode())
            h.update(b"\0")
        h.update(b"\n")
    return h.hexdigest()


# ── Embedding call ───────────────────────────────────────────────────────────

def _embed(env, texts, kind="document"):
    """Call the embedding endpoint, return L2-normalised np.ndarray (N, D).

    Two endpoint variants are tried in order:
      • POST /api/embed         (newer servers, batched, payload `input`)
      • POST /api/embeddings    (older servers, one string per call,
                                 payload `prompt`)

    The fall-back triggers on 404 from the newer endpoint, so an older
    host keeps working without an upgrade. nomic-embed-text
    benefits from task prefixes so we always inject them.
    """
    from .ai_config import get_ai_config
    cfg = get_ai_config(env)
    base = cfg["embed_base_url"].rstrip("/")
    model = cfg["embed_model"]
    prefix = "search_query: " if kind == "query" else "search_document: "
    inputs = [prefix + (t or "") for t in texts]

    # 1. Try the batched modern endpoint first.
    try:
        resp = requests.post(
            f"{base}/api/embed",
            json={"model": model, "input": inputs},
            timeout=EMBED_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(str(e))

    if resp.status_code == 404:
        # 2. Fall back to the legacy single-string endpoint.
        _cprint("yellow", "RAG EMBED OLD",
                f"{base}/api/embed → 404, falling back to /api/embeddings "
                f"(older server). Upgrade it for ~10× faster batched calls.")
        embs = []
        for one in inputs:
            try:
                r = requests.post(
                    f"{base}/api/embeddings",
                    json={"model": model, "prompt": one},
                    timeout=EMBED_TIMEOUT,
                )
            except requests.exceptions.RequestException as e:
                raise RuntimeError(str(e))
            if not r.ok:
                try:
                    err = r.json().get("error", r.reason)
                except Exception:
                    err = r.reason
                raise RuntimeError(f"Indexing service error: {err}")
            data = r.json()
            emb = data.get("embedding")
            if not emb:
                raise RuntimeError(f"The indexing service returned no embedding: {data}")
            embs.append(emb)
        arr = np.asarray(embs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    if not resp.ok:
        try:
            err_msg = resp.json().get('error', resp.reason)
        except Exception:
            err_msg = resp.reason
        raise RuntimeError(f"Indexing service error: {err_msg}")

    data = resp.json()
    embs = data.get("embeddings") or []
    if not embs:
        raise RuntimeError(f"The indexing service returned no embeddings: {data}")
    arr = np.asarray(embs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


# ── Disk cache ────────────────────────────────────────────────────────────────

def _load_disk_cache():
    if not (os.path.exists(INDEX_FILE) and os.path.exists(META_FILE)):
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        npz = np.load(INDEX_FILE)
        return {
            "hash":       meta["hash"],
            "models":     meta["models"],
            "embeddings": npz["embeddings"],
        }
    except Exception as e:
        _logger.warning("DIGI-ERP AI RAG: disk cache read failed: %s", e)
        return None


def _save_disk_cache(state):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.savez(INDEX_FILE, embeddings=state["embeddings"])
        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump({"hash": state["hash"], "models": state["models"]}, f)
        _cprint("green", "RAG SAVE", f"{INDEX_FILE} ({state['embeddings'].nbytes // 1024} KB)")
    except Exception as e:
        _logger.warning("DIGI-ERP AI RAG: disk cache write failed: %s", e)


# ── Index building ────────────────────────────────────────────────────────────

def _build_index(env):
    """Build (or reuse) the embedding index. Idempotent; safe under the lock."""
    t0 = time.time()
    collected = _collect_models(env)
    sig = _signature(collected)

    if _index["hash"] == sig and _index["embeddings"] is not None:
        return _index

    disk = _load_disk_cache()
    if disk and disk["hash"] == sig:
        _index.update(disk)
        _cprint("green", "RAG LOAD",
                f"{len(disk['models'])} models from disk cache  ({time.time() - t0:.1f}s)")
        return _index

    from .ai_config import get_ai_config
    cfg = get_ai_config(env)
    _cprint("magenta", "RAG BUILD",
            f"embedding {len(collected)} models with {cfg['embed_model']}…")

    models_meta, documents = [], []
    for mname, label, info, fields in collected:
        models_meta.append({"model": mname, "label": label})
        documents.append(_build_document(mname, label, info, fields))

    chunks = []
    for i in range(0, len(documents), EMBED_BATCH):
        batch = documents[i:i + EMBED_BATCH]
        chunk = _embed(env, batch, kind="document")
        chunks.append(chunk)
        _cprint("cyan", "RAG EMBED",
                f"{min(i + EMBED_BATCH, len(documents))}/{len(documents)}")
    embeddings = np.vstack(chunks).astype(np.float32)

    state = {"hash": sig, "models": models_meta, "embeddings": embeddings}
    _index.update(state)
    _save_disk_cache(state)

    _cprint("green", "RAG READY",
            f"{len(models_meta)} models  dim={embeddings.shape[1]}  "
            f"{time.time() - t0:.1f}s total")
    return state


def ensure_index(env):
    """Lazy build — first request after a fresh start pays the cost."""
    if _index["embeddings"] is not None:
        return True
    with _index_lock:
        if _index["embeddings"] is not None:
            return True
        try:
            _build_index(env)
            return True
        except Exception as e:
            from .ai_config import get_ai_config
            cfg = get_ai_config(env)
            _logger.warning("DIGI-ERP AI RAG: index build failed (%s). "
                            "Make sure the model %s is available on the AI server.",
                            e, cfg["embed_model"])
            _cprint("red", "RAG ERROR", f"{type(e).__name__}: {e}")
            return False


def invalidate_index():
    """Force a full rebuild on next request (on-disk cache is also cleared)."""
    with _index_lock:
        _index.update({"hash": None, "models": None, "embeddings": None})
    for path in (INDEX_FILE, META_FILE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve_relevant_model_names(env, query, k=DEFAULT_TOP_K):
    """Same retrieval pipeline as `retrieve_relevant_schema`, but returns
    just the list of model technical names (e.g. ['account.move',
    'sale.order', ...]) in similarity-ranked order. Empty list if retrieval
    is unavailable. Used by the merged classify-and-extract step which
    needs to build its OWN per-candidate field blocks."""
    global _embed_down_until
    if not query or not query.strip():
        return []

    now = time.time()
    if _embed_down_until and now < _embed_down_until:
        return []

    if not ensure_index(env):
        _embed_down_until = now + _EMBED_DOWN_COOLDOWN
        return []

    try:
        q_emb = _embed(env, [query], kind="query")
    except Exception as e:
        _logger.warning("DIGI-ERP AI RAG: query embed failed: %s", e)
        _embed_down_until = now + _EMBED_DOWN_COOLDOWN
        return []

    _embed_down_until = 0.0

    sims = (_index["embeddings"] @ q_emb[0])
    order = np.argsort(-sims)
    top = [int(i) for i in order if sims[int(i)] >= MIN_SIMILARITY][:k]
    return [_index["models"][i]["model"] for i in top
            if _index["models"][i]["model"] in env]


def retrieve_candidate_models(env, query, k=DEFAULT_TOP_K):
    """Embed `query` and return the top-K semantically relevant models as a
    list of dicts: [{"model": str, "label": str, "sim": float}, …], ranked
    by similarity. Empty list if retrieval is unavailable.

    This is the raw retrieval step. `retrieve_relevant_schema` formats these
    into a prompt block; the LLM re-rank pass (see controllers.main
    `_rerank_candidate_models`) filters this shortlist down before formatting.
    Separated so the re-rank can inspect the (name, label, sim) triples and
    drop the noise BEFORE we pay to serialise full field lists for each one."""
    global _embed_down_until
    if not query or not query.strip():
        return []

    now = time.time()
    if _embed_down_until and now < _embed_down_until:
        return []

    if not ensure_index(env):
        _embed_down_until = now + _EMBED_DOWN_COOLDOWN
        return []

    try:
        q_emb = _embed(env, [query], kind="query")  # (1, D)
    except Exception as e:
        _logger.warning("DIGI-ERP AI RAG: query embed failed: %s", e)
        _cprint("red", "RAG QUERY ERR", f"{type(e).__name__}: {e}")
        _embed_down_until = now + _EMBED_DOWN_COOLDOWN
        _cprint("yellow", "RAG SKIPPING",
                f"embed host appears down — disabling RAG for {_EMBED_DOWN_COOLDOWN:.0f}s")
        return []

    # Successful embed — make sure the circuit-breaker is reset.
    _embed_down_until = 0.0

    sims = (_index["embeddings"] @ q_emb[0])           # (N,)
    order = np.argsort(-sims)
    top = [int(i) for i in order if sims[int(i)] >= MIN_SIMILARITY][:k]
    if not top:
        _cprint("yellow", "RAG NO MATCH",
                f"best sim={sims[order[0]]:.3f} < threshold {MIN_SIMILARITY}")
        return []

    _cprint("cyan", "RAG RETRIEVE",
            f"top-{len(top)} for {query[:60]!r}  "
            + "  ".join(f"{_index['models'][i]['model']}({sims[i]:.2f})" for i in top[:5]))

    out = []
    for idx in top:
        meta = _index["models"][idx]
        if meta["model"] not in env:
            continue
        out.append({
            "model": meta["model"],
            "label": meta["label"],
            "sim": float(sims[idx]),
        })
    return out


def build_schema_block_for_models(env, candidates,
                                  token_budget=SCHEMA_BLOCK_TOKEN_BUDGET):
    """Format a compact schema block for an explicit, already-chosen list of
    candidate models. `candidates` is a list of {"model", "label", "sim"}
    dicts (the shape `retrieve_candidate_models` returns, optionally filtered
    by the LLM re-rank pass). Returns "" if nothing usable remains.

    Kept separate from retrieval so the re-rank can prune the shortlist
    first — we only pay to serialise field lists for the models that survive.

    `token_budget` caps the TOTAL size of the block (est. chars/4). Candidates
    are consumed most-relevant-first; once the budget is spent we stop adding
    models so the block never overflows the LLM's context window. The single
    most relevant model is always included (at least trimmed) even if it alone
    would exceed the budget, so we never return an empty block for a real hit."""
    if not candidates:
        return ""

    IrModel  = env["ir.model"]
    IrFields = env["ir.model.fields"]
    # The header below is deliberately blunt: smaller models (qwen2.5:7b,
    # otherwise treat this block as a tool-result they have to
    # react to, and ask the user "what do you want me to do with this?"
    # instead of calling a tool. The explicit "REFERENCE — NOT a tool
    # result" framing fixed the regression.
    lines = [
        "=== MODEL REFERENCE (data FOR you — not a tool result, not a user "
        f"message). Top {len(candidates)} models retrieved for this prompt. "
        "===\n"
        "Pick a model here, then call `odoo_search_read` / `odoo_call` — do "
        "not ask the user first. Not listed? `odoo_models_search`.\n"
        "Format: model.name (Label) [relevance X%]\n"
        "  field:type  field:many2one→target  field:selection[v1,v2]\n"
        "These field lists are AUTHORITATIVE (name, type, relation target and "
        "selection values are all shown) — never call `odoo_fields_get` for a "
        "model listed here.\n\n"
    ]

    # Running size estimate so the block never overflows the model's context.
    budget_chars = max(0, token_budget) * _CHARS_PER_TOKEN
    used_chars = sum(len(x) for x in lines)   # header cost
    used = 0
    truncated = False
    for cand in candidates:
        mname = cand["model"]
        if mname not in env:
            continue
        mid_rec = IrModel.search_read([("model", "=", mname)], ["id"], limit=1)
        if not mid_rec:
            continue
        frows = IrFields.search_read(
            [("model_id", "=", mid_rec[0]["id"])],
            ["name", "ttype", "relation", "selection"],
            order="name",
        )
        parts = []
        for f in frows:
            if _skip_field(f["name"], f["ttype"]):
                continue
            parts.append(_format_field_token(f))
            if len(parts) >= MAX_FIELDS_PER_MODEL:
                break
        if not parts:
            continue
        sim_pct = int(cand.get("sim", 0.0) * 100)
        entry = (
            f"{mname} ({cand['label']})  [relevance {sim_pct}%]\n  "
            + "  ".join(parts) + "\n"
        )
        # Budget check — but ALWAYS include the first (most relevant) model,
        # trimming its field list to fit rather than dropping it entirely.
        if used and used_chars + len(entry) > budget_chars:
            truncated = True
            break
        if not used and len(entry) > budget_chars:
            # The single top model alone blows the budget: keep only as many
            # fields as fit, so the model still gets its most relevant schema.
            fit_fields = []
            head_len = len(f"{mname} ({cand['label']})  [relevance {sim_pct}%]\n  \n")
            room = budget_chars - used_chars - head_len
            for p in parts:
                if room - (len(p) + 2) < 0:
                    break
                fit_fields.append(p)
                room -= (len(p) + 2)
            entry = (
                f"{mname} ({cand['label']})  [relevance {sim_pct}%]\n  "
                + "  ".join(fit_fields or parts[:20]) + "\n"
            )
            truncated = True
        lines.append(entry)
        used_chars += len(entry)
        used += 1
        if truncated:
            break

    if not used:
        return ""
    if truncated:
        lines.append(
            "(Additional less-relevant models were omitted to fit the context "
            "budget. If the model you need isn't listed, call "
            "`odoo_models_search`.)\n"
        )
    lines.append("=== END REFERENCE — back to the user's actual prompt ===\n")
    _cprint("cyan", "RAG BLOCK",
            f"{used} model(s) serialised  ~{used_chars // _CHARS_PER_TOKEN} tok"
            + ("  [truncated to budget]" if truncated else ""))
    return "".join(lines)


def retrieve_relevant_schema(env, query, k=DEFAULT_TOP_K):
    """Embed `query`, return a compact schema block for the top-K most
    semantically relevant models. Empty string if retrieval is unavailable.

    Thin wrapper over `retrieve_candidate_models` + `build_schema_block_for
    _models` — kept for the many existing call sites that want the one-shot
    "prompt in, block out" behaviour with NO re-rank."""
    candidates = retrieve_candidate_models(env, query, k=k)
    return build_schema_block_for_models(env, candidates)
