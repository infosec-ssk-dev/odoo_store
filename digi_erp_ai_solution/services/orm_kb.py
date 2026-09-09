# -*- coding: utf-8 -*-
"""ORM-method knowledge base for DIGI-ERP AI.

Scrapes the Odoo ORM source tree (orm/models.py, orm/commands.py,
orm/domains.py, orm/decorators.py, orm/environments.py, orm/fields*.py)
via AST, extracts every public class / method / function / constant
with its signature and first paragraph of docstring, embeds the lot,
and retrieves the top-K most relevant entries at chat time.

Why this exists
---------------
The main schema RAG (schema_rag.py) answers "which MODEL holds the
data?" — e.g. "invoice" → account.move. It says nothing about HOW to
manipulate that model: which method to call, what args, what
relational-field commands to use.

This RAG channel answers the second question. When the user asks
"create a task in project Acme", the schema RAG injects the
project.task / project.project schemas, and THIS RAG injects the
`create(vals)` and Command.create method signatures with their
docstrings — so the model doesn't have to guess.

The two channels share the same embedding model and disk-cache
directory; they're independent indexes built and looked up
separately. Both are sub-second after the first cold build.
"""
import ast
import hashlib
import json
import logging
import os
import time
from threading import Lock

import numpy as np

from .schema_rag import _embed, CACHE_DIR

_logger = logging.getLogger(__name__)


# ── Source-files to scrape ───────────────────────────────────────────────

# Files inside <odoo>/orm/ that we extract from. Ordered for log
# readability; the scraper handles each independently.
SOURCE_FILES = (
    "models.py",          # BaseModel + create/write/search/read/copy/etc.
    "commands.py",        # Command enum (0,1,2,3,4,5,6) for O2M/M2M
    "domains.py",         # Domain operators, AND/OR/NOT combinators
    "decorators.py",      # @api.model / @api.depends / @api.onchange / …
    "environments.py",    # Environment / sudo / with_user / context
    "fields.py",          # Field base class
    "fields_relational.py",  # Many2one / One2many / Many2many semantics
    "fields_selection.py",   # Selection field
    "fields_temporal.py",    # Date / Datetime
)


def _orm_dir(env):
    """Locate the Odoo ORM source directory.

    Precedence:
      1. ir.config_parameter digi_erp_ai.orm_source_dir (operator override).
      2. The location of the `odoo.orm` package itself.

    Note `odoo.__file__` is None in Odoo 19 — `odoo` is a namespace
    package — so deriving the path from it silently yields nothing. We
    locate `odoo.orm` directly instead, and keep `odoo.__path__` as a
    second try for layouts where importing the subpackage fails.

    Returns None when neither resolves, which callers treat as "no ORM
    knowledge base available" rather than an error.
    """
    p = (env["ir.config_parameter"].sudo()
            .get_param("digi_erp_ai.orm_source_dir") or "").strip()
    if p and os.path.isdir(p):
        return p
    try:
        import odoo.orm
        cand = os.path.dirname(odoo.orm.__file__)
        if os.path.isdir(cand):
            return cand
    except Exception:
        pass
    try:
        import odoo
        for root in list(getattr(odoo, "__path__", []) or []):
            cand = os.path.join(root, "orm")
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return None


# ── AST scrape ────────────────────────────────────────────────────────────

# How many chars of each docstring's first paragraph to keep. Larger
# = richer retrieval but bigger embeddings index.
MAX_DOC_CHARS = 500


def _build_signature(node):
    """Reconstruct a readable `name(args)` from an ast.FunctionDef."""
    a = node.args
    parts = []
    # positional-or-keyword args
    pos_defaults = list(a.defaults or [])
    n_pos = len(a.args)
    # defaults align to the END of args
    pad = n_pos - len(pos_defaults)
    for i, arg in enumerate(a.args):
        if i >= pad:
            try:
                default = ast.unparse(pos_defaults[i - pad])
                parts.append(f"{arg.arg}={default}")
            except Exception:
                parts.append(f"{arg.arg}=…")
        else:
            parts.append(arg.arg)
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")  # keyword-only separator
    for i, arg in enumerate(a.kwonlyargs):
        if a.kw_defaults and i < len(a.kw_defaults) and a.kw_defaults[i] is not None:
            try:
                default = ast.unparse(a.kw_defaults[i])
                parts.append(f"{arg.arg}={default}")
            except Exception:
                parts.append(f"{arg.arg}=…")
        else:
            parts.append(arg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return f"{node.name}({', '.join(parts)})"


def _first_paragraph(text, max_chars=MAX_DOC_CHARS):
    """First doc paragraph, whitespace-normalised, capped."""
    if not text:
        return ""
    first = text.strip().split("\n\n")[0].strip()
    first = " ".join(first.split())
    return first[:max_chars]


def _scrape_source(path):
    """Yield extracted entries from a Python file. Skips anything
    underscore-prefixed (treated as private)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
    except (OSError, SyntaxError) as e:
        _logger.warning("[ORM KB] skip %s — %s", path, e)
        return
    fname = os.path.basename(path)

    def _walk(node, parent_class=None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                if not child.name.startswith("_"):
                    yield {
                        "name": child.name,
                        "kind": "class",
                        "signature": f"class {child.name}",
                        "doc": _first_paragraph(ast.get_docstring(child)),
                        "source_file": fname,
                        "parent": parent_class,
                    }
                yield from _walk(child, parent_class=child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("_"):
                    continue
                yield {
                    "name": child.name,
                    "kind": "method" if parent_class else "function",
                    "signature": _build_signature(child),
                    "doc": _first_paragraph(ast.get_docstring(child)),
                    "source_file": fname,
                    "parent": parent_class,
                }
            elif isinstance(child, ast.Assign):
                # UPPER_CASE constants — both module-level (e.g.
                # INSERT_BATCH_SIZE in models.py) AND class-level
                # (e.g. Command.CREATE = 0, Command.LINK = 4 — the
                # whole O2M/M2M command vocabulary the chat needs to
                # know about). Both flavours go into the index.
                for tgt in child.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        try:
                            val = ast.unparse(child.value)
                        except Exception:
                            val = "…"
                        # If we're inside a class, include the parent
                        # in the signature so search hits like "Command
                        # CREATE" find Command.CREATE not just CREATE.
                        if parent_class:
                            sig = f"{parent_class}.{tgt.id} = {val}"
                        else:
                            sig = f"{tgt.id} = {val}"
                        yield {
                            "name": tgt.id,
                            "kind": "constant",
                            "signature": sig,
                            "doc": "",
                            "source_file": fname,
                            "parent": parent_class,
                        }

    yield from _walk(tree)


def _collect_entries(env):
    """Walk every source file and return a flat list of entry dicts."""
    orm_dir = _orm_dir(env)
    if not orm_dir:
        _logger.warning(
            "[ORM KB] ORM source not found. Set "
            "ir.config_parameter digi_erp_ai.orm_source_dir to <odoo>/orm "
            "(skipping ORM-method retrieval — main schema RAG still works).",
        )
        return []
    entries = []
    for fname in SOURCE_FILES:
        path = os.path.join(orm_dir, fname)
        if not os.path.exists(path):
            continue
        for e in _scrape_source(path):
            entries.append(e)
    # Dedupe by (parent, name) keeping the first hit — BaseModel is
    # re-exported in a few places and we don't want duplicate methods.
    seen = set()
    out = []
    for e in entries:
        key = (e.get("parent"), e["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# ── Embedding index (mirrors schema_rag's layout) ─────────────────────────

INDEX_FILE = os.path.join(CACHE_DIR, "orm_methods_index.npz")
META_FILE  = os.path.join(CACHE_DIR, "orm_methods_index.json")

# Bump this when the document builder or scrape rules change — forces
# the on-disk cache to be treated as stale and rebuilt.
_DOC_BUILDER_VERSION = b"v1-orm-methods"

DEFAULT_TOP_K  = 4
MIN_SIMILARITY = 0.20

_index_lock = Lock()
_index = {"hash": None, "entries": None, "embeddings": None}


def _entry_document(e):
    """The text that gets embedded for one entry. Concatenate the
    most-searchable parts: name, parent class, kind, signature, and
    the first doc paragraph."""
    parent = f" (in {e['parent']})" if e.get("parent") else ""
    kind = e.get("kind") or ""
    return (
        f"{e['name']}{parent} | {kind} | {e['signature']} | "
        f"{e.get('doc') or ''}"
    )


def _signature_hash(entries):
    h = hashlib.sha256()
    h.update(_DOC_BUILDER_VERSION)
    for e in entries:
        h.update(str(e.get("parent") or "").encode())
        h.update(b"::")
        h.update(e["name"].encode())
        h.update(b"::")
        h.update(e["signature"].encode())
        h.update(b"\n")
    return h.hexdigest()


def _load_disk_cache():
    if not (os.path.exists(INDEX_FILE) and os.path.exists(META_FILE)):
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        npz = np.load(INDEX_FILE)
        return {
            "hash":       meta["hash"],
            "entries":    meta["entries"],
            "embeddings": npz["embeddings"],
        }
    except Exception as e:
        _logger.warning("[ORM KB] disk cache read failed: %s", e)
        return None


def _save_disk_cache(state):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        np.savez(INDEX_FILE, embeddings=state["embeddings"])
        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"hash": state["hash"], "entries": state["entries"]}, f,
            )
        _logger.info(
            "[ORM KB SAVE] %s (%d KB)",
            INDEX_FILE, state["embeddings"].nbytes // 1024,
        )
    except Exception as e:
        _logger.warning("[ORM KB] disk cache write failed: %s", e)


def _build_index(env):
    """Build (or reuse) the ORM-method embedding index. Idempotent
    under the lock."""
    t0 = time.time()
    entries = _collect_entries(env)
    if not entries:
        return None
    sig = _signature_hash(entries)

    if _index["hash"] == sig and _index["embeddings"] is not None:
        return _index

    disk = _load_disk_cache()
    if disk and disk["hash"] == sig:
        _index.update(disk)
        _logger.info(
            "[ORM KB LOAD] %d entries from cache (%.1fs)",
            len(disk["entries"]), time.time() - t0,
        )
        return _index

    _logger.info("[ORM KB BUILD] embedding %d ORM entries…",
                 len(entries))
    documents = [_entry_document(e) for e in entries]

    BATCH = 32
    chunks = []
    for i in range(0, len(documents), BATCH):
        batch = documents[i:i + BATCH]
        chunks.append(_embed(env, batch, kind="document"))
    embeddings = np.vstack(chunks).astype(np.float32)

    state = {"hash": sig, "entries": entries, "embeddings": embeddings}
    _index.update(state)
    _save_disk_cache(state)

    _logger.info(
        "[ORM KB READY] %d entries  dim=%d  %.1fs",
        len(entries), embeddings.shape[1], time.time() - t0,
    )
    return state


def ensure_index(env):
    """Lazy build — first chat call after a fresh start pays the cost
    (~30 s for ~300 entries on nomic-embed-text). Returns True if the
    index is available."""
    if _index["embeddings"] is not None:
        return True
    with _index_lock:
        if _index["embeddings"] is not None:
            return True
        try:
            return _build_index(env) is not None
        except Exception as e:
            _logger.warning("[ORM KB] index build failed: %s", e)
            return False


def invalidate_index():
    """Force a full rebuild on next call. Useful after an Odoo upgrade
    when the ORM source layout may have shifted."""
    with _index_lock:
        _index.update({"hash": None, "entries": None, "embeddings": None})
    for path in (INDEX_FILE, META_FILE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# ── Retrieval ─────────────────────────────────────────────────────────────


def retrieve_relevant_orm(env, query, k=DEFAULT_TOP_K):
    """Return a compact text block listing the top-K ORM methods /
    classes / constants most relevant to `query`. Empty string if
    retrieval is unavailable (no source, no embed model, sim < min)."""
    if not query or not query.strip():
        return ""
    if not ensure_index(env):
        return ""

    try:
        q_emb = _embed(env, [query], kind="query")
    except Exception as e:
        _logger.warning("[ORM KB] query embed failed: %s", e)
        return ""

    sims = (_index["embeddings"] @ q_emb[0])
    order = np.argsort(-sims)
    top = [int(i) for i in order if sims[int(i)] >= MIN_SIMILARITY][:k]
    if not top:
        return ""

    lines = [
        "=== RELEVANT ORM METHODS (top "
        f"{len(top)} retrieved semantically) ===",
        "Format: `signature`  (in ParentClass — file.py)",
        "        — first sentence of the docstring",
        "",
    ]
    for i in top:
        e = _index["entries"][i]
        parent = f"  (in {e['parent']})" if e.get("parent") else ""
        loc = f" — {e['source_file']}"
        lines.append(f"• `{e['signature']}`{parent}{loc}")
        if e.get("doc"):
            lines.append(f"    {e['doc']}")
    lines.append("")
    return "\n".join(lines)
