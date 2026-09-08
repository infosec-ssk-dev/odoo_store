# -*- coding: utf-8 -*-
import io
import json
import logging
import os
import re

import requests

from odoo import _, fields, http
from odoo.http import request, Response

from ..services.tools import (
    MAX_TOOL_RESULT_LEN,
    TOOLS as _TOOLS_REGISTRY,
    dispatch as tool_dispatch,
    build_tools_schema,
    tool_names_for_intent,
    is_write_call,
    build_action_preview,
)
from ..services.schema_rag import (
    retrieve_relevant_schema,
    retrieve_candidate_models,
    build_schema_block_for_models,
)
from ..services.orm_kb import retrieve_relevant_orm
from ..services.llm_providers import call_chat as remote_call_chat
from ..services.ai_config import get_ai_config

_logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 15         # créer une facture peut nécessiter 7-10 calls
# History limits sized for a model with a large (32k+) context window.
# The old 30-message / 12k-char caps were tuned for a small model on a
# constrained GPU where every token of history competed with the schema RAG
# for a tiny budget — that made the chat "forget" context a few turns back.
# With the
# big context we can keep a real conversation window. If a smaller/local
# model is used on constrained hardware, lower these in code or rely on the
# provider's own num_ctx to bound the actual prompt.
HISTORY_MESSAGES_LIMIT = 60      # plafond de messages réinjectés en contexte
HISTORY_CHARS_LIMIT = 48000      # plafond global de caractères du contexte (~12k tokens)

# All connection settings (URL, model, timeout, context size) now
# live in ir.config_parameter, set from DIGI-ERP AI → Settings. The fallbacks
# below are only used if a request fires before the user has saved settings
# for the first time.
_LOCAL_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}


def _cfg():
    """Pull the current DIGI-ERP AI config from ir.config_parameter.

    Called every time a request hits a local-model helper. Cheap (single
    SQL query batched on the cursor) and means admins editing settings
    take effect on the next request without a restart."""
    return get_ai_config(request.env)


def _local_options(cfg):
    return {"num_ctx": cfg["num_ctx"], "temperature": 0.2}

# Phrases that mean "the model is about to act but didn't emit a tool_call".
# When we see one of these in a text-only response, we nudge it once instead
# of treating the response as final.
_NARRATION_RE = re.compile(
    r"(étape\s*\d|je\s+vais\s+(maintenant\s+)?(chercher|créer|rechercher|"
    r"identifier|procéder|commencer|exécuter|appeler|utiliser|lancer)|"
    r"je\s+commence\s+par|je\s+procède|"
    r"création\s+(de\s+la|du|des).*en\s+cours|"
    r"recherche\s+et\s+identification|"
    r"i\s+(will|am\s+going\s+to|'ll|'m\s+going\s+to)\s+(now\s+)?"
    r"(search|create|look\s+up|find|call|use))",
    re.IGNORECASE,
)


# ── ANSI colour helpers ───────────────────────────────────────────────────────
_C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "cyan":    "\033[96m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "red":     "\033[91m",
    "blue":    "\033[94m",
    "magenta": "\033[95m",
}

def _cprint(color, label, msg=""):
    # Routed through the Odoo logger so output lands in the configured
    # logfile. A bare print() would only reach stdout/journald, which
    # split DIGI-ERP AI output across two places on a prod deployment.
    # `color` is kept for call-site compatibility but is no longer used.
    _logger.info("[%s] %s", label, msg)

# ── Limites pour les pièces jointes ───────────────────────────────────────────
MAX_FILE_BYTES   = 5 * 1024 * 1024   # 5 MB upload max
MAX_EXTRACT_CHARS = 30000            # tronque le texte injecté dans le prompt

TEXTUAL_EXTS = (
    '.txt', '.md', '.csv', '.json', '.log', '.xml',
    '.html', '.htm', '.py', '.js', '.css', '.tsv', '.yml', '.yaml',
)

IMAGE_EXTS = (
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif',
)


def _ocr_normalize_image(blob, max_side=1280):
    """Re-encode the image as a JPEG sized to fit `max_side` on the longest
    edge. This prevents vision errors like 'invalid image index: 0'
    that happen with large PNGs on qwen2.5-vl, and shrinks the payload so
    inference fits in 4 GB VRAM cards without OOM/CPU-offload churn.

    Returns the re-encoded bytes (or the original blob if PIL isn't
    available — the caller still tries and can surface a clearer error)."""
    try:
        from PIL import Image
    except ImportError:
        return blob
    try:
        img = Image.open(io.BytesIO(blob))
        # Drop alpha — JPEG can't hold it and some vision models choke
        # on RGBA from PNGs.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            img = img.resize((int(w * scale), int(h * scale)),
                              Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception:
        return blob


def _ocr_via_cloud_vision(filename, provider, messages):
    """Read an image with a CLOUD vision model, through the normal adapters.

    Takes the exact same `messages` the self-hosted path builds — the image
    on `images: [<base64>]` — and hands it to `call_chat`, which translates
    it per vendor. No tools are passed: this call has one job, look at the
    picture. A model that is not flagged vision-capable is refused inside
    `call_chat` before any request goes out. No "is `model_name` set?" check
    either — the model's own `_check_required` constraint makes an empty one
    unsavable.
    """
    _cprint("blue", "OCR START",
            f"{filename} → {provider.name} "
            f"[{provider.provider_type}:{provider.model_name}] cloud vision")
    out = remote_call_chat(provider, messages)
    if not out.get("ok"):
        err = out.get("error") or _("unknown error")
        _cprint("red", "OCR CLOUD FAIL", f"{provider.name}: {err}")
        # The provider's own words: a model that cannot see says so here
        # (HTTP 400 "image input not supported", a refusal, a bad key…),
        # and the OCR chain moves on to the classic engines.
        return None, _("%(name)s could not read the image: %(err)s",
                       name=provider.name, err=err)
    text = ((out.get("message") or {}).get("content") or "").strip()
    _cprint("green", "OCR DONE", f"{filename}  {len(text)} chars extracted")
    return (text or _("(No text detected in the image.)")), None


def _ocr_via_vision_model(filename, blob, provider_id=None, instruction=None):
    """Send an image to whichever vision model the user has selected.

    Cloud or self-hosted, both work: the image rides on `images: [<base64>]`
    either way, and only the transport differs (a provider adapter vs the
    local /api/chat endpoint). The "OCR AI model" chosen in Settings wins;
    the chat dropdown selection (provider_id) is used when that is empty and
    the picked model can see. Falls back to the global Settings → Chat model
    only when nothing else is registered.

    `instruction` (optional) — when the user asked something ABOUT the
    image ("what is this?", "describe it", "translate the sign") rather
    than a pure text extraction, we send THAT instruction to the vision
    model directly, so it actually looks at the picture and answers the
    question — instead of the default transcribe-everything prompt that
    made it blindly dump the image's text and ignore the user. None =
    the pure-OCR transcription prompt (used for document text layers,
    scanned PDFs, and generic uploads).
    """
    import base64
    cfg = _cfg()

    # ── Resolve WHICH model reads the image ──────────────────────────────
    #
    # Both transports speak the same protocol: `images: [<base64>]` on the
    # user turn. That is the self-hosted /api/chat shape verbatim, and the
    # cloud adapters in services/llm_providers.py translate it into each
    # vendor's own multimodal format (OpenAI image_url, Anthropic image
    # block, Gemini inlineData). So a cloud model is a first-class reader
    # here, not a special case.
    #
    # Order of preference:
    #   1. the "OCR AI model" from Settings — someone went and said "THIS
    #      model reads my documents", which outranks whatever happens to be
    #      selected in the chat picker;
    #   2. the provider the caller handed us (the chat selection), if it can
    #      actually see;
    #   3. any registered vision model, cloud or self-hosted;
    #   4. the global self-hosted settings, as a guess of last resort.
    #
    # What we no longer do is take a cloud provider's `model_name` and post
    # it to the LOCAL server — that answered "which model read my image?"
    # with a completely different one (pick DeepSeek, get a local llama3.2
    # that may not even be running).
    Provider = request.env["digi_erp.model.llm.provider"].sudo()

    passed = None
    if provider_id:
        try:
            cand = Provider.browse(int(provider_id))
            if cand.exists():
                passed = cand
        except (TypeError, ValueError):
            pass  # never let OCR crash on a bad provider id

    prov = None
    raw_ocr = (request.env["ir.config_parameter"].sudo()
               .get_param("digi_erp_ai.ocr_provider_id"))
    if raw_ocr:
        try:
            cand = Provider.browse(int(raw_ocr))
            if cand.exists() and cand.active:
                prov = cand
        except (TypeError, ValueError):
            pass  # stale id in the config table → fall through

    if prov is None and passed is not None and passed.supports_vision:
        prov = passed

    if prov is None:
        prov = Provider.search([
            ("active", "=", True),
            ("supports_vision", "=", True),
        ], limit=1) or None

    # Nothing anywhere can see, and the caller's own pick is text-only. Say
    # which model and what to change, rather than sending an image to a model
    # that will either 400 or invent a description of a picture it never got.
    if prov is None and passed is not None and not passed.supports_vision:
        _cprint("yellow", "OCR NO VISION", f"{passed.name} is text-only")
        return None, _(
            "%(name)s cannot read images. Tick \"Can read images "
            "(OCR / vision)\" on that model if it supports vision, or choose "
            "an OCR AI model under Settings → DIGI-ERP AI.", name=passed.name)

    if prov is not None and prov.model_name:
        ocr_model = prov.model_name
        base_url = (prov.base_url.rstrip("/") if prov.base_url
                    else cfg["local_base_url"])
        source = f"provider #{prov.id} ({prov.name!r})"
    else:
        # Last resort: the global self-hosted settings. Legitimate when the
        # admin runs a vision model there without registering it as a
        # provider — but it is a guess, so say so in the log.
        ocr_model = cfg["chat_model"]
        base_url = cfg["local_base_url"]
        source = "global settings (no vision model registered)"

    # Resize/normalize before base64 so we never push a 5 MB PNG through
    # the vision pipeline.
    blob = _ocr_normalize_image(blob)
    b64 = base64.b64encode(blob).decode()
    _cprint("blue", "OCR START",
            f"{filename}  {len(blob)} bytes (post-normalize) → "
            f"{ocr_model} vision  [{source}]  base={base_url}")
    # Prompt phrasing: "Read … name every visible word" avoids the
    # safety-alignment refusal triggered by "Extract text from this
    # image" on many aligned vision models.
    # No `system` turn — some chat templates
    # mishandle a system message combined with an image and respond with
    # `Failed to create new sequence: invalid image index: 0`. The image
    # binds reliably when the payload is a single user message.
    if instruction and instruction.strip():
        # The user asked something ABOUT the image — answer THAT, looking
        # at the picture. This is what fixes "explain this image" returning
        # a blind text dump: the vision model gets the real question.
        user_content = (
            f"{instruction.strip()}\n\n"
            "Answer the request above by LOOKING AT the attached image. "
            "If the request needs text that appears in the image, read it "
            "accurately; otherwise describe / analyse what you actually "
            "see. Do NOT just transcribe the image unless that is exactly "
            "what was asked.\n"
            "Reply in the same language as the request. Structure your "
            "answer with short Markdown (a heading and/or bullet points) "
            "when it helps readability — do not return one raw undivided "
            "block of text."
        )
    else:
        # Pure transcription — document text layers, scanned PDFs, generic
        # uploads with no specific question.
        user_content = (
            "Read this page. Name every visible word and number "
            "in their natural reading order (top to bottom, left "
            "to right). Preserve line breaks and table structure "
            "where possible. Output only the text you see — no "
            "'I can see' prefix, no apologies, no commentary."
        )
    messages = [{"role": "user", "content": user_content, "images": [b64]}]

    # Cloud model → through the normal adapters, which turn `images` into the
    # vendor's multimodal shape. Self-hosted → straight to /api/chat below,
    # which already speaks this payload natively.
    if prov is not None and not prov.is_local:
        return _ocr_via_cloud_vision(filename, prov, messages)

    payload = {
        "model": ocr_model,
        "messages": messages,
        "stream": False,
        "options": _local_options(cfg),
    }
    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            headers=_LOCAL_HEADERS,
            json=payload, timeout=cfg["request_timeout"],
        )
        if not resp.ok:
            try:
                err_msg = resp.json().get('error', resp.reason)
            except Exception:
                err_msg = resp.reason
            return None, f"Vision OCR error: {err_msg}"
        
        text = ((resp.json().get("message") or {}).get("content") or "").strip()
        _cprint("green", "OCR DONE", f"{filename}  {len(text)} chars extracted")
        return (text or _("(No text detected in the image.)")), None
    except requests.exceptions.ConnectionError:
        # By far the most common failure, and the least self-explanatory in
        # a raw urllib3 traceback: the AI vision reader is the DEFAULT
        # document reader, so an install with no self-hosted server hits
        # this on the very first image. Say which server and what to do.
        _logger.warning(
            "Vision OCR: no self-hosted AI server answering at %s (%s)",
            base_url, filename)
        _cprint("red", "OCR UNREACHABLE", f"{base_url}")
        return None, _(
            "No self-hosted AI server is answering at %s, so the AI vision "
            "reader could not run. Start it, or pick a different document "
            "reader under Settings → DIGI-ERP AI.", base_url)
    except Exception as e:
        _logger.warning("Vision OCR failed (%s): %s", filename, e)
        _cprint("red", "OCR ERROR", f"{filename}: {e}")
        return None, _("OCR failed: %s", e)


# Archive formats we unpack member-by-member. Order matters for the double
# extensions: ".tar.gz" must be tested before ".gz" (str.endswith on the
# tuple is any-match, but _iter_archive_members branches on the longest hit).
ARCHIVE_EXTS = (
    '.zip',
    '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz',
    '.gz', '.bz2', '.xz',
    '.7z', '.rar',
)
MAX_ARCHIVE_MEMBERS = 30   # cap files pulled from a single archive


def _soffice_convert(blob, src_name, target_ext):
    """Convert a legacy binary Office blob (.ppt/.doc/.xls…) to its modern
    equivalent (target_ext, e.g. '.pptx'/'.docx') using LibreOffice headless.

    Returns (converted_bytes, None) on success or (None, error_message) if
    soffice is unavailable or the conversion produced nothing. Runs in an
    isolated temp dir with a private user profile so concurrent chat uploads
    don't collide on LibreOffice's single-instance lock."""
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which('soffice') or shutil.which('libreoffice')
    if not soffice:
        return None, ("Lecture des anciens formats Office (.ppt/.doc) "
                      "indisponible : installez LibreOffice (soffice) sur le "
                      "serveur.")
    fmt = target_ext.lstrip('.')          # 'pptx' / 'docx'
    src_base = os.path.basename(src_name) or f"input{os.path.splitext(src_name)[1]}"
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, src_base)
        with open(src_path, 'wb') as f:
            f.write(blob)
        profile = os.path.join(tmp, 'profile')
        try:
            proc = subprocess.run(
                [soffice, '--headless', '--norestore',
                 f'-env:UserInstallation=file://{profile}',
                 '--convert-to', fmt, '--outdir', tmp, src_path],
                capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return None, _("LibreOffice conversion timed out (file too large?).")
        except Exception as e:
            return None, _("LibreOffice conversion failed: %s", e)
        stem = os.path.splitext(src_base)[0]
        out_path = os.path.join(tmp, f"{stem}.{fmt}")
        if not os.path.exists(out_path):
            err = (proc.stderr or b'').decode('utf-8', 'replace')[:300]
            return None, _("LibreOffice conversion failed (%(fmt)s). %(err)s",
                       fmt=fmt, err=err).strip()
        with open(out_path, 'rb') as f:
            return f.read(), None


def _iter_archive_members(name, blob):
    """Yield (member_name, member_bytes) for every file inside an archive.

    Handles .zip, tar family (.tar/.tar.gz/.tgz/.tar.bz2/.tbz2/.tar.xz/.txz),
    single-stream .gz/.bz2/.xz (one wrapped file), and optionally .7z/.rar
    when their libraries are installed. Returns (members, None) or
    ([], error_message). Members are materialised lazily into a list; the
    per-file byte cap is enforced by the caller."""
    import tarfile
    import zipfile

    try:
        # --- ZIP -----------------------------------------------------------
        if name.endswith('.zip'):
            out = []
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    try:
                        out.append((info.filename, zf.read(info.filename)))
                    except Exception:
                        continue
            return out, None

        # --- TAR family (incl. compressed tarballs) ------------------------
        if name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2',
                          '.tar.xz', '.txz')):
            out = []
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    try:
                        fobj = tf.extractfile(member)
                        out.append((member.name, fobj.read() if fobj else None))
                    except Exception:
                        continue
            return out, None

        # --- Single-stream compression (wraps exactly one file) ------------
        if name.endswith('.gz'):
            import gzip
            inner = name[:-3] or 'contenu'
            return [(os.path.basename(inner), gzip.decompress(blob))], None
        if name.endswith('.bz2'):
            import bz2
            inner = name[:-4] or 'contenu'
            return [(os.path.basename(inner), bz2.decompress(blob))], None
        if name.endswith('.xz'):
            import lzma
            inner = name[:-3] or 'contenu'
            return [(os.path.basename(inner), lzma.decompress(blob))], None

        # --- 7z (optional dep: py7zr) --------------------------------------
        if name.endswith('.7z'):
            try:
                import py7zr
            except ImportError:
                return [], ("Lecture .7z indisponible (installez `py7zr`).")
            out = []
            with py7zr.SevenZipFile(io.BytesIO(blob)) as z:
                for inner_name, bio in (z.readall() or {}).items():
                    out.append((inner_name, bio.read()))
            return out, None

        # --- RAR (optional dep: rarfile + unrar binary) --------------------
        if name.endswith('.rar'):
            try:
                import rarfile
            except ImportError:
                return [], ("Lecture .rar indisponible (installez `rarfile` "
                            "et l'utilitaire `unrar`).")
            out = []
            with rarfile.RarFile(io.BytesIO(blob)) as rf:
                for info in rf.infolist():
                    if info.isdir():
                        continue
                    try:
                        out.append((info.filename, rf.read(info)))
                    except Exception:
                        continue
            return out, None

    except Exception as e:
        _logger.warning("Archive read failed (%s): %s", name, e)
        return [], f"Impossible de lire l'archive : {e}"

    return [], f"Format d'archive non pris en charge : {name}"


def _extract_text_from_file(filename, blob, provider_id=None, want_layout=False,
                            ocr_chain=None):
    """Extrait le texte d'un document. Renvoie (text, error_message).

    `provider_id` (when given) tells the OCR path which LLM provider to
    use — same provider as chat / doc-extract, so a single selection in
    the UI drives every LLM-touching step.

    `ocr_chain` (optional) overrides the admin's configured OCR engine
    order for THIS call — used when a dedicated OCR model must lead the
    chain (see resolve_ocr_strategy "dedicated" mode). When None, the
    order comes from Settings via get_ocr_chain().

    When `want_layout=True`, returns a 3-tuple (text, error, layout).
    `layout` = {"pages": [{"page", "width", "height",
    "boxes": [{"text","bbox"}]}]} — per-page OCR box geometry for the
    editable-PDF preview — or None when no box-aware OCR ran (text-layer
    PDFs, plain text, docx)."""
    name = (filename or '').lower().strip()

    def _ret(text, err, layout=None):
        """Shape the return value to match the `want_layout` flag."""
        return (text, err, layout) if want_layout else (text, err)

    if not blob:
        return _ret(None, "Fichier vide.")

    # OCR engine order (primary first, others as fallbacks). An explicit
    # `ocr_chain` argument (e.g. a dedicated OCR model that must lead) wins;
    # otherwise use the admin's Settings choice. Resolved once here and
    # passed to every ocr_image() call below so a single decision drives
    # image, embedded-image and scanned-PDF OCR alike.
    if ocr_chain is None:
        from ..services.ai_config import get_ocr_chain
        ocr_chain = get_ocr_chain(request.env)

    # 1. Fichiers texte simples
    if name.endswith(TEXTUAL_EXTS):
        for enc in ('utf-8', 'utf-8-sig', 'latin-1'):
            try:
                return _ret(blob.decode(enc), None)
            except UnicodeDecodeError:
                continue
        return _ret(blob.decode('utf-8', errors='replace'), None)

    # 2. Images — go through the layered OCR pipeline (preprocess →
    #    PaddleOCR → Surya → LLM-vision fallback). Lazy import so a
    #    missing dependency doesn't break the controller at load time.
    if name.endswith(IMAGE_EXTS):
        from ..services.ocr import ocr_image
        if want_layout:
            text, err, lay = ocr_image(blob, filename=filename,
                                       provider_id=provider_id,
                                       return_layout=True, chain=ocr_chain)
            pages = [dict(lay, page=0)] if lay else []
            return _ret(text, err, {"pages": pages} if pages else None)
        text, err = ocr_image(blob, filename=filename,
                              provider_id=provider_id, chain=ocr_chain)
        return _ret(text, err)

    # 3. PDF — text layer first, then OCR each page that yielded nothing
    if name.endswith('.pdf'):
        try:
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
        except ImportError:
            return _ret(None, "Lecture PDF indisponible (installez `pypdf` ou `PyPDF2`).")
        try:
            reader = PdfReader(io.BytesIO(blob))
            page_texts = []
            for page in reader.pages:
                try:
                    page_texts.append(page.extract_text() or '')
                except Exception:
                    page_texts.append('')

            # Every page: ALSO OCR any embedded images so the model sees
            # text rendered as images (logos with text, signature blocks,
            # tables-as-image, screenshots inside a doc) — not just the
            # text layer. Capped at 5 images per page to avoid OCR-ing
            # every decorative icon. Failure is silent: we keep the
            # text-layer content.
            from ..services.ocr import ocr_image
            for idx, page in enumerate(reader.pages):
                extras = []
                try:
                    images = list(page.images)
                except Exception:
                    images = []
                for img_idx, img_file in enumerate(images[:5]):
                    try:
                        img_bytes = img_file.data
                    except Exception:
                        continue
                    if not img_bytes or len(img_bytes) < 1024:
                        # Skip tiny decorations.
                        continue
                    try:
                        img_text, _e = ocr_image(
                            img_bytes,
                            filename=f"{filename}#p{idx + 1}-img{img_idx + 1}",
                            provider_id=provider_id, chain=ocr_chain,
                        )
                    except Exception:
                        img_text = None
                    if img_text and img_text.strip():
                        extras.append(
                            f"[Embedded image #{img_idx + 1}: "
                            f"{img_text.strip()}]")
                if extras:
                    page_texts[idx] = (page_texts[idx] + "\n"
                                       + "\n".join(extras))

            # Identify pages where the text layer didn't yield enough content
            # (probably image-only / scanned). Those — and ONLY those — need
            # OCR via pdf2image + a vision model. A clean digital PDF where
            # every page has a real text layer never touches pdf2image.
            need_ocr_pages = [i for i, t in enumerate(page_texts)
                              if len(t.strip()) < 80]

            if not need_ocr_pages:
                _cprint("green", "PDF TEXT",
                        f"{filename}  {len(reader.pages)} page(s) — "
                        f"all text-layer, OCR skipped")
                text = '\n'.join(page_texts).strip()
                return _ret(text or "(Aucun texte extractible.)", None)

            # Some pages have little/no text — OCR them. Lazy-render only
            # those page indices via pdf2image (we don't rasterise the whole
            # PDF when only one page needs help).
            _cprint("yellow", "PDF MIXED",
                    f"{filename}  {len(need_ocr_pages)}/{len(page_texts)} "
                    f"page(s) need OCR — rendering only those")
            try:
                from pdf2image import convert_from_bytes
            except ImportError:
                _cprint("yellow", "PDF OCR SKIP",
                        "pdf2image not installed; image pages won't be OCR'd. "
                        "Install with: pip install pdf2image && apt install poppler-utils")
                text = '\n'.join(page_texts).strip()
                return _ret(text or _("(No extractable text — the PDF is probably scanned.)"), None)

            from ..services.ocr import ocr_image
            final_chunks = list(page_texts)  # start with text-layer pages
            pages_layout = []                # per-OCR'd-page box geometry
            for idx in need_ocr_pages:
                _cprint("cyan", "PDF OCR",
                        f"page {idx + 1}/{len(page_texts)} of {filename}")
                # pdf2image uses 1-based page numbers via first_page/last_page.
                imgs = convert_from_bytes(blob, dpi=150,
                                          first_page=idx + 1, last_page=idx + 1)
                if not imgs:
                    continue
                img_bytes = io.BytesIO()
                imgs[0].save(img_bytes, format="PNG")
                # Per-page OCR for scanned PDFs: same layered pipeline as
                # for image uploads. When a layout is wanted, capture the
                # box geometry for this page too.
                if want_layout:
                    ocr_text, _err, lay = ocr_image(
                        img_bytes.getvalue(),
                        filename=f"{filename}#page{idx + 1}",
                        provider_id=provider_id, return_layout=True,
                        chain=ocr_chain,
                    )
                    if lay:
                        pages_layout.append(dict(lay, page=idx))
                else:
                    ocr_text, _err = ocr_image(
                        img_bytes.getvalue(),
                        filename=f"{filename}#page{idx + 1}",
                        provider_id=provider_id, chain=ocr_chain,
                    )
                if ocr_text and ocr_text != _("(No text detected in the image.)"):
                    final_chunks[idx] = ocr_text
            text = '\n'.join(c for c in final_chunks if c).strip()
            layout = {"pages": pages_layout} if pages_layout else None
            return _ret(
                text or _("(No extractable text — the PDF is probably scanned.)"),
                None, layout)
        except Exception as e:
            _logger.warning("PDF extraction failed (%s): %s", filename, e)
            return _ret(None, f"Impossible de lire le PDF : {e}")

    # 4. DOCX
    if name.endswith('.docx'):
        try:
            import docx  # python-docx
        except ImportError:
            return _ret(None, "Lecture DOCX indisponible (installez `python-docx`).")
        try:
            doc = docx.Document(io.BytesIO(blob))
            paragraphs = [p.text for p in doc.paragraphs if p.text]
            for table in doc.tables:
                for row in table.rows:
                    paragraphs.append(' | '.join(cell.text for cell in row.cells))
            return _ret('\n'.join(paragraphs).strip() or "(Document vide.)", None)
        except Exception as e:
            _logger.warning("DOCX extraction failed (%s): %s", filename, e)
            return _ret(None, f"Impossible de lire le DOCX : {e}")

    # 5. Excel — .xlsx / .xlsm
    if name.endswith(('.xlsx', '.xlsm')):
        try:
            from openpyxl import load_workbook
        except ImportError:
            return _ret(None, "Lecture Excel indisponible "
                              "(installez `openpyxl`).")
        try:
            wb = load_workbook(io.BytesIO(blob),
                               read_only=True, data_only=True)
            chunks = []
            for sn in wb.sheetnames:
                ws = wb[sn]
                chunks.append(f"=== Sheet: {sn} ===")
                row_count = 0
                for row in ws.iter_rows(values_only=True):
                    if not any(c is not None for c in row):
                        continue
                    chunks.append(" | ".join(
                        "" if c is None else str(c) for c in row))
                    row_count += 1
                    if row_count >= 2000:   # cap per-sheet to keep prompt sane
                        chunks.append("[…rows truncated…]")
                        break
                chunks.append("")
            return _ret("\n".join(chunks).strip() or "(Empty workbook.)",
                        None)
        except Exception as e:
            _logger.warning("XLSX extraction failed (%s): %s", filename, e)
            return _ret(None, f"Impossible de lire le fichier Excel : {e}")

    # 6. Legacy Excel — .xls
    if name.endswith('.xls'):
        try:
            import xlrd
        except ImportError:
            return _ret(None, "Lecture XLS (legacy) indisponible "
                              "(installez `xlrd<2`).")
        try:
            book = xlrd.open_workbook(file_contents=blob)
            chunks = []
            for sn in book.sheet_names():
                sh = book.sheet_by_name(sn)
                chunks.append(f"=== Sheet: {sn} ===")
                for r in range(min(sh.nrows, 2000)):
                    row_vals = [str(sh.cell_value(r, c) or "")
                                for c in range(sh.ncols)]
                    if any(v.strip() for v in row_vals):
                        chunks.append(" | ".join(row_vals))
                chunks.append("")
            return _ret("\n".join(chunks).strip() or "(Empty workbook.)",
                        None)
        except Exception as e:
            _logger.warning("XLS extraction failed (%s): %s", filename, e)
            return _ret(None, f"Impossible de lire le fichier XLS : {e}")

    # 6b. Legacy Office — .ppt / .doc (binary 97-2003 formats).
    #     python-pptx / python-docx only read the modern XML formats, so we
    #     convert the old binary blob to its modern equivalent with
    #     LibreOffice headless, then re-enter this function so the existing
    #     .pptx / .docx branch does the actual text extraction. Requires
    #     `soffice` on the PATH (LibreOffice); if it's missing we say so
    #     instead of failing with an opaque "unsupported type".
    if name.endswith(('.ppt', '.doc')):
        target = '.pptx' if name.endswith('.ppt') else '.docx'
        converted, conv_err = _soffice_convert(blob, name, target)
        if conv_err:
            return _ret(None, conv_err)
        # Recurse with the converted bytes under a modern name so the
        # .pptx / .docx handler picks it up. Layout isn't produced for
        # Office docs, so the tuple shape from the recursive call matches.
        return _extract_text_from_file(
            filename + target, converted,
            provider_id=provider_id, want_layout=want_layout,
            ocr_chain=ocr_chain,
        )

    # 7. PowerPoint — .pptx
    if name.endswith('.pptx'):
        try:
            from pptx import Presentation
        except ImportError:
            return _ret(None, "Lecture PPTX indisponible "
                              "(installez `python-pptx`).")
        try:
            prs = Presentation(io.BytesIO(blob))
            chunks = []
            for idx, slide in enumerate(prs.slides):
                chunks.append(f"=== Slide {idx + 1} ===")
                for shape in slide.shapes:
                    txt = getattr(shape, "text", None)
                    if txt and txt.strip():
                        chunks.append(txt.strip())
                if getattr(slide, "has_notes_slide", False) \
                        and slide.has_notes_slide:
                    notes = slide.notes_slide.notes_text_frame.text
                    if notes and notes.strip():
                        chunks.append(f"[Speaker notes]: {notes.strip()}")
                chunks.append("")
            return _ret("\n".join(chunks).strip() or "(Empty presentation.)",
                        None)
        except Exception as e:
            _logger.warning("PPTX extraction failed (%s): %s", filename, e)
            return _ret(None, f"Impossible de lire le PPTX : {e}")

    # 8. Archives — .zip, .tar(.gz/.bz2/.xz), .tgz, .gz, .bz2, .xz, .7z, .rar.
    #    Every member is extracted and run back through THIS function one by
    #    one (recursively), so a ZIP of PDFs/Excels/images is analysed member
    #    by member — and a nested archive is unpacked too. `.gz`/`.bz2`/`.xz`
    #    that wrap a plain single file (not a tarball) are decompressed then
    #    recursed with the inner name (foo.csv.gz → foo.csv).
    if name.endswith(ARCHIVE_EXTS):
        members, arch_err = _iter_archive_members(name, blob)
        if arch_err:
            return _ret(None, arch_err)
        chunks = []
        n_files = 0
        for inner_name, inner_blob in members:
            if n_files >= MAX_ARCHIVE_MEMBERS:
                chunks.append("[…remaining files in archive skipped "
                              f"(cap = {MAX_ARCHIVE_MEMBERS})…]")
                break
            if inner_blob is None or len(inner_blob) > MAX_FILE_BYTES:
                continue
            # Recursion: the inner call only needs the text (no layout for
            # chat-side analysis of archive contents). Depth is bounded by
            # MAX_ARCHIVE_MEMBERS and by MAX_FILE_BYTES per member.
            inner_text, inner_err = _extract_text_from_file(
                inner_name, inner_blob,
                provider_id=provider_id, want_layout=False,
                ocr_chain=ocr_chain,
            )
            if inner_text:
                chunks.append(f"=== {inner_name} ===\n{inner_text}\n")
                n_files += 1
        if not chunks:
            return _ret("(Archive vide ou aucun fichier lisible.)", None)
        return _ret("\n".join(chunks), None)

    return _ret(None, f"Type de fichier non pris en charge : {filename}")


# A request is "pure text extraction" when the user explicitly wants the
# words out of the image (OCR). Anything else about an image — "what is
# this", "describe", "explain", "is this a cat", "translate the sign",
# "how much is the total on this receipt" — is a VISION question that must
# be answered by looking at the picture, not by dumping its text layer.
_IMAGE_EXTRACT_RE = re.compile(
    r"\b("
    r"ocr|"
    r"extract\s+(?:the\s+)?text|extrais?\s+le\s+texte|"
    r"transcri(?:be|re|s|ption)|"
    r"read\s+(?:the\s+)?text|lis\s+le\s+texte|"
    r"copy\s+(?:the\s+)?text|copie\s+le\s+texte|"
    r"get\s+(?:the\s+)?text\s+(?:from|out)|"
    r"texte\s+(?:de|dans)\s+(?:l'?image|la\s+photo)"
    r")\b",
    re.IGNORECASE,
)


def _image_wants_vision_answer(prompt):
    """True when an attached IMAGE should be answered by the vision model
    directly (describe / explain / analyse / translate / Q&A) rather than
    OCR'd to text. False only when the user explicitly asked for pure text
    extraction, OR gave no prompt at all (then we default to transcription
    so the text is available for later questions)."""
    if not prompt or not prompt.strip():
        return False
    if _IMAGE_EXTRACT_RE.search(prompt):
        return False
    return True


def _build_prompt_with_doc(prompt, filename, extracted_text):
    """Construit un prompt enrichi avec le contenu du document, tronqué si besoin."""
    text = extracted_text or ''
    truncated_note = ''
    if len(text) > MAX_EXTRACT_CHARS:
        text = text[:MAX_EXTRACT_CHARS]
        truncated_note = f"\n[... document truncated at {MAX_EXTRACT_CHARS} characters ...]"
    return (
        "Tu es un assistant utile. L'utilisateur a joint le document suivant. "
        "Use its content to answer their question.\n\n"
        f"=== DOCUMENT : {filename} ===\n"
        f"{text}{truncated_note}\n"
        f"=== FIN DU DOCUMENT ===\n\n"
        f"Question de l'utilisateur :\n{prompt}"
    )


def _call_local_generate(prompt):
    """Appel HTTP /api/generate vers le serveur local.
    Renvoie {ok, response/error, model}."""
    cfg = _cfg()
    _cprint("blue", "LOCAL /generate", f"prompt={prompt[:120]!r}…")
    try:
        resp = requests.post(
            f"{cfg['local_base_url']}/api/generate",
            headers=_LOCAL_HEADERS,
            json={
                "model": cfg["chat_model"],
                "prompt": prompt,
                "stream": False,
                "options": _local_options(cfg),
            },
            timeout=cfg["request_timeout"],
        )
        if not resp.ok:
            try:
                err_msg = resp.json().get('error', resp.reason)
            except Exception:
                err_msg = resp.reason
            return {'ok': False, 'error': f"AI service error: {err_msg}"}

        data = resp.json()
        text = (data.get('response') or '').strip()
        _cprint("green", "LOCAL /generate OK", f"{len(text)} chars")
        return {'ok': True, 'response': text, 'model': cfg["chat_model"]}
    except requests.exceptions.Timeout:
        t = cfg["request_timeout"]
        _logger.warning("Local model timeout (>%ss)", t)
        _cprint("red", "LOCAL /generate TIMEOUT", f">{t}s")
        return {'ok': False,
                'error': _('Timeout: the model took more than %ss to answer.', t)}
    except requests.exceptions.ConnectionError as e:
        _logger.error("Local model connection refused: %s", e)
        _cprint("red", "LOCAL /generate CONN ERR", str(e)[:200])
        return {'ok': False,
                'error': _("Cannot reach %s. Check the URL under "
                            "DIGI-ERP AI → Settings.", cfg["local_base_url"])}
    except Exception as e:
        _logger.exception("Local model unexpected error")
        _cprint("red", "LOCAL /generate ERR", f"{type(e).__name__}: {e}")
        return {'ok': False, 'error': str(e)}


# ── Boucle d'utilisation d'outils (tool-use) ──────────────────────────────────

# Cheap, no-LLM intent router. Decides which heavy blocks to inject
# into the system prompt so we don't waste 1500+ tokens of prefill on
# blocks the model doesn't need for this turn.
_INTENT_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|salut|bonjour|bonsoir|merci|thanks?|thx|"
    r"ok|d'accord|bye|au\s+revoir|cool|nice|good|"
    r"yes|oui|no|non|"
    r"\?+|\.+|👍|👋)\s*[!\.\?]*\s*$",
    re.IGNORECASE,
)
_INTENT_FILE_RE = re.compile(
    r"\b("
    r"docx|xlsx|pptx|csv|pdf|fichier|file|document|"
    r"translate|traduit?|traduis|traduction|translation|"
    r"draw|dessine|generate.*image|génère.*image|"
    r"export(?:e|er|s)?|"
    r"image|picture|illustration|logo|poster|avatar"
    r")\b",
    re.IGNORECASE,
)
_INTENT_ACTION_RE = re.compile(
    r"\b("
    r"create|cree|crée|créer|créé|ajoute|ajouter|add|"
    r"update|modifie|modifier|change(?:r)?|"
    r"delete|supprime|supprimer|"
    r"send|envoie|envoyer|post|valide|valider|confirm|confirme|"
    r"assign|assigne|cancel|annule|annuler|link|relie|relier|"
    r"set|fix|réinitialise"
    r")\b",
    re.IGNORECASE,
)
# A REPORT is an analytical document — the model must gather real data,
# INTERPRET it (trends, totals, anomalies, comparisons) and write a
# structured narrative, not just tabulate rows. Distinct from a plain
# 'file' export (raw table dump) so it gets its own prompt block that
# demands analysis + rich HTML layout. Kept separate from _INTENT_FILE_RE
# because the words below ("analyse", "synthèse", "bilan"…) signal INTENT
# to interpret, which a plain export must not do.
_INTENT_REPORT_RE = re.compile(
    r"\b("
    r"rapport|report|"
    r"analys(?:e|er|es|é|is)|analyt\w*|"
    r"synth[èe]se|synthétis\w*|"
    r"bilan|"
    r"[ée]tude|study|"
    r"aper[çc]u|overview|"
    r"tableau\s+de\s+bord|dashboard|"
    r"compte[- ]rendu|résum[ée]\s+(?:analytique|détaillé)|"
    r"insights?|recommandations?|recommendations?|"
    r"tendances?|trends?|"
    r"graphiques?|graphs?|charts?|"
    r"courbes?|diagrammes?|histogrammes?|camembert|"
    r"interpr[ée]t\w*"
    r")\b",
    re.IGNORECASE,
)


def _classify_query_intent(prompt):
    """Return one of 'greeting' / 'identity' / 'report' / 'file' / 'action' / 'lookup'.

    Used by the system-prompt builder to inject only the rules the
    current query actually needs. Saves 1000-2000 tokens of prefill
    on common queries — keeps the chat snappy on small GPUs.

    'lookup' is the default — safe because it's the most common shape
    and includes the schema RAG (needed for reads) without the larger
    ORM cheat sheet (needed only for writes).

    'identity' ("who are you", "what can you do") needs NO DB access and
    NO schema RAG — the model self-describes from the identity block. It
    must be checked BEFORE 'file'/'action' because "what can you do" and
    "how does this work" would otherwise trip those regexes and drag in
    the whole schema dump for a question about the bot itself. But a real
    verb ("who are you, give me all invoices") must NOT be swallowed —
    so identity only wins when there is NO strong file/action/data
    signal alongside it."""
    if not prompt or not prompt.strip():
        return "greeting"
    if len(prompt) < 200 and _INTENT_GREETING_RE.match(prompt):
        return "greeting"
    # An uploaded file announces itself by carrying ATTACHMENT_ID in
    # the augmented user prompt — treat that as a 'file' intent so the
    # 3-tool file block is injected even if the verb sits elsewhere.
    if "ATTACHMENT_ID" in prompt or "===BEGIN MARKED===" in prompt:
        return "file"
    # Identity/capability question about the bot itself — only when it
    # isn't riding alongside a real data/file/action request.
    if (_COMPONENT_PATTERNS["identity"].search(prompt)
            and not _INTENT_FILE_RE.search(prompt)
            and not _INTENT_REPORT_RE.search(prompt)
            and not _INTENT_ACTION_RE.search(prompt)
            and not _DATA_FETCH_STRONG_RE.search(prompt)):
        return "identity"
    # Report BEFORE file/action: "génère un rapport d'analyse des ventes"
    # matches both _INTENT_REPORT_RE and (via "génère"/"pdf") _INTENT_FILE_RE,
    # but it needs the analytical block, not the raw-export block. A pure
    # write verb still wins over report, though — "crée un rapport" as a DB
    # record (rare) would be caught by action; but the analytical reading is
    # far more common for these words, so report is checked first here and we
    # only fall through to action when NO report signal is present.
    if _INTENT_REPORT_RE.search(prompt):
        return "report"
    if _INTENT_FILE_RE.search(prompt):
        return "file"
    if _INTENT_ACTION_RE.search(prompt):
        return "action"
    return "lookup"


def _resolve_schema_block(user, user_prompt, conv=None):
    """Return the schema-RAG block to inject, using a per-conversation cache.

    The block itself (field lists for ~28 candidate models, + optional 7B
    re-rank) is the single most expensive prompt component and it barely
    changes across a thread that stays on one topic. Strategy:

      1. Always run the CHEAP step: one query-embed → candidate model set
         (`retrieve_candidate_models`). This is a single HTTP call and it's
         what we use to decide whether the topic drifted.
      2. If `conv` has a cached block AND the freshly-retrieved top models
         are all already covered by the cache (subset test), REUSE the
         cached block — we skip the expensive per-model field serialisation
         and the re-rank LLM call entirely.
      3. Otherwise rebuild the block (optionally re-ranked), then store it
         + its model set on the conversation for the next turn.

    Fail-open: any error, no conv, or embedding unavailable → fall back to
    the original one-shot `retrieve_relevant_schema` path. Never worse than
    before, just cheaper on the hit path."""
    env = user.env
    rerank_on = (
        env["ir.config_parameter"].sudo()
        .get_param("digi_erp_ai.rerank_enabled", "False")
    ) in ("True", "true", "1")

    # No conversation to cache against → original behaviour.
    if conv is None:
        if rerank_on:
            candidates = retrieve_candidate_models(env, user_prompt)
            candidates = _rerank_candidate_models(
                env, user_prompt, candidates, conv=None)
            return build_schema_block_for_models(env, candidates)
        return retrieve_relevant_schema(env, user_prompt)

    try:
        # Step 1 — cheap retrieval (embed only, no field serialisation).
        candidates = retrieve_candidate_models(env, user_prompt)
        if not candidates:
            # Embedding unavailable / no match. If we have a cached block,
            # reuse it (better than an empty block); else return nothing.
            return conv.schema_cache_block or ""

        new_models = [c["model"] for c in candidates]
        cached_csv = conv.schema_cache_models or ""
        cached_models = set(filter(None, cached_csv.split(",")))

        # Step 2 — cache HIT test. We only need the models the model will
        # actually reach for; the top few dominate. Reuse the cache when
        # every one of the top retrieved models is already covered by it.
        TOP_N = 6
        top_new = set(new_models[:TOP_N])
        if (conv.schema_cache_block and cached_models
                and top_new.issubset(cached_models)):
            _cprint("green", "SCHEMA CACHE HIT",
                    f"conv#{conv.id} — top {len(top_new)} models covered by "
                    f"cache ({len(cached_models)} models); skipping rebuild")
            return conv.schema_cache_block

        # Step 3 — cache MISS → (re-rank +) rebuild, then store.
        if rerank_on:
            candidates = _rerank_candidate_models(
                env, user_prompt, candidates, conv=conv)
        block = build_schema_block_for_models(env, candidates)
        stored_models = ",".join(c["model"] for c in candidates)
        try:
            conv.sudo().write({
                "schema_cache_models": stored_models[:2000],
                "schema_cache_block": block or "",
            })
            _cprint("cyan", "SCHEMA CACHE STORE",
                    f"conv#{conv.id} — cached {len(candidates)} models "
                    f"(~{len(block or '') // 4} tok)")
        except Exception:
            _logger.exception(
                "DIGI-ERP AI: failed to store schema cache on conv#%s",
                getattr(conv, "id", "?"))
        return block
    except Exception:
        # Any failure in the cache path must not break the chat — fall back
        # to the plain one-shot retrieval.
        _logger.exception("DIGI-ERP AI: schema cache path failed, "
                          "falling back to plain retrieval")
        return retrieve_relevant_schema(env, user_prompt)


def _build_system_prompt(user, user_prompt, extra_context="", conv=None):
    """Build the system prompt for the chat.

    Assembled CONDITIONALLY from what the current query actually needs (see
    `_classify_query_intent`): greetings get identity only; lookups add the
    data rules + schema RAG; actions add the ORM cheat sheet + method RAG;
    file and report work add their own block.

    The wording is deliberately terse. An earlier revision spelled every rule
    out at length because the small models in use then needed
    the hand-holding; on today's models that prose was pure prefill cost. Rules
    were compressed, not dropped — anything that changed behaviour is still
    here, just said once and said short. Static text went from ~4.2k to ~1.6k
    tokens; a plain lookup now carries ~800 tokens of instructions instead of
    ~1.9k, and a report ~1.3k instead of ~3.3k.

    Two things are deliberately NOT repeated here: per-tool usage details
    (they live in each tool's own description, which is sent alongside) and
    the model/field reference (the schema RAG block, which is dynamic)."""
    intent = _classify_query_intent(user_prompt)
    today = fields.Date.context_today(user).isoformat()

    # ── Block 1: identity + audience. Always included.
    blocks = [
        f"You are **DIGI-ERP AI**, an assistant inside Odoo.\n"
        f"User: {user.name} (uid {user.id}, company "
        f"{user.company_id.name}). Today: {today}.\n"
        f"Reply in the user's language. No LaTeX — use Unicode symbols.\n"
        "\n"
        "Speak business, not code. Never show a technical identifier in a "
        "visible answer: no model names (say \"invoice\", not `account.move`), "
        "no field names (say \"total\", not `amount_total`), no method names, "
        "domains, module names or raw ids. Name a record the way the user "
        "knows it (\"invoice INV/2026/0007\"). Technical names are for your "
        "tool calls only. Exception: a developer explicitly asking for one."
    ]

    # ── Greeting → stop here.
    if intent == "greeting":
        return "\n".join(blocks) + ("\n\n" + extra_context if extra_context else "")

    # ── Identity / capability question → self-describe, no tools, no RAG.
    if intent == "identity":
        blocks.append(
            "The user is asking about YOU. Answer briefly from this; call no "
            "tool, query nothing.\n"
            "You are DIGI-ERP AI, an assistant embedded in this Odoo. On "
            "request you can: look up and count any data the user is allowed "
            "to see; create or update records and run actions; produce "
            "PDF/Excel/Word/PowerPoint files and exports; translate or rewrite "
            "an attached document keeping its layout; and generate images. "
            "Everything runs under the user's own Odoo permissions. Invite "
            "them to ask for a concrete task."
        )
        return "\n".join(blocks) + ("\n\n" + extra_context if extra_context else "")

    # ── Block 2: data rules. Every non-greeting turn — a turn the regex
    # tagged "lookup" may still need to write, and a "file" export still
    # needs correct domains.
    if intent in ("lookup", "action", "file", "report"):
        blocks.append(
            "=== DATA RULES ===\n"
            "Use the tools for every Odoo fact — never invent an id, name, "
            "number or amount. The server enforces the user's permissions; on "
            "`Accès refusé`, say so. `unlink` and private methods (`_*`) are "
            "blocked.\n"
            "Domains: `[['field','op',value], …]` — ops =, !=, in, ilike, "
            ">=, <=; combinators '&', '|', '!'.\n"
            "\n"
            "Pick fields by TYPE, never by matching the user's words against "
            "`name`:\n"
            "• a MOMENT IN TIME (year, month, week, today, quarter, range) → a "
            "date/datetime field with >=, <, =. A year is never an ilike on "
            "`name`; if the model has no date field, ask.\n"
            "• a STATUS / state / flag / archived → a boolean or selection "
            "field; its valid values are in the reference block below.\n"
            "• a PERSON (owner, manager, assignee, customer, vendor) → the "
            "many2one to users/partners/employees — filter by id, or dotted: "
            "`[['field.name','=',value]]`.\n"
            "• ANOTHER RECORD (department, category, project, journal, tag) → "
            "the many2one/many2many to that model, by id or dotted.\n"
            "• an AMOUNT, COUNT or QUANTITY → an integer/float field with a "
            "comparison operator.\n"
            "• `[['name','ilike',value]]` ONLY when the user literally asks by "
            "name (\"called X\", \"containing Z\").\n"
            "If several fields fit (date_start vs date_deadline vs "
            "create_date), take the most business-meaningful one or ask a "
            "one-line question. Field lists with types, relations and "
            "selection values are in the reference block below — call "
            "`odoo_fields_get` only for a model NOT listed there.\n"
            "\n"
            "COUNTING: \"how many\" / a total → `odoo_search_count`, not "
            "`odoo_search_read`.\n"
            "AGGREGATION: a sum/average/count grouped by something → ONE "
            "`odoo_call(model='X', method='read_group', args=[domain, "
            "['measure:sum'], ['group_field']])`, never row-by-row. Group "
            "dates with a granularity: `['date_field:month']`.\n"
            "TIME WINDOW: never count the future — bound any period reaching "
            "past today at today's date (given above).\n"
            "DONE vs PLANNED: \"réalisées / effectuées / terminées / done / "
            "closed\" also needs the completed state, or dates ≤ today.\n"
            "LISTING: \"all / every\" means every row — no sampling, no \"and "
            "N more\"; use a markdown table. A `truncation_note` in a result "
            "means rows were withheld: page with `offset`, or hand over the "
            "full set with `export_records_to_file`. Never invent the missing "
            "rows."
        )

    # ── Block 3: ORM cheat sheet. Cheap, and a read that escalates to a
    # write mid-turn must not be left blind — so lookups get it too.
    if intent in ("lookup", "action", "file", "report"):
        blocks.append(
            "=== ORM CHEAT SHEET (with `odoo_call`) ===\n"
            "CREATE: odoo_call(model='X', method='create', "
            "args=[{\"field\":\"value\"}])\n"
            "WRITE:  odoo_call(model='X', method='write', "
            "args=[[id1,id2], {\"field\":\"value\"}])\n"
            "SEARCH: odoo_call(model='X', method='search', "
            "args=[[['name','=','foo']]], kwargs={\"limit\":1})\n"
            "ACTION: odoo_call(model='account.move', method='action_post', "
            "args=[[123]])\n"
            "o2m/m2m commands in vals: (0,0,{vals}) create · (1,id,{vals}) "
            "update · (2,id) delete · (3,id) detach · (4,id) link · (5,) "
            "detach all · (6,0,[ids]) replace.\n"
            "\"Create X in context Y\" → find Y's id, create with it, confirm "
            "with the returned id."
        )

    # ── Block 4: file tools. Which tool to reach for; each tool's own
    # parameters stay in its schema description rather than being repeated.
    if intent == "file":
        blocks.append(
            "=== FILE TOOLS ===\n"
            "• `export_records_to_file` — THE DEFAULT for any file containing "
            "Odoo data (\"a pdf/excel with all the employees…\", \"export…\", "
            "\"a detailed report of…\"). You pass model + filename; the code "
            "fetches every matching record and fills the file from the real "
            "data, so you never transcribe rows. `layout='detailed'` gives one "
            "section per record. ONE call — never `odoo_search_read` then "
            "`generate_file` by hand, that drops and mixes up rows. Unsure "
            "which model? `odoo_models_search` first.\n"
            "• `generate_file` — only for content you author yourself (letter, "
            "memo, notes, plan). `content` = Markdown, or full HTML+CSS for a "
            "rich-layout .pdf. Never put fabricated Odoo data here.\n"
            "• `modify_document` — a file is attached and the user wants a text "
            "transformation (translate, rewrite, redact, anonymise, "
            "summarise). Return `new_marked` with the SAME marker numbers in "
            "the SAME order, no markdown; pass `source_attachment_id` from "
            "`ATTACHMENT_ID:<n>`.\n"
            "• `generate_image` — draw / generate an image, logo, "
            "illustration, poster, avatar. Build the prompt in English. Relay "
            "the tool's `message` verbatim.\n"
            "After a file tool returns, your CHAT reply is the download link "
            "plus one sentence — never paste the file's content into chat. "
            "(The FILE itself stays as complete as the request demands.)"
        )

    # ── Block 4-bis: report / analysis — an analytical document, not a
    # table dump. Workflow + skeleton so the model doesn't default to one
    # markdown table.
    if intent == "report":
        blocks.append(
            "=== REPORT MODE ===\n"
            "The user wants an ANALYTICAL DOCUMENT, not a data dump. A wall of "
            "table rows is a failure here.\n"
            "\n"
            "1. GATHER real data first — never invent a number. "
            "`odoo_search_count` for totals; `read_group` (via `odoo_call`) "
            "for any sum/average grouped by period, category, person or state; "
            "group dates with a granularity (`['date_order:month']`) to get "
            "one row per month. Take a top-N from the read_group RESULT, not "
            "by eye. A \"par X\" table is ONE read_group — never page raw rows "
            "with `offset` to build a summary, that loops without finishing. "
            "If a result is truncated, switch to `read_group` (to aggregate) "
            "or `export_records_to_file` (for a full listing). Issue every "
            "call you need BEFORE writing a word: every figure, name and "
            "percentage in the report must come from a tool result THIS turn.\n"
            "\n"
            "2. INTERPRET — this is the point. State the key numbers (totals, "
            "averages, shares, growth vs a prior period). Call out trends, "
            "top/bottom performers, anomalies, and what they mean for the "
            "business. Give conclusions and concrete recommendations. Tables "
            "are evidence supporting your prose, not a replacement for it.\n"
            "\n"
            "3. PRODUCE with `generate_file` (.pdf unless the user asks "
            "otherwise). Pass `content` as a FULL HTML document so the "
            "renderer applies real layout:\n"
            "   <h1> title + period/scope\n"
            "   <h2>Executive summary</h2> — 3-6 sentences of narrative\n"
            "   <h2>Key figures</h2> — a small styled <table>, not raw rows\n"
            "   <h2>Analysis</h2> — trends, comparisons, top/bottom, anomalies; "
            "detailed tables here as supporting evidence\n"
            "   <h2>Recommendations</h2> — an ordered <ol> of concrete steps\n"
            "   Those headings are the STRUCTURE, not the wording: translate "
            "them, and write the whole document, in the USER'S language. For a graph, drop a "
            "fenced `chart` block (format below) straight into the HTML — "
            "never <canvas>, Chart.js, <script> or `width:NN%` divs. The "
            "renderer draws it in the PDF AND in the chat from the same JSON, "
            "so the two can never disagree.\n"
            "\n"
            "Use `export_records_to_file` instead ONLY when the user explicitly "
            "wants a raw list with no analysis. \"report / analysis / review / "
            "summary / study / insights\" — and their equivalents in any "
            "language — mean THIS document.\n"
            "After `generate_file` returns, your chat reply is the download "
            "link + one sentence on the top finding, optionally ONE inline "
            "chart. Never paste the report body into chat.\n"
            "\n"
            "CHART BLOCK — a fenced block tagged `chart` containing only JSON:\n"
            "```chart\n"
            "{\"type\":\"bar\",\"title\":\"2025 sales by month\","
            "\"labels\":[\"Jan\",\"Feb\",\"Mar\"],"
            "\"series\":[{\"name\":\"Revenue\",\"data\":[14300,11100,15800]}],"
            "\"unit\":\"€\"}\n"
            "```\n"
            "`type` = bar | line | pie | doughnut (line for trends, bar for "
            "comparisons, pie/doughnut for shares). `labels` and every "
            "`series[].data` must be the same length; ≤ 12 labels; at most 1-2 "
            "charts per reply; several measures = several `series` entries.\n"
            "⚠️ Every value must come from a tool result THIS turn — never "
            "estimated, rounded or smoothed. Build `labels` and `data` ONLY "
            "from the periods `read_group` actually returned: never pad the "
            "axis to a full Jan→Dec year, never add a zero month to \"complete\" "
            "it. A missing chart is fine; an invented one is a critical failure."
        )

    # ── Block 5: dynamic RAGs.
    # Schema RAG whenever the model might touch Odoo data — lookups, actions,
    # and file work that exports DB content. Only greeting/identity skip it.
    # `_resolve_schema_block` handles the hybrid RAG → re-rank AND the
    # per-conversation cache. Fail-open throughout.
    schema = _resolve_schema_block(user, user_prompt, conv=conv)
    if schema:
        blocks.append(schema)
    # ORM-method RAG is heavier (signatures, docstrings). It pays off for
    # writes and for DB-export file work; pure reads skip it.
    if intent in ("action", "file", "report"):
        orm_methods = retrieve_relevant_orm(user.env, user_prompt)
        if orm_methods:
            blocks.append(orm_methods)

    if extra_context:
        blocks.append(extra_context)

    # Final mandate, last thing before the user's message: keep the model
    # from treating the schema block as input to comment on instead of
    # acting on the request.
    if intent in ("lookup", "action", "file", "report"):
        blocks.append(
            "=== ACTION MANDATE ===\n"
            "When the user asks for data, a list, a count, a record, a file or "
            "a change: your NEXT message is a TOOL CALL — not a question, not "
            "a recap of the reference block (that block is for you, not the "
            "user). Only ask when a real-world value is genuinely missing and "
            "cannot be inferred (e.g. two different clients share a name)."
        )

    return "\n\n".join(blocks)


def _truncate_json(value, limit=MAX_TOOL_RESULT_LEN):
    """Sérialise un dict en JSON et tronque si trop long pour ne pas exploser
    la fenêtre de contexte du modèle au tour suivant.

    ROW-BOUNDARY AWARE: when `value` is a search_read-style result
    ({"results": [...], "count": N, ...}), we drop WHOLE rows off the end
    until the payload fits — never slicing a row mid-object. A blind byte
    cut used to hand the model a half-written JSON row (`{"id": 42, "na`),
    which it then either ignored or hallucinated around. Dropping whole
    rows keeps every row the model DOES see fully valid, and we tell it
    exactly how many rows were withheld + how to page for the rest — so a
    "list everything" answer is honest instead of silently short."""
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    if len(s) <= limit:
        return s

    # Structured search_read result → trim whole rows off the tail.
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        rows = value["results"]
        total = value.get("count", len(rows))
        # Binary-ish shrink: keep dropping the last row until it fits.
        kept = list(rows)
        while kept:
            trial = dict(value)
            trial["results"] = kept
            omitted = total - len(kept)
            if omitted > 0:
                trial["truncation_note"] = (
                    f"{omitted} more record(s) are not shown here, to fit "
                    f"the context ({total} in total). To get them, call "
                    f"odoo_search_read again with offset={len(kept)}, or use "
                    f"odoo_search_count for a plain total, or "
                    f"export_records_to_file to export everything to a file."
                )
            try:
                cand = json.dumps(trial, default=str, ensure_ascii=False)
            except Exception:
                break
            if len(cand) <= limit:
                return cand
            # Drop ~10% of remaining rows per step so we converge fast on
            # very wide rows instead of shaving one at a time.
            drop = max(1, len(kept) // 10)
            kept = kept[:-drop]
        # Even zero rows didn't fit (huge single row) — fall through to the
        # blunt cut below as a last resort.

    return s[:limit] + f"\n[...truncated at {limit} characters...]"


def _call_local_chat(base_url, model, messages, tools=None, options=None,
                 timeout=500, keep_alive=None, cancel_check=None):
    """Generic /api/chat caller for a local server. Parameterised so the same code
    handles the global-settings backend (`_call_default_local`) AND per-record
    local provider records — each can target a different URL / model /
    set of options.

    `cancel_check`: optional zero-arg callable returning True when the user
    asked to stop this turn. On a streamed turn we poll it between chunks and,
    on True, close the HTTP response — closing the socket makes the server abort
    generation on the box. Returns {ok: False, cancelled: True} in that case.

    STREAMING POLICY — we stream ONLY when NO `tools` are sent (a final-answer
    / no-tool generation, the long write where the Stop button matters most).
    When `tools` ARE sent (tool-call turns) we use the NON-streaming path:
    Streaming tool-call reassembly is fragile, and a partially- or
    doubly-reassembled tool_call, sent back on the NEXT turn, makes the server
    reject the whole request ("invalid character 'T'…"). Non-streaming returns
    one clean `message.tool_calls`. Cancellation on tool turns is still honoured
    by the caller BETWEEN loop iterations, so Stop stays responsive.
    """
    use_stream = not tools
    sys_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "system")
    _cprint("blue", "LOCAL /chat →",
            f"{len(messages)} messages  tools={'yes' if tools else 'no'}  "
            f"stream={'yes' if use_stream else 'no'}  "
            f"sys={sys_chars} chars  model={model}  base={base_url}")
    payload = {
        "model":   model,
        "messages": messages,
        "stream":  use_stream,
        "options": options or {},
    }
    if tools:
        payload["tools"] = tools
    if keep_alive:
        payload["keep_alive"] = keep_alive
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            headers=_LOCAL_HEADERS,
            json=payload, timeout=timeout, stream=use_stream,
        )
        if not resp.ok:
            try:
                err_msg = resp.json().get('error', resp.reason)
            except Exception:
                err_msg = resp.reason
            # "does not support chat" → the user has wired an image-gen
            # (or any non-chat) model as a chat provider. Replace
            # the terse server error with an actionable hint pointing to
            # the picker / provider list, and remind them that image
            # generation does NOT need a provider — the `generate_image`
            # tool calls /api/generate on the image model directly,
            # whichever chat brain is selected here.
            low = str(err_msg).lower()
            if "does not support chat" in low or "not a chat model" in low:
                _cprint("yellow", "LOCAL /chat WRONG-MODEL",
                        f"selected={model} → {err_msg}")
                return {
                    "ok": False,
                    "error": _(
                        "The model selected in the picker (`%s`) is not a "
                        "chat model — the AI server refuses it. It is most "
                        "likely an image model (e.g. `x/z-image-turbo`) added "
                        "by mistake as a chat model. Go to DIGI-ERP AI → AI "
                        "Models, archive that entry, and pick a real chat "
                        "model instead (`llama3.2`, `qwen2.5`, …). Image "
                        "generation works through the built-in tool — it does "
                        "not need to be registered as a model.", model
                    ),
                }
            # A server-side JSON parse error ("invalid character …") means
            # The server rejected something in the payload — almost always
            # a malformed tool_call the model produced. Log a COMPACT shape of
            # the outgoing messages (roles + tool-call names, never the bulky
            # content) so a recurrence is diagnosable instead of a guess.
            if "invalid character" in low or "looking for beginning" in low:
                shape = []
                for m in messages:
                    r = m.get("role")
                    if r == "assistant" and m.get("tool_calls"):
                        names = [((tc.get("function") or {}).get("name") or "?")
                                 for tc in m["tool_calls"]]
                        shape.append(f"assistant→tools{names}")
                    else:
                        shape.append(r or "?")
                _cprint("red", "LOCAL /chat BAD-PAYLOAD",
                        f"{err_msg} | msg shape: {shape}")
                _logger.error("Server rejected payload (%s). Last assistant "
                              "tool_calls: %s", err_msg,
                              next((m.get("tool_calls") for m in reversed(messages)
                                    if m.get("role") == "assistant"
                                    and m.get("tool_calls")), None))
            return {"ok": False, "error": f"AI service error: {err_msg}"}

        # ── Non-streaming path (tool turns): one clean parse ────────────────
        # The server assembles the tool_calls and returns them whole,
        # exactly as callers re-send them next turn — no fragile client-side
        # reassembly, so no malformed tool_call round-trip.
        if not use_stream:
            data = resp.json()
            msg = data.get("message") or {}
            tc = msg.get("tool_calls") or []
            content_preview = (msg.get("content") or "")[:100]
            _cprint("green", "LOCAL /chat OK",
                    f"tool_calls={len(tc)}  content={content_preview!r}")
            return {"ok": True, "message": msg, "raw": data}

        # ── Streaming reassembly ────────────────────────────────────────────
        # The server streams newline-delimited JSON objects, each with a partial
        # `message` (content delta and/or tool_calls) and a `done` flag. We
        # concatenate content, keep any tool_calls (they arrive whole, in one
        # chunk), and poll `cancel_check` between chunks so Stop is honoured
        # mid-generation.
        acc_content = []
        acc_thinking = []
        tool_calls = []
        final_raw = None
        cancelled = False
        for line in resp.iter_lines(decode_unicode=True):
            if cancel_check and cancel_check():
                cancelled = True
                _cprint("yellow", "LOCAL /chat CANCELLED",
                        "user pressed Stop — closing stream, server aborts")
                break
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except (ValueError, TypeError):
                continue
            cmsg = chunk.get("message") or {}
            if cmsg.get("content"):
                acc_content.append(cmsg["content"])
            # Some builds stream the reasoning trace separately as `thinking`.
            if cmsg.get("thinking"):
                acc_thinking.append(cmsg["thinking"])
            if cmsg.get("tool_calls"):
                tool_calls.extend(cmsg["tool_calls"])
            if chunk.get("done"):
                final_raw = chunk
                break

        # Closing the response returns the socket to the pool / tears it down;
        # after a cancel this is what actually makes the server stop generating.
        try:
            resp.close()
        except Exception:
            pass

        if cancelled:
            return {"ok": False, "cancelled": True,
                    "error": _("Generation interrupted by the user.")}

        msg = {"role": "assistant", "content": "".join(acc_content)}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if acc_thinking:
            msg["thinking"] = "".join(acc_thinking)
        content_preview = (msg.get("content") or "")[:100]
        _cprint("green", "LOCAL /chat OK",
                f"tool_calls={len(tool_calls)}  content={content_preview!r}")
        return {"ok": True, "message": msg, "raw": final_raw or {}}
    except requests.exceptions.Timeout:
        _logger.warning("Local /api/chat timeout (>%ss) @ %s", timeout, base_url)
        _cprint("red", "LOCAL /chat TIMEOUT", f">{timeout}s @ {base_url}")
        return {"ok": False,
                "error": _("Timeout: no answer from %(url)s after %(secs)ss.",
                           url=base_url, secs=timeout)}
    except requests.exceptions.ConnectionError as e:
        _logger.error("Local /api/chat connection refused: %s", e)
        _cprint("red", "LOCAL /chat CONN ERR", str(e)[:200])
        return {"ok": False,
                "error": _("Cannot reach %s. Check the URL.", base_url)}
    except Exception as e:
        _logger.exception("Local /api/chat erreur inattendue")
        _cprint("red", "LOCAL /chat ERR", f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


def _call_default_local(messages, tools=None, cancel_check=None):
    """Default local backend using the global DIGI-ERP AI settings
    (Settings → DIGI-ERP AI → local_base_url / chat_model)."""
    cfg = _cfg()
    return _call_local_chat(
        base_url=cfg["local_base_url"],
        model=cfg["chat_model"],
        messages=messages,
        tools=tools,
        options=_local_options(cfg),
        timeout=cfg["request_timeout"],
        cancel_check=cancel_check,
    )


def _call_local_provider(provider, messages, tools=None, cancel_check=None):
    """Call a local server using a specific record's URL / model / tuning.
    Every per-record knob (base_url, num_ctx, temperature, max_tokens,
    timeout, keep_alive) falls back to the global Settings → DIGI-ERP AI value
    when left blank, so 'local model X on the default host' takes one line
    of config.

    Self-healing: if the server responds 'model does not support tools', we
    clear `supports_tools` on the provider record and retry without tools.
    Subsequent calls then skip the tools payload entirely.
    """
    cfg = _cfg()
    base_url   = (provider.base_url or "").rstrip("/") or cfg["local_base_url"]
    model      = provider.model_name or cfg["chat_model"]
    timeout    = provider.timeout_seconds or cfg["request_timeout"]
    options = {
        "num_ctx":     provider.num_ctx or cfg["num_ctx"],
        "temperature": provider.temperature,
    }
    if provider.max_tokens:
        options["num_predict"] = provider.max_tokens
    use_tools = tools if (tools and provider.supports_tools) else None

    result = _call_local_chat(
        base_url=base_url,
        model=model,
        messages=messages,
        tools=use_tools,
        options=options,
        timeout=timeout,
        keep_alive=provider.keep_alive or None,
        cancel_check=cancel_check,
    )

    # Self-heal: the server rejected tools. Mark the provider as tools-less,
    # retry without tools so the user still gets an answer.
    if (
        use_tools
        and not result.get("ok")
        and "does not support tools" in (result.get("error") or "").lower()
    ):
        _cprint(
            "yellow", "PROVIDER SELF-FIX",
            f"{provider.name} ({model}) doesn't support tools — "
            f"clearing supports_tools on the provider record and retrying."
        )
        try:
            provider.sudo().supports_tools = False
        except Exception:
            pass  # not critical — next call would just retry the same way
        result = _call_local_chat(
            base_url=base_url,
            model=model,
            messages=messages,
            tools=None,
            options=options,
            timeout=timeout,
            keep_alive=provider.keep_alive or None,
            cancel_check=cancel_check,
        )
    return result


# ── Provider resolution & unified chat call ──────────────────────────────────

_AUTO_PROVIDER_SENTINELS = ("auto", "Auto", "AUTO")

# ── Workload-component classifier ──────────────────────────────────────
# Instead of a single "intent" label, we decompose the prompt into the
# WORKLOAD COMPONENTS it requires (data fetch, file output, write
# action, modify-attached-file, image, reasoning). Tier follows from
# how many components are present + which heavyweight ones are.
#
# Why this is better than the old intent+complexity logic: a prompt like
#   "give me all the projects with their details in a txt file"
# fires TWO components (data_fetch + file_output). Old logic only saw
# "file" intent → balanced. New logic sees two components → heavy.
# That's exactly the kind of multi-step query that needs a strong model.

_COMPONENT_PATTERNS = {
    "greeting": re.compile(
        r"^\s*("
        # ─── English ────────────────────────────────────────────
        r"hi+|hello+|hey+|yo+|sup|howdy|hiya|hi\s*there|hi\s*all|"
        # Common misspellings — covers typos like "ehllo", "hellow",
        # "helo", "halo", "hii", "hai" (sms-speak) — small but real
        # share of chat inputs, especially on mobile.
        r"ehllo|hellow|helo|halo|hii+|hai|heya|"
        r"good\s+(?:morning|afternoon|evening|night)|"
        r"g'?day|gm|gn|"
        r"morning|afternoon|evening|"
        r"what'?s\s+up|whatsup|wassup|wazzup|"
        # English closes
        r"bye+|goodbye|good\s+bye|see\s+(?:you|ya)(?:\s+later)?|cya|"
        r"later|ttyl|take\s+care|peace(?:\s+out)?|cheers|farewell|"
        # English thanks
        r"thanks?|thx|tx|ty|tysm|much\s+(?:obliged|appreciated)|"
        r"appreciate(?:\s+it|d)?|many\s+thanks|thank\s+you(?:\s+so\s+much)?|"
        # English acknowledgements / yes-no
        r"ok+|okay+|kk|k|alright|all\s+right|got\s+it|understood|noted|"
        r"fine|gotcha|copy(?:\s+that)?|roger(?:\s+that)?|"
        r"yes+|yeah|yep|yup|yass|aye|"
        r"no+|nope|nah|"
        r"sure|absolutely|exactly|right|correct|indeed|of\s+course|"
        # English reactions
        r"cool+|nice+|good+|great+|perfect+|excellent+|awesome+|amazing+|"
        r"wonderful+|lovely+|brilliant+|fantastic+|"
        r"wow+|haha+|hehe+|lol+|lmao+|rofl+|"
        r"nice\s+(?:one|work|job)|well\s+done|"
        # ─── French ─────────────────────────────────────────────
        r"salut+|bonjour+|bonsoir+|coucou+|allô+|hé+|hey+|holà+|"
        r"bjr|bsr|cc|"
        r"bonne\s+(?:journée|soirée|nuit|matinée|après-midi)|"
        r"merci(?:\s+(?:beaucoup|bien|infiniment|mille\s+fois))?|mci|"
        r"je\s+vous\s+remercie|"
        r"au\s+revoir|à\s+(?:plus|bientôt|tout\s+à\s+l'heure|demain|"
        r"la\s+prochaine|toute)|adieu|salutations|"
        r"oui+|ouais|ouaip|d'accord|d'acc|dac|"
        r"non+|nan|"
        r"super+|génial+|excellent+|parfait+|nickel+|top+|"
        r"bien\s+(?:vu|sûr|joué)|"
        # ─── Spanish ────────────────────────────────────────────
        r"hola+|buenos\s+(?:días|dias|tardes|noches)|buen\s+día|"
        r"qué\s+tal|que\s+tal|"
        r"gracias|muchas\s+gracias|mil\s+gracias|"
        r"adios|adiós|hasta\s+(?:luego|pronto|mañana|la\s+vista)|chao|"
        r"sí|si|no|claro|vale|de\s+acuerdo|"
        # ─── German ─────────────────────────────────────────────
        r"hallo+|hi+|servus|moin|grüß\s+(?:gott|dich|sie)|"
        r"guten\s+(?:tag|morgen|abend)|"
        r"danke(?:\s+(?:schön|sehr|vielmals))?|vielen\s+dank|"
        r"tschüss|tschüß|auf\s+wiedersehen|bis\s+(?:bald|später|morgen)|"
        r"ja+|nein+|jawohl|"
        # ─── Italian ────────────────────────────────────────────
        r"ciao+|buongiorno|buonasera|buon\s+giorno|salve|"
        r"grazie(?:\s+mille|\s+tante)?|"
        r"arrivederci|a\s+(?:presto|dopo|domani)|"
        r"sì|si|no|certo|va\s+bene|"
        # ─── Portuguese ─────────────────────────────────────────
        r"olá|ola|oi+|"
        r"bom\s+(?:dia|tarde)|boa\s+(?:noite|tarde)|"
        r"obrigad[oa](?:\s+muito)?|valeu|"
        r"tchau|adeus|até\s+(?:logo|breve|amanhã)|"
        r"sim|não|claro|"
        # ─── Arabic (defense if translation didn't fire) ───────
        r"مرحبا|مرحباً|"
        r"السلام\s*عليكم|"
        r"أهلا|أهلاً|اهلا|اهلاً|"
        r"صباح\s+الخير|مساء\s+الخير|تصبح\s+على\s+خير|"
        r"شكرا|شكراً|شكرا\s+جزيلا|"
        r"نعم|لا|طبعا|أكيد|"
        r"وداعا|وداعاً|مع\s+السلامة|إلى\s+اللقاء|"
        # ─── Hebrew ─────────────────────────────────────────────
        r"שלום|תודה|להתראות|כן|לא|"
        # ─── Hindi / Sanskrit ──────────────────────────────────
        r"namaste|namaskar|dhanyavad|shukriya|alvida|"
        # ─── Japanese romaji + hiragana ────────────────────────
        r"konnichiwa|ohayou?|konbanwa|arigatou?|sayonara|"
        r"はい|いいえ|どうも|"
        # ─── Chinese pinyin + hanzi ────────────────────────────
        r"ni\s*hao|xie\s*xie|zai\s*jian|"
        r"你好|谢谢|再见|是|不|"
        # ─── Korean ─────────────────────────────────────────────
        r"annyeong(?:haseyo)?|gamsahamnida|"
        r"안녕|안녕하세요|감사합니다|"
        # ─── Russian ────────────────────────────────────────────
        r"privet|zdravstvuyte|spasibo|poka|da|nyet|"
        r"привет|здравствуйте|спасибо|пока|"
        # ─── Generic interjections / emoji / punctuation ───────
        r"\?+|\.+|!+|…+|"
        r"👍|👋|🙂|🙏|😊|❤️|❤|💚|💙|🤝|👌|✌|"
        r")\s*[!\.\?…؟。]*\s*$",
        re.IGNORECASE,
    ),
    # ── Identity / capability questions about the BOT itself ────────
    # ("who are you", "what can you do", "comment tu fonctionnes",
    #  "are you a bot", "tell me about yourself", …)
    # These need NO tool call and NO DB access — the model just
    # self-describes from the system prompt. Always routed to
    # lightweight. Separated from data_fetch on purpose so
    # questions like "who are you" don't get mis-classified as
    # "who is <person in the DB>".
    "identity": re.compile(
        r"\b("
        # ─── English ────────────────────────────────────────────
        # Polite check-in — "how are you", "how's it going"
        r"how\s+(?:are|r)\s+(?:you|u|ya|things)|"
        r"how\s+have\s+(?:you|u)\s+been|"
        r"how(?:'s|\s+is)\s+(?:it\s+going|life|everything)|"
        r"hru|wyd|wassup|"
        # Who / what are you
        r"who\s+(?:are|r|is)\s+(?:you|u|this|that)|"
        r"what\s+(?:are|r|is)\s+(?:you|u|this(?:\s+chat)?|that(?:\s+chat)?)|"
        r"what'?s?\s+(?:your|ur)\s+name|"
        r"what\s+(?:should|do)\s+(?:i|we)\s+call\s+(?:you|u)|"
        r"who\s+(?:made|created|built|trained|developed)\s+(?:you|u)|"
        r"what\s+(?:model|version|engine|llm|ai)\s+(?:are|r)\s+(?:you|u)|"
        r"are\s+(?:you|u)\s+(?:a|an)\s+"
        r"(?:bot|ai|chatbot|assistant|human|person|robot|machine|model)|"
        r"are\s+(?:you|u)\s+"
        r"(?:chatgpt|gpt|gemini|claude|llama|gemma|qwen|copilot|alexa|siri)|"
        # Capability
        r"what\s+can\s+(?:you|u)\s+(?:do|help\s+(?:with|me\s+with))|"
        r"what\s+do\s+(?:you|u)\s+do|"
        r"what\s+are\s+(?:your|ur)\s+(?:capabilities|features|skills|powers|tools|"
        r"functions|abilities)|"
        r"list\s+(?:your|ur)\s+(?:features|capabilities|skills|tools)|"
        r"how\s+can\s+(?:you|u)\s+help(?:\s+me)?|"
        r"how\s+do\s+(?:you|u)\s+(?:work|help)|"
        r"how\s+does\s+(?:this|the\s+chat)\s+(?:work|chat|app)|"
        r"what(?:'s|\s+is)\s+(?:your|ur)\s+"
        r"(?:purpose|role|job|goal|function|mission)|"
        r"what(?:'s|\s+is)\s+(?:this|that)(?:\s+chat|\s+app|\s+thing)?|"
        # Introduce
        r"introduce\s+(?:yourself|urself)|"
        r"tell\s+me\s+(?:about|something\s+about)\s+(?:yourself|urself|u)|"
        r"describe\s+(?:yourself|urself|u)|"
        # Generic help
        r"^\s*help\s*[!\.\?]*\s*$|"
        r"can\s+you\s+help(?:\s+me)?|i\s+need\s+(?:your\s+)?help|"
        # ─── French ────────────────────────────────────────────
        r"qui\s+(?:es[\s-]+tu|êtes[\s-]+vous|t['e]s?-?tu|"
        r"est-ce\s+que\s+tu\s+es)|"
        r"qu(?:'?est-ce\s+que\s+tu\s+es|i\s+es-tu|i\s+êtes-vous)|"
        r"qu(?:'?est-ce\s+que\s+vous\s+êtes)|"
        r"comment\s+(?:tu\s+t'appelles|vous\s+vous\s+appelez|t'appelles-tu)|"
        r"quel\s+(?:est\s+ton\s+nom|est\s+votre\s+nom|nom\s+as[\s-]+tu)|"
        r"qui\s+t'a\s+(?:fait|créé|conçu|construit)|"
        r"qui\s+vous\s+a\s+(?:fait|créé|conçu|construit)|"
        r"quel\s+(?:modèle|moteur|llm|ia|version)\s+es[\s-]+tu|"
        r"es[\s-]+tu\s+(?:un|une)\s+"
        r"(?:bot|ai|ia|robot|humain|personne|assistante?|machine)|"
        r"êtes[\s-]+vous\s+(?:un|une)\s+"
        r"(?:bot|ai|ia|robot|humain|personne|assistante?|machine)|"
        r"que\s+(?:peux|sais|fais)[\s-]?tu(?:\s+faire)?|"
        r"que\s+pouvez[\s-]+vous\s+faire|"
        r"quelles?\s+(?:sont\s+)?tes\s+"
        r"(?:capacités|compétences|fonctionnalités|outils)|"
        r"liste(?:[\s-]+moi)?\s+(?:tes|vos)\s+"
        r"(?:capacités|fonctionnalités|outils)|"
        r"comment\s+(?:tu\s+fonctionnes|fonctionnes[\s-]+tu)|"
        r"comment\s+(?:vous\s+fonctionnez|fonctionnez[\s-]+vous)|"
        r"comment\s+(?:tu\s+peux|peux[\s-]+tu|pouvez[\s-]+vous)\s+"
        r"(?:m'aider|aider)|"
        r"comment\s+(?:ça|cela)\s+(?:marche|fonctionne)|"
        r"quel\s+est\s+(?:ton|votre)\s+(?:rôle|but|objectif|nom)|"
        r"présente[\s-]+toi|présentez[\s-]+vous|"
        r"parle[\s-]+moi\s+de\s+toi|parlez[\s-]+moi\s+de\s+vous|"
        r"décris[\s-]+toi|décrivez[\s-]+vous|"
        r"^\s*aide(?:[\s-]+moi)?\s*[!\.\?]*\s*$|"
        r"j'ai\s+besoin\s+d'aide|"
        # ─── Spanish ────────────────────────────────────────────
        r"quién\s+(?:eres|sois)|"
        r"qué\s+(?:eres|sois)|"
        r"cómo\s+(?:te\s+llamas|se\s+llama)|"
        r"qué\s+(?:puedes|podéis)\s+hacer|"
        r"cuáles\s+son\s+tus\s+(?:capacidades|funciones)|"
        r"cómo\s+(?:funcionas|me\s+puedes\s+ayudar|me\s+ayudas)|"
        r"preséntate|preséntese|"
        r"háblame\s+de\s+ti|"
        # ─── German ────────────────────────────────────────────
        r"wer\s+(?:bist\s+du|sind\s+sie)|"
        r"was\s+(?:bist\s+du|sind\s+sie)|"
        r"wie\s+(?:heißt\s+du|heißen\s+sie)|"
        r"wer\s+hat\s+(?:dich|sie)\s+(?:gemacht|erschaffen)|"
        r"was\s+kannst\s+du(?:\s+tun)?|"
        r"was\s+sind\s+deine\s+(?:fähigkeiten|funktionen)|"
        r"wie\s+(?:funktionierst\s+du|kannst\s+du\s+helfen)|"
        # ─── Italian ───────────────────────────────────────────
        r"chi\s+(?:sei|siete)|"
        r"come\s+ti\s+chiami|"
        r"cosa\s+(?:puoi|sai)\s+fare|"
        r"come\s+(?:funzioni|puoi\s+aiutarmi)|"
        r"presentati|"
        # ─── Portuguese ────────────────────────────────────────
        r"quem\s+(?:é\s+você|és\s+tu|sois)|"
        r"qual\s+(?:é\s+o\s+seu|é\s+teu)\s+nome|"
        r"o\s+que\s+(?:você\s+pode|podes)\s+fazer|"
        r"como\s+(?:você\s+funciona|funcionas)|"
        # ─── Arabic (in case translation didn't fire) ──────────
        r"من\s+أنت|"
        r"ما\s+(?:هو|اسمك)|ما\s+اسمك|"
        r"ماذا\s+تفعل|ماذا\s+تستطيع|"
        r"كيف\s+(?:تساعد|تعمل|أساعدك)|"
        r"عرف\s+بنفسك"
        r")\b",
        re.IGNORECASE,
    ),
    # Asking to retrieve Odoo data — list me / give me / show / find /
    # how many / what is / who / etc. Triggers a tool call.
    "data_fetch": re.compile(
        r"\b("
        r"give\s*me|donne(?:[zs]?(?:\s*moi)?)?|"
        r"show|montre|affiche|display|"
        r"list|liste|"
        r"all|tous|toutes|every|chaque|"
        r"how\s*many|combien|count|nombre\s*de|"
        r"find|trouve|cherche|search|recherche|search\s*for|"
        r"what\s*are|what\s*is|qu(?:'?est-ce|el)|quels?|quelles?|"
        r"who(?:'s|\s)|qui(?:\s+est)?|"
        r"fetch|récupère|récupérer"
        r")\b",
        re.IGNORECASE,
    ),
    # Producing a NEW file from scratch — output format mentioned.
    "file_output": re.compile(
        r"\b("
        r"docx|xlsx|pptx|csv|pdf|txt|json|xml|html|"
        r"file|fichier|document|spreadsheet|tableur|"
        r"export(?:e|er|s|ed)?|"
        r"download|télécharge|téléchargement|"
        r"generate\s+(?:a\s+)?(?:doc|file|pdf|excel|sheet)|"
        r"génère\s+(?:un\s+)?(?:doc|fichier|pdf|excel|tableur)"
        r")\b",
        re.IGNORECASE,
    ),
    # Transforming a file the user attached (translation, rewrite…).
    # Detected from the prompt text — attachment presence is a strong
    # extra signal added below in _detect_components.
    "modify_file": re.compile(
        r"\b("
        r"translate|traduit?|traduis|traduction|translation|"
        r"rewrite|réécris|réécrire|"
        r"redact|"
        r"anonymize|anonymise|anonymiser|"
        r"summari[sz]e|résume|résumé|resume|"
        r"convert|convertir|"
        r"modify\s+(?:the|this)\s+(?:file|document)|"
        r"modifie(?:r)?\s+(?:le|ce)\s+(?:fichier|document)"
        r")\b",
        re.IGNORECASE,
    ),
    "image": re.compile(
        r"\b("
        r"image|picture|photo|illustration|logo|poster|avatar|"
        r"draw|dessine|"
        r"generate\s+(?:an?\s+)?image|génère\s+(?:une\s+)?image"
        r")\b",
        re.IGNORECASE,
    ),
    # Writing to Odoo data — CRUD verbs. Always needs the strong model.
    "write_action": re.compile(
        r"\b("
        r"create|cree|crée|créer|créé|"
        r"add|ajoute|ajouter|"
        r"update|modifie|modifier|change(?:r)?|"
        r"delete|supprime|supprimer|"
        r"send|envoie|envoyer|"
        r"post|valide|valider|confirm|confirme|"
        r"assign|assigne|"
        r"cancel|annule|annuler|"
        r"link|relie|relier|"
        r"set|fix|réinitialise"
        r")\b",
        re.IGNORECASE,
    ),
    # Reasoning markers — the model has to think, not just retrieve.
    "reasoning": re.compile(
        r"\b("
        r"analyz|analy[sz]e|"
        r"compare|compar[aé]|"
        r"explain|expliqu|"
        r"why|pourquoi|porquoi|"
        r"how\s+does|comment(?:\s+ça)?|"
        r"step\s*by\s*step|étape\s*par\s*étape|"
        r"plan|propose|recommend|recommand|"
        r"workflow|process|processus|orchestr|"
        r"complex|complexe|complicated|compliqué|"
        r"multiple|plusieurs|several"
        r")\b",
        re.IGNORECASE,
    ),
}


def _detect_components(prompt):
    """Return the set of workload-components found in the prompt.

    A component represents one kind of work the model has to do
    (fetch, write, output a file, modify an attached file, generate
    an image, reason). The same prompt can carry several components
    — that's exactly what the tier picker uses to decide.

    Special cases:
      • Empty prompt / pure greeting → {'greeting'} (exclusive).
      • ATTACHMENT_ID present in the prompt → always adds
        'modify_file' (we know there's a file to act on).
    """
    if not prompt or not prompt.strip():
        return {"greeting"}
    s = prompt.strip()
    # A short message that's nothing but a greeting word — return
    # exclusively. Other components are spurious here.
    if len(s) < 200 and _COMPONENT_PATTERNS["greeting"].match(s):
        return {"greeting"}
    components = set()
    has_attachment = ("ATTACHMENT_ID" in s
                      or "===BEGIN MARKED===" in s)
    if has_attachment:
        components.add("modify_file")
    for name in (
        "data_fetch", "file_output", "modify_file",
        "image", "write_action", "reasoning", "identity",
    ):
        if _COMPONENT_PATTERNS[name].search(s):
            components.add(name)
    # ── Dedup pass: kill false positives without losing real signals ─
    # When the user is clearly transforming an attached file
    # ("translate this document"), the `file_output` regex tends to
    # fire on the same words ("document", "file") that modify_file
    # matches. Suppress the duplicate so the workload count is honest.
    if has_attachment and "modify_file" in components:
        components.discard("file_output")
    # Identity questions ("who are you", "what can you do") superficially
    # match `data_fetch` ("who/what") and `reasoning` ("how does this
    # work"). BUT a prompt like "how are you, give me all the projects
    # in a file" has BOTH a polite identity check AND a real data fetch
    # — we must only suppress the false positive, not the real signal.
    # The trick: check for STRONG fetch/reasoning verbs that don't
    # overlap with identity. If any are present, the data_fetch /
    # reasoning component stays.
    if "identity" in components:
        if ("data_fetch" in components
                and not _DATA_FETCH_STRONG_RE.search(s)):
            components.discard("data_fetch")
        if ("reasoning" in components
                and not _REASONING_STRONG_RE.search(s)):
            components.discard("reasoning")
    return components


# ── Strong signals used by the dedup rule ─────────────────────────────
# These are the unambiguous fetch / reasoning verbs that DON'T overlap
# with identity question patterns. If any of them is present alongside
# identity, the corresponding component stays alive — because the user
# is genuinely asking for data / analysis on top of the polite preamble.
_DATA_FETCH_STRONG_RE = re.compile(
    r"\b("
    r"give\s*me|donne(?:[zs]?(?:\s*moi)?)?|"
    r"show|montre|affiche|display|"
    r"list|liste|"
    r"all|tous|toutes|every|chaque|"
    r"how\s*many|combien|count|nombre\s*de|"
    r"find|trouve|cherche|search(?:\s+for)?|recherche|"
    r"fetch|récupère|récupérer"
    r")\b",
    re.IGNORECASE,
)
_REASONING_STRONG_RE = re.compile(
    r"\b("
    r"analyz|analy[sz]e|"
    r"compare|compar[aé]|"
    r"why|pourquoi|porquoi|"
    r"step\s*by\s*step|étape\s*par\s*étape|"
    r"plan|propose|recommend|recommand|"
    r"workflow|process|processus|orchestr|"
    r"complex|complexe|complicated|compliqué"
    r")\b",
    re.IGNORECASE,
)


# Each component carries an INTENSITY — how strong a model is needed
# to handle that piece of work. The picker takes the MAX intensity
# across all components (as the user explicitly asked: "use the heaviest
# model between them to cover everything"), then bumps to heavy when
# multiple distinct work components co-occur (multi-step orchestration
# is hard for smaller models even if each step is balanced-tier work).
#
#   0 = no model work       (greeting, identity)
#   2 = balanced            (one tool call: fetch / file / image / modify)
#   3 = heavy               (write_action, reasoning, multi-step)
_COMPONENT_INTENSITY = {
    "greeting":     0,
    "identity":     0,
    "image":        2,
    "data_fetch":   2,
    "modify_file":  2,
    "file_output":  2,
    "write_action": 3,
    "reasoning":    3,
}

_INTENSITY_TIER = {0: "lightweight", 2: "balanced", 3: "heavy"}


def _pick_tier_for_components(components):
    """Pick the tier that handles every component in the set.

    The rule is "max intensity wins" — if any single component needs a
    heavy model, that's what the whole prompt gets. Mixed-intensity
    prompts ("hi, give me all projects in a file") therefore always
    escalate to the strongest required tier instead of being demoted
    by the lightweight pieces.

    Two refinements on top of pure max:
      • 2+ distinct work components → heavy (multi-step orchestration).
        Two balanced-tier things together (data_fetch + file_output)
        require the model to plan, not just answer.
      • Empty / unknown input → balanced (safe default)."""
    if not components:
        return "balanced"
    # Max intensity across all present components.
    max_intensity = max(
        _COMPONENT_INTENSITY.get(c, 2) for c in components
    )
    # Count distinct WORK components (intensity ≥ 2). Two or more
    # different work types in the same prompt = orchestration → heavy.
    work_count = sum(
        1 for c in components
        if _COMPONENT_INTENSITY.get(c, 0) >= 2
    )
    if work_count >= 2:
        return "heavy"
    return _INTENSITY_TIER.get(max_intensity, "balanced")


# Tier ranking — bigger = stronger model. Used by the search chain
# to enforce "never demote": a provider you tagged lightweight is
# never picked for a balanced or heavy target, even if no other
# provider matches. Falling through to the global default is the
# correct degradation; putting greeting-grade models on heavy work
# produces worse answers than just failing over.
_TIER_RANK = {"lightweight": 0, "balanced": 1, "heavy": 2}


def _tier_search_chain(target_tier):
    """Tiers we try, in order, for a given target.

    Exact match preferred, then upgrades only — never demotions.
      heavy       → ['heavy']
      balanced    → ['balanced', 'heavy']
      lightweight → ['lightweight', 'balanced', 'heavy']
      image       → ['image']  (used only by generate_image tool)
    """
    if target_tier == "image":
        return ["image"]
    rank = _TIER_RANK.get(target_tier)
    if rank is None:
        return [target_tier]
    return [t for t, r in sorted(_TIER_RANK.items(), key=lambda x: x[1])
            if r >= rank]


def _auto_pick_provider(env, prompt, conv=None):
    """Decompose the prompt into workload components, pick a tier,
    then find the first ACTIVE provider for that tier (or an upgrade
    of it). Returns the provider record, or None when no eligible
    provider is configured — caller then falls back to the global
    default model.

    `prompt` is the original (untranslated) user text. We translate
    it to English HERE — only for classification — because the
    `_detect_components` regex is English-only. The LLM itself still
    gets the original prompt downstream (see `_run_with_tools`).

    `conv` (optional) is the conversation record. When provided, the
    picked tier is clamped UP to the conversation's `auto_tier_ceiling`
    — once a thread has used a stronger model, follow-up turns never
    fall back to a weaker one, even if the classifier happens to
    misjudge a short prompt like "name and email"."""
    english_shadow = _english_for_classifier(prompt or "")
    components = _detect_components(english_shadow)
    candidate_tier = _pick_tier_for_components(components)

    # Apply conversation-level "never demote" rule.
    ceiling_tier = getattr(conv, "auto_tier_ceiling", None) if conv else None
    if (ceiling_tier
            and _TIER_RANK.get(ceiling_tier, 0)
            > _TIER_RANK.get(candidate_tier, 0)):
        _cprint("magenta", "AUTO TIER CLAMP",
                f"candidate={candidate_tier} < conv ceiling={ceiling_tier} "
                f"→ using ceiling (never demote in a thread)")
        target_tier = ceiling_tier
    else:
        target_tier = candidate_tier

    chain = _tier_search_chain(target_tier)
    for tier in chain:
        rec = env["digi_erp.model.llm.provider"].sudo().search([
            ("active", "=", True),
            ("auto_route_tier", "=", tier),
        ], limit=1)
        if rec:
            _cprint("magenta", "AUTO PICK",
                    f"components={sorted(components)} → "
                    f"tier={target_tier} (matched {tier}) → "
                    f"{rec.name} (id={rec.id}, model={rec.model_name})")
            # Lock the conversation to AT LEAST this tier from now on.
            if conv is not None:
                try:
                    conv._promote_auto_tier(tier)
                except Exception:
                    # Never let a stickiness write break the chat call.
                    _logger.exception(
                        "DIGI-ERP AI: failed to promote auto tier on conv #%s",
                        getattr(conv, "id", "?"),
                    )
            return rec
    _cprint("yellow", "AUTO PICK MISS",
            f"components={sorted(components)} → target="
            f"{target_tier}, tried {chain} — no provider at or "
            f"above that tier; falling back to global default")
    return None


def _pick_lightweight_provider(env):
    """Return the provider that runs the re-rank pre-pass, or None.

    Precedence:
      1. The model explicitly chosen in Settings
         (`digi_erp_ai.rerank_provider_id`) — an admin who names a model
         means it, so we honour it even if it isn't tagged 'Quick questions'.
         A stale id (record deleted or archived) falls through rather than
         disabling the feature.
      2. Otherwise the first ACTIVE model tagged as the lightweight tier
         ('Quick questions'). `_order` is name-ascending, so with several
         tagged the alphabetically first one wins.

    Unlike `_auto_pick_provider` this never upgrades — a re-rank on a heavy
    model would defeat the point (we want a fast, cheap filter, not a second
    expensive reasoning call). With neither configured the caller skips the
    re-rank and uses the raw RAG shortlist as-is.
    """
    Provider = env["digi_erp.model.llm.provider"].sudo()

    raw = (env["ir.config_parameter"].sudo()
           .get_param("digi_erp_ai.rerank_provider_id") or "").strip()
    if raw:
        try:
            chosen = Provider.browse(int(raw))
            if chosen.exists() and chosen.active:
                return chosen
            _cprint("yellow", "RERANK CONFIG",
                    f"configured model #{raw} is missing or archived — "
                    f"falling back to the 'Quick questions' tier")
        except (TypeError, ValueError):
            _cprint("yellow", "RERANK CONFIG",
                    f"bad rerank_provider_id {raw!r} — ignoring")

    return Provider.search([
        ("active", "=", True),
        ("auto_route_tier", "=", "lightweight"),
    ], limit=1) or None


# Regex to salvage model technical names from a re-rank reply that wasn't
# clean JSON (small models sometimes wrap the list in prose or markdown).
_MODEL_NAME_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")


def _parse_rerank_reply(text, allowed):
    """Extract the chosen model names from a re-rank reply. Accepts a bare
    JSON array, a JSON object with a "models" key, or loose prose — we always
    intersect against `allowed` (the shortlist we sent) so the model can never
    invent a name or smuggle one in. Order of the reply is preserved; if
    nothing valid is found we return [] and the caller falls back to the full
    shortlist (fail-open — never worse than no re-rank)."""
    allowed_set = set(allowed)
    picked = []

    def _add(name):
        if name in allowed_set and name not in picked:
            picked.append(name)

    raw = (text or "").strip()
    # Try strict JSON first (array or {"models": [...]}).
    parsed = None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        # Pull the first [...] or {...} block out of prose and retry.
        m = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except (ValueError, TypeError):
                parsed = None
    if isinstance(parsed, dict):
        parsed = parsed.get("models") or parsed.get("relevant") or []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str):
                _add(item.strip())

    # Last resort: scan the raw text for anything model.name-shaped.
    if not picked:
        for name in _MODEL_NAME_RE.findall(raw):
            _add(name)
    return picked


def _rerank_candidate_models(env, prompt, candidates, conv=None):
    """Hybrid RAG → 7B re-rank. Given the RAG shortlist (`candidates`, a list
    of {"model","label","sim"} dicts), ask a cheap lightweight model which of
    them are actually relevant to `prompt`, and return the pruned+reordered
    list. Fail-open: on any problem (no lightweight provider, LLM error,
    empty/garbage reply) we return the original `candidates` untouched, so the
    chat is never worse off than plain RAG.

    Why bother: RAG shortlists ~28 models by embedding similarity, which is
    recall-oriented and noisy (near-duplicate PO/SO/invoice layouts all score
    alike). A 7B reading the actual prompt + the candidate LABELS can throw
    out the ~20 that don't matter, so the expensive 32B main call prefills a
    handful of precise schemas instead of a wall of look-alikes."""
    if not candidates or len(candidates) <= 5:
        # Nothing to prune — shortlist is already tight.
        return candidates

    provider = _pick_lightweight_provider(env)
    if provider is None:
        _cprint("yellow", "RERANK SKIP",
                "no lightweight-tier provider configured — using raw RAG shortlist")
        return candidates

    names = [c["model"] for c in candidates]
    listing = "\n".join(f"- {c['model']} ({c['label']})" for c in candidates)
    sys_msg = (
        "You are a model-routing classifier for an Odoo assistant. You are "
        "given a user request and a numbered list of Odoo models (technical "
        "name + human label). Return ONLY the models that are genuinely "
        "needed to answer the request — the target model plus any models "
        "required to resolve its relations (e.g. res.users to assign, "
        "res.partner for a customer). Be strict: drop look-alikes that share "
        "a field layout but don't match the intent.\n"
        "Reply with a JSON array of technical names ONLY, most relevant "
        'first, e.g. ["sale.order","res.partner"]. No prose, no markdown.'
    )
    user_msg = f"USER REQUEST:\n{prompt}\n\nCANDIDATE MODELS:\n{listing}"
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]

    try:
        # Direct provider call — no tools, no auto-routing, no tier ceiling.
        # This must stay cheap, so we deliberately bypass _call_chat's
        # routing; but we still have to pick the right TRANSPORT. An admin
        # can now name any model here, including a cloud one, and sending
        # that to the local server would just fail.
        if provider.is_local or provider.provider_type not in _VALID_REMOTE_TYPES:
            rpc = _call_local_provider(provider, messages, tools=None)
        else:
            rpc = remote_call_chat(provider, messages, tools=None)
    except Exception as e:
        _logger.warning("DIGI-ERP AI re-rank: LLM call failed: %s", e)
        return candidates
    if not rpc.get("ok"):
        _cprint("yellow", "RERANK ERR",
                f"{rpc.get('error', '')[:120]} — using raw RAG shortlist")
        return candidates

    reply = (rpc.get("message") or {}).get("content") or ""
    chosen = _parse_rerank_reply(reply, names)
    if not chosen:
        _cprint("yellow", "RERANK EMPTY",
                "re-rank returned no valid names — using raw RAG shortlist")
        return candidates

    by_name = {c["model"]: c for c in candidates}
    pruned = [by_name[n] for n in chosen if n in by_name]
    _cprint("magenta", "RERANK",
            f"{len(candidates)} → {len(pruned)}  ({provider.model_name}): "
            + ", ".join(chosen[:6]))
    return pruned


def _resolve_provider(env, provider_id):
    """Look up the provider record by id. Returns:
      - None  → the chat falls back to the global DIGI-ERP AI settings
                (used when provider_id is empty or the sentinel 'local')
      - record → can be either a remote provider (openai/anthropic/…) or a
                local provider record carrying its own server URL + model.
    """
    if not provider_id or provider_id in ("local", "0", 0):
        return None
    try:
        pid = int(provider_id)
    except (TypeError, ValueError):
        return None
    # sudo(): `api_key` is restricted to group_ai_manager, and the transport
    # reads it off this record. A plain AI User must still be able to CHAT
    # through a cloud model — they just must never see the key, which is what
    # the field-level group already guarantees on every UI path.
    rec = env["digi_erp.model.llm.provider"].sudo().search([
        ("id", "=", pid), ("active", "=", True),
    ], limit=1)
    if not rec:
        _cprint("yellow", "PROVIDER MISS",
                f"id={provider_id} not found or inactive; "
                f"falling back to global DIGI-ERP AI settings")
        return None
    return rec


_VALID_REMOTE_TYPES = ("openai", "anthropic", "gemini", "deepseek", "xai", "openai_compat")


def _latest_user_text(messages):
    """The user's latest message text — fed to the auto-router for
    intent + complexity classification."""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _call_chat(env, provider_id, messages, tools=None, conv=None,
               cancel_check=None):
    """Single entry point for chat completion. Routes:
      • "auto" sentinel     → pick a provider per-message based on intent.
      • No provider record  → the built-in local model (Settings defaults).
      • Local record        → the server at the record's URL/model.
      • Remote record       → matching cloud API adapter.

    `conv` is the active conversation record (when known). It's passed
    down so the Auto router can apply the never-demote tier ceiling.

    Forgiving dispatch: any record that ISN'T a fully-specified remote
    (is_local=False AND provider_type is one of the supported APIs) is
    treated as local. So a record where is_local got toggled off but no
    valid API was picked still hits the local server and uses the
    record's model_name — the user can't end up with a dead provider."""
    # Auto mode: classify this turn's user message and resolve to a
    # specific provider record. From here on it's a normal call.
    if isinstance(provider_id, str) and provider_id in _AUTO_PROVIDER_SENTINELS:
        provider = _auto_pick_provider(env, _latest_user_text(messages),
                                       conv=conv)
    else:
        provider = _resolve_provider(env, provider_id)
    if provider is None:
        res = _call_default_local(messages, tools=tools,
                               cancel_check=cancel_check)
    elif provider.is_local or provider.provider_type not in _VALID_REMOTE_TYPES:
        res = _call_local_provider(provider, messages, tools=tools,
                                   cancel_check=cancel_check)
    else:
        # Properly-configured remote provider — tools may be disabled at the
        # provider level. (Remote APIs aren't streamed here, so cancel is only
        # honoured between tool-loop iterations for them, not mid-generation.)
        use_tools = tools if (tools and provider.supports_tools) else None
        res = remote_call_chat(provider, messages, tools=use_tools)

    # Tag the reply with WHICH model actually answered. This is the only
    # place that knows the resolved record — Auto routing picks it per
    # message, so the caller can't infer it from provider_id alone.
    if isinstance(res, dict):
        res["model_label"] = _provider_label(env, provider)
    return res


def _provider_label(env, provider):
    """Friendly, user-facing name of the model that answered.

    Falls back to the global chat model when no AI model record was
    used, so the UI always has something concrete to show instead of a
    generic 'local'."""
    if provider is not None:
        return provider.name or provider.model_name or ""
    try:
        return _cfg()["chat_model"] or ""
    except Exception:  # pragma: no cover - defensive
        return ""


def _history_to_messages(history):
    """Convertit l'historique persisté au format de messages OpenAI."""
    out = []
    for h in history or []:
        role = h.get("role")
        content = h.get("content") or ""
        if role in ("user", "assistant"):
            out.append({"role": role, "content": content})
        # tool_call/tool_result : on les ignore — l'historique n'a pas besoin
        # d'être rejoué tour par tour côté modèle, seul le verdict (assistant)
        # importe pour la mémoire utilisateur.
    return out


_TOKEN_LEAK_RE = re.compile(r"<\|[^|<>]{1,32}\|>")


def _repair_tool_args_str(raw):
    """Some models emit tool-call arguments as nearly-valid JSON
    riddled with the model's own tokenizer artifacts: `<|"|>`, `<|'|>`,
    Python literals (True/False/None), unquoted keys (`{model:"x"}`),
    a missing colon between key and value (`{model"x"}`), or single-
    quoted strings inside arrays. We pre-process all of those, then
    try progressively looser parsers until one returns a dict.

    Returns a dict on any parse success, or `None` if even ast eval
    couldn't make sense of it."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()

    # 1. Strip the model's tokenizer artifacts. The most common one is
    #    `<|"|>` wrapping every double-quote inside nested JSON, but
    #    the regex catches any `<|...|>` block under 32 chars to be safe.
    s = s.replace('<|"|>', '"').replace("<|'|>", "'")
    s = _TOKEN_LEAK_RE.sub("", s)

    # Each candidate is tried in turn — the FIRST one that parses as a
    # dict wins. Ordering matters: cheaper / less-lossy fixes first.
    candidates = [s]

    # 2. Python literals → JSON literals.
    repaired = re.sub(r"\bTrue\b",  "true",  s)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    repaired = re.sub(r"\bNone\b",  "null",  repaired)

    # 3. Missing colon between identifier-key and string-value:
    #    `{model"foo"}` → `{model:"foo"}` and `,model"foo"` →
    #    `,model:"foo"`. Limited to `{`/`,` contexts so we don't
    #    insert colons inside array values like `["a","b"]`.
    repaired = re.sub(
        r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*")',
        r'\1\2:\3', repaired,
    )

    # 4. Unquoted dict keys: `{model: "foo"}` → `{"model": "foo"}`.
    repaired = re.sub(
        r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)',
        r'\1"\2"\3', repaired,
    )
    candidates.append(repaired)

    # 5. Same as #4 but also convert single quotes to double quotes —
    #    handles Python-dict-style values like `[['a','=','b']]`. Done
    #    AFTER unquoted-key repair so we don't accidentally make
    #    `key:value` → `key:"value"` ambiguous.
    candidates.append(repaired.replace("'", '"'))

    for cand in candidates:
        try:
            v = json.loads(cand)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue

    # 6. Last resort — let Python's ast eval the original (post-strip).
    #    Safe because we only accept the result if it's a dict; ast
    #    literal_eval rejects calls / names / arbitrary expressions.
    try:
        import ast
        v = ast.literal_eval(s)
        if isinstance(v, dict):
            return v
    except (ValueError, SyntaxError):
        pass

    return None


def _scan_balanced(s, start):
    """Return the index of the `}` that closes the `{` at `s[start]`,
    respecting quoted strings. -1 if unbalanced."""
    depth = 0
    in_str = False
    q = None
    i = start
    while i < len(s):
        ch = s[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                in_str = False
        elif ch in ('"', "'"):
            in_str = True
            q = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _extract_envelope_tool_call(content, known):
    """Recover a tool call written as a JSON envelope in plain text:
        {"name": "<tool>", "arguments": {...}}
        {"tool": "<tool>",  "parameters": {...}}
        {"function": {"name": "<tool>", "arguments": {...}}}
    Instruct models (qwen2.5, llama3) do this when they 'narrate' the
    call instead of emitting a real tool_call. Returns a synthetic
    tool_calls list, or [] when no envelope is present."""
    known_set = set(known)
    # Locate `"name"|"tool"|"function" ... : ... "<toolname>"`.
    name_re = re.compile(
        r'"(?:name|tool|function)"\s*:\s*"([a-zA-Z0-9_]+)"')
    for m in name_re.finditer(content):
        tool = m.group(1)
        if tool not in known_set:
            continue
        # Find the sibling arguments object after the name.
        arg_key = re.compile(r'"(?:arguments|parameters|args|input)"\s*:\s*\{')
        am = arg_key.search(content, m.end())
        if am:
            brace_start = content.index("{", am.end() - 1)
            end = _scan_balanced(content, brace_start)
            if end > 0:
                return [{
                    "function": {"name": tool,
                                 "arguments": content[brace_start:end + 1]},
                    "id": "",
                }]
        # Name found but no arguments object → call with empty args
        # (some tools accept that; the dispatcher will validate).
        return [{
            "function": {"name": tool, "arguments": "{}"},
            "id": "",
        }]
    return []


def _extract_inline_tool_call(content):
    """Recover a tool call when the server's parser failed.

    When a model emits a tool call the server can't parse, the
    intended call shows up as PLAIN TEXT in the assistant's `content`
    field (the server returns `tool_calls=[]`). Typical shapes:

        call:odoo_search_read{model:"hr.employee", domain:[...]}
        odoo_search_read({"model": "hr.employee"})
        odoo_search_read{model:hr.employee, fields:[<|"|>id<|"|>]}

    This walks every known tool name (longest first to handle prefix
    collisions like `odoo_search` vs `odoo_search_read`) and looks for
    the name followed by a balanced `{...}` or `(...)` block. The
    extracted arg-string is returned as a synthetic tool_calls list in
    the standard shape, so the existing dispatch loop processes
    it unchanged — and crucially, _normalize_tool_args runs its full
    JSON-repair pipeline on the recovered arg-string just like a
    normally-emitted call.

    Returns [] if nothing matches."""
    if not content:
        return []
    known = sorted(_TOOLS_REGISTRY.keys(), key=len, reverse=True)

    # PASS 0 — standard JSON envelope emitted as plain text, e.g.
    #   {"name": "odoo_search_read", "arguments": {"model": "..."}}
    #   {"tool": "...", "parameters": {...}}
    #   {"function": {"name": "...", "arguments": {...}}}
    # Here the tool name is a VALUE, not a prefix, so the name-prefix
    # scan below misses it. Find the name key, then grab the sibling
    # arguments object by brace-balanced scan.
    env_calls = _extract_envelope_tool_call(content, known)
    if env_calls:
        return env_calls

    for name in known:
        pattern = re.compile(
            r"\b" + re.escape(name) + r"\s*[:=]?\s*([\{\(])",
        )
        m = pattern.search(content)
        if not m:
            continue
        opener = m.group(1)
        closer = "}" if opener == "{" else ")"
        start = m.end() - 1
        # Brace-balanced scan that respects single/double-quoted
        # strings (so a `}` inside a string literal doesn't close
        # the block).
        depth = 0
        in_str = False
        quote_char = None
        end = -1
        i = start
        while i < len(content):
            ch = content[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote_char:
                    in_str = False
            elif ch in ('"', "'"):
                in_str = True
                quote_char = ch
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end < 0:
            continue
        raw_args = content[start:end + 1]
        # When opener was `(`, strip the parens so _normalize_tool_args
        # sees a JSON-object shape (the repair pipeline expects {...}
        # at the top level, not (...)). Two shapes to handle:
        #   tool(key:value, ...)      → wrap in braces
        #   tool({key:value, ...})    → unwrap, the braces are inside
        if opener == "(":
            inner = raw_args[1:-1].strip()
            if inner.startswith("{") and inner.endswith("}"):
                raw_args = inner
            else:
                raw_args = "{" + inner + "}"
        return [{
            "function": {"name": name, "arguments": raw_args},
            "id": "",
        }]
    return []


def _normalize_tool_args(args):
    """Coerce tool-call args into a dict.

    The spec is loose: depending on the model and the tool-use
    parser version, `arguments` may already be a dict OR a JSON string
    OR a not-quite-JSON string with tokenizer artifacts inside.
    We try strict JSON first, then progressively looser repair passes
    (see _repair_tool_args_str). On total failure we log the raw
    payload so the operator can see what the model actually emitted —
    silent `{}` returns made the previous bug almost impossible to
    diagnose."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        repaired = _repair_tool_args_str(args)
        if repaired is not None:
            return repaired
        _logger.warning(
            "DIGI-ERP AI tool-call args UNPARSEABLE — raw payload "
            "(first 400 chars): %r", args[:400],
        )
        return {}
    return {}


_FILE_TOOLS = ("export_records_to_file", "generate_file",
               "generate_image", "modify_document")
_DATA_TOOLS = ("odoo_search_read", "odoo_call", "odoo_read")


def _is_document_content(content):
    """True when the assistant's text is actually a document body (HTML
    or a Markdown report) it should have put in a file rather than the
    chat. Used by the file-capture fallback."""
    if not content:
        return False
    s = content.strip()
    low = s.lower()
    if ("<h1" in low or "<table" in low or "<!doctype" in low
            or "<html" in low):
        return True
    # Markdown report: a heading plus enough body to be a document.
    if re.search(r"(^|\n)#{1,3}\s+\S", s) and len(s) > 300:
        return True
    # A Markdown table with several rows also counts.
    if low.count("|") >= 8 and "---" in low:
        return True
    return False


def _extract_document_content(content):
    """Pull just the document body out of the model's chat message,
    dropping leading/trailing chatter ("Here is the report…", "You can
    now download…"). For HTML, slice from the first block tag to the last
    closing `>`. For Markdown, slice from the first heading."""
    s = content or ""
    low = s.lower()
    starts = [low.find(t) for t in ("<!doctype", "<html", "<h1", "<table")
              if low.find(t) != -1]
    if starts:
        a = min(starts)
        b = s.rfind(">")
        if b > a:
            return s[a:b + 1].strip()
    mh = re.search(r"(^|\n)#{1,3}\s+\S", s)
    if mh:
        return s[mh.start():].strip()
    return s.strip()


def _run_messages_with_tools(env, messages, label="DIGI-ERP AI",
                             provider_id=None, conv=None, expect_file=False,
                             file_ext=".pdf", intent=None, cancel_check=None):
    """Run the tool loop and stamp the answer with the model that produced it.

    Thin wrapper around `_run_messages_with_tools_impl`. The loop can call
    `_call_chat` several times (one per iteration) and returns from a dozen
    places; rather than tagging each return site, we record the label of the
    LAST model that actually answered and attach it once, here. That matters
    in Auto mode, where the router may resolve a different provider per
    iteration — the user sees who wrote the final text."""
    seen = {}

    def _record(res):
        if isinstance(res, dict) and res.get("model_label"):
            seen["label"] = res["model_label"]
        return res

    result = _run_messages_with_tools_impl(
        env, messages, label=label, provider_id=provider_id, conv=conv,
        expect_file=expect_file, file_ext=file_ext, intent=intent,
        cancel_check=cancel_check, _on_call=_record,
    )
    if isinstance(result, dict) and seen.get("label"):
        result.setdefault("model_label", seen["label"])
    return result


def _run_messages_with_tools_impl(env, messages, label="DIGI-ERP AI",
                              provider_id=None, conv=None, expect_file=False,
                              file_ext=".pdf", intent=None, cancel_check=None,
                              _on_call=None):
    """Boucle tool-use sur une liste de messages déjà construite.

    `expect_file` — True when the user's intent was to produce a file.
    Used to push the model to actually CALL `generate_file` (instead of
    pasting the report into chat) and to refuse fabricated data.

    `provider_id` choisit le modèle (None = modèle local par défaut).
    `conv` (optional) — conversation record; passed to the Auto router
    so it can apply the never-demote tier ceiling across turns.

    `intent` (optional) — classified turn intent ('lookup'/'action'/
    'file'/…). Scopes the tool set we send so a plain lookup doesn't pay
    ~2k tokens for the four file/image schemas it can't use. None = all
    read-only tools (safe fallback).

    Retourne :
        {ok, response, model, tool_calls: [{name, params, result}, ...]}
    """
    if intent is None:
        tools_schema = build_tools_schema()
    else:
        tools_schema = build_tools_schema(only=tool_names_for_intent(intent))
    executed_calls = []  # [{name, params, result}]

    # Per-intent cap on how big a SINGLE tool result may grow before we trim it
    # (whole rows dropped at row boundaries + a paging note — see _truncate_json).
    # A lookup rarely needs 8k tokens of raw rows, and every oversized result is
    # re-sent on later loop iterations, so a tighter cap keeps the message tail
    # lean. Reports keep the full ceiling — they legitimately analyse more data.
    result_limit = {
        "lookup": 12000,
        "action": 16000,
        "file":   16000,
        "report": MAX_TOOL_RESULT_LEN,
    }.get(intent, MAX_TOOL_RESULT_LEN)

    _cprint("magenta", f"{label} START",
            f"uid={env.uid}  messages={len(messages)}  provider={provider_id or 'local'}")

    # Convergence guard: on a data-heavy request the model can keep READING
    # (paging raw rows with offset, re-checking fields) and never produce the
    # final answer / file — exhausting MAX_TOOL_ITERATIONS with 10+ search_reads.
    # We count data-fetch calls and, past READ_BUDGET, cut reading OFF: for a
    # file/report we then send ONLY the output tools (it can still write the
    # file but can't read more); otherwise we force a no-tools finish. A strong
    # directive tells it to conclude from the data it already has.
    READ_BUDGET = 10
    data_read_calls = 0
    converge_finish = False       # reads cut off → output-only (or no) tools
    converge_announced = False
    output_tools_schema = build_tools_schema(only=_FILE_TOOLS)
    # A report ALSO produces a file (generate_file), even though its intent
    # isn't literally "file" — so keep the output tools available for it on
    # convergence, not just for the "file" intent.
    produces_file = bool(expect_file) or intent in ("report", "file")

    nudges_used = 0
    MAX_NUDGES = 2

    # Loop-breaker: small models sometimes call
    # the SAME tool with the SAME args over and over, never advancing
    # (e.g. odoo_fields_get(project.task) ×15). We track how many times
    # each (name, args) signature has been seen. On a repeat we don't
    # re-run the tool — we feed back a blunt "you already have this,
    # move on" result. After REPEAT_LIMIT identical calls we force the
    # model to answer with NO tools so the turn always terminates.
    call_counts = {}            # signature -> times seen
    REPEAT_LIMIT = 2
    forced_finish = False       # next call goes out without tools

    def _sig(name, args):
        try:
            return name + "|" + json.dumps(args, sort_keys=True,
                                           ensure_ascii=False, default=str)
        except Exception:
            return name + "|" + str(args)

    for iteration in range(MAX_TOOL_ITERATIONS):
        # Honour a Stop BEFORE starting another (possibly long) model call —
        # e.g. the user hit Stop while a tool was running between iterations.
        if cancel_check and cancel_check():
            _cprint("yellow", f"{label} CANCELLED", "between iterations")
            return {"ok": False, "cancelled": True,
                    "error": _("Generation interrupted by the user."),
                    "tool_calls": executed_calls}
        _cprint("yellow", f"{label} ITER", f"#{iteration + 1}/{MAX_TOOL_ITERATIONS}")
        # When the loop-breaker tripped, send the request WITHOUT tools so
        # the model is forced to produce a textual final answer instead of
        # calling the same tool yet again. When the read budget is spent
        # (converge_finish) we cut reading off: a file turn keeps ONLY the
        # output tools so it can still generate the file; anything else gets
        # no tools and must write the answer.
        if converge_finish:
            turn_tools = output_tools_schema if produces_file else None
        elif forced_finish:
            turn_tools = None
        else:
            turn_tools = tools_schema
        rpc = _call_chat(env, provider_id, messages,
                         tools=turn_tools, conv=conv,
                         cancel_check=cancel_check)
        if _on_call:
            _on_call(rpc)
        # Cancelled mid-generation inside the streaming call.
        if rpc.get("cancelled"):
            _cprint("yellow", f"{label} CANCELLED", "mid-generation")
            return {"ok": False, "cancelled": True,
                    "error": rpc.get("error")
                    or _("Generation interrupted by the user."),
                    "tool_calls": executed_calls}
        if not rpc.get("ok"):
            _cprint("red", f"{label} ABORT", rpc.get("error", ""))
            return {**rpc, "tool_calls": executed_calls}

        msg = rpc["message"] or {}
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""

        # SAFETY NET: when the server's tool-call parser fails
        # (some models emit a format the server can't always extract),
        # the intended call lands in `content` as plain text. Recover it
        # before we treat the response as a "no tool" final answer.
        if not tool_calls and content:
            recovered = _extract_inline_tool_call(content)
            if recovered:
                _logger.info(
                    "[%s] tool_calls=[] but content has an inline call "
                    "— recovered via _extract_inline_tool_call: %s",
                    label, recovered[0]["function"]["name"],
                )
                _cprint("yellow", f"{label} INLINE TOOL",
                        f"recovered {recovered[0]['function']['name']} "
                        f"from content (server parser failed)")
                tool_calls = recovered
                # Empty the content so the assistant message we add to
                # history doesn't also carry the malformed raw call.
                content = ""

        if tool_calls:
            _cprint("cyan", f"{label} TOOLS",
                    f"model requested {len(tool_calls)} tool(s)")
            # Pre-normalise EVERY tool_call's arguments to a clean DICT
            # before pushing the assistant message back into the request.
            # /api/chat expects `function.arguments` to be an
            # OBJECT (map[string]any), NOT a JSON string. If we leave the
            # model's raw, malformed payload (a Python-ish
            # `odoo_search_read(model='res.user', ...)`) OR a json.dumps
            # string in place, the chat template renders it and its
            # tool-call grammar parser aborts the next iteration with
            #   "Value looks like object, but can't find closing '}' symbol"
            # killing the whole turn. Passing a real dict is what the server
            # both emits and re-accepts natively.
            sanitized_calls = []
            parsed_by_idx = {}
            for i, tc in enumerate(tool_calls):
                fn = (tc.get("function") or {})
                name = fn.get("name") or ""
                parsed = _normalize_tool_args(fn.get("arguments"))
                parsed_by_idx[i] = parsed
                sanitized_calls.append({
                    **{k: v for k, v in tc.items() if k != "function"},
                    "function": {
                        **{k: v for k, v in fn.items() if k != "arguments"},
                        "name": name,
                        # DICT, not a JSON string — see comment above.
                        "arguments": parsed if isinstance(parsed, dict) else {},
                    },
                })
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": sanitized_calls,
            })
            for i, tc in enumerate(sanitized_calls):
                fn = tc["function"]
                name = fn["name"]
                args = parsed_by_idx[i]
                sig = _sig(name, args)
                seen = call_counts.get(sig, 0)
                call_counts[sig] = seen + 1

                if seen >= REPEAT_LIMIT:
                    # Same call, same args, already done REPEAT_LIMIT times.
                    # Stop re-running it; tell the model to move on, and
                    # force the NEXT turn to answer without tools.
                    _cprint("red", f"{label} LOOP",
                            f"{name}({_truncate_json(args, 80)}) repeated "
                            f"{seen + 1}× — breaking loop, forcing finish")
                    messages.append({
                        "role": "tool",
                        "name": name,
                        "tool_call_id": tc.get("id") or "",
                        "content": (
                            "⛔ STOP. You have ALREADY called this exact "
                            "tool with these exact arguments and received "
                            "the result above (more than once). Calling it "
                            "again will NOT give new information. Use the "
                            "data you already have to produce your FINAL "
                            "answer now — write the full response (or call "
                            "a DIFFERENT tool with DIFFERENT arguments if a "
                            "distinct step truly remains). Do not repeat "
                            "this call."
                        ),
                    })
                    forced_finish = True
                    continue

                if seen >= 1:
                    # First repeat: don't burn a real tool execution, just
                    # remind the model it already has this.
                    _cprint("yellow", f"{label} DUP CALL",
                            f"{name} repeated ({seen + 1}×) — returning "
                            f"cached-reminder instead of re-executing")
                    messages.append({
                        "role": "tool",
                        "name": name,
                        "tool_call_id": tc.get("id") or "",
                        "content": (
                            "⚠️ You already called this exact tool with "
                            "these exact arguments above — the result is "
                            "unchanged. Do NOT call it again. Proceed to "
                            "the next distinct step or give your final "
                            "answer."
                        ),
                    })
                    continue

                # ── CONFIRMATION GATE ──────────────────────────────────────
                # If this call MODIFIES the database (create / write / a
                # state-changing button), we do NOT execute it here. Instead
                # we build a human-readable "before → after" preview, STOP the
                # loop, and hand the pending action back to the client. The
                # user reviews it and clicks Confirm — which replays this exact
                # call through /ai-solution/confirm-action. Reads and file
                # generation are never gated.
                if is_write_call(name, args):
                    _cprint("magenta", f"{label} CONFIRM GATE",
                            f"write intercepted: {name} "
                            f"{args.get('model')}.{args.get('method')} "
                            f"— pausing for user confirmation")
                    try:
                        preview = build_action_preview(env, args)
                    except Exception:
                        _logger.exception("build_action_preview failed")
                        preview = {
                            "kind": "method",
                            "model_label": args.get("model", ""),
                            "summary": _("Change to confirm"),
                            "changes": [], "record_ids": [], "note": "",
                        }
                    # Any preamble the model already wrote (e.g. "Je vais
                    # créer la facture suivante :") becomes the card's intro.
                    intro = (content or "").strip()
                    return {
                        "ok": True,
                        "response": intro or _("Here is the action to confirm."),
                        "model": "remote" if provider_id else _cfg()["chat_model"],
                        "tool_calls": executed_calls,
                        "pending_action": {
                            # The EXACT tool call to replay on confirm. The
                            # client echoes this back untouched — it is never
                            # trusted from the client beyond re-dispatch, which
                            # re-applies the user's own ACL.
                            "tool": name,
                            "params": args,
                            "preview": preview,
                        },
                    }

                _logger.info("DIGI-ERP AI call_tool name=%s args=%s",
                             name, _truncate_json(args, 300))
                result = tool_dispatch(env, name, args)
                if name not in _FILE_TOOLS:
                    data_read_calls += 1
                executed_calls.append({"name": name, "params": args, "result": result})
                messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": tc.get("id") or "",
                    "content": _truncate_json(result, result_limit),
                })

            # Read budget spent → cut off further reading on the NEXT turn and
            # tell the model to conclude from what it already has. Fires once.
            if data_read_calls >= READ_BUDGET and not converge_announced:
                converge_finish = True
                converge_announced = True
                _cprint("red", f"{label} CONVERGE",
                        f"{data_read_calls} data reads ≥ budget {READ_BUDGET} "
                        f"— cutting reads off, forcing final output")
                if produces_file:
                    directive = (
                        "⛔ STOP READING. You have already queried the database "
                        f"{data_read_calls} times — that is enough data. Do NOT "
                        "call any more read tools (odoo_search_read, odoo_call, "
                        "odoo_fields_get…). NOW call `generate_file` with the "
                        "complete report (HTML) built from the data you already "
                        "gathered above. Work with what you have; do not fetch "
                        "more. Do NOT page further with offset."
                    )
                else:
                    directive = (
                        "⛔ STOP READING. You have already queried the database "
                        f"{data_read_calls} times — that is enough. Write your "
                        "FINAL answer NOW from the data above. Do not call any "
                        "more tools, and do not page further with offset."
                    )
                messages.append({"role": "user", "content": directive})
            continue

        # No tool_calls: did the model narrate intent without acting?
        # A model often writes "Étape 1/3 : Je vais chercher..." then stops.
        # Nudge it once instead of treating that as a final answer.
        if (
            nudges_used < MAX_NUDGES
            and content.strip()
            and _NARRATION_RE.search(content)
        ):
            nudges_used += 1
            _cprint("yellow", f"{label} NUDGE",
                    f"narration without tool_call → re-prompting "
                    f"({nudges_used}/{MAX_NUDGES})")
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": (
                    "You described what you were going to do instead of doing "
                    "it. Emit the tool_call NOW, directly, with no preamble "
                    "and no step announcement. If there is nothing left to "
                    "do, give the final recap (number / id of the record "
                    "you created)."
                ),
            })
            continue

        # File intent, but the model wrote a final TEXT answer without
        # ever calling a file tool → it pasted the report in chat instead
        # of producing the file (and may have fabricated the data). Nudge
        # it to actually act: search first if it has no real data, then
        # call generate_file with what it just wrote as `content`.
        file_called = any(c["name"] in _FILE_TOOLS for c in executed_calls)
        data_called = any(
            c["name"] in _DATA_TOOLS and not (isinstance(c["result"], dict)
                                              and c["result"].get("error"))
            for c in executed_calls
        )

        # ── AUTO-CAPTURE ────────────────────────────────────────────────
        # The model wrote the whole document (HTML / Markdown report) in
        # chat instead of calling generate_file. If it has REAL data, we
        # don't beg it to retry — we build the file OURSELVES from that
        # content. The user always gets a file; no reliance on the small
        # model wrapping the tool call. Gated on data_called so we never
        # materialise fabricated data into a PDF.
        if (expect_file and not file_called and data_called
                and _is_document_content(content)):
            doc = _extract_document_content(content)
            fname = f"rapport{file_ext}"
            _cprint("green", f"{label} AUTO-FILE",
                    f"capturing model document → {fname} "
                    f"({len(doc)} chars of {'HTML' if '<' in doc[:200] else 'Markdown'})")
            gen = tool_dispatch(env, "generate_file",
                                {"filename": fname, "content": doc})
            if isinstance(gen, dict) and gen.get("ok"):
                executed_calls.append({
                    "name": "generate_file",
                    "params": {"filename": fname, "content": "<auto-captured>"},
                    "result": gen,
                })
                return {
                    "ok": True,
                    "response": gen.get("message")
                    or _("The file **%(name)s** was generated. "
                         "[Download](%(url)s)",
                         name=fname, url=gen.get("download_url", "#")),
                    "model": _cfg()["chat_model"],
                    "tool_calls": executed_calls,
                }
            _cprint("yellow", f"{label} AUTO-FILE FAIL",
                    f"generate_file returned {gen!r} — falling through to nudge")

        if (expect_file and not file_called and content.strip()
                and nudges_used < MAX_NUDGES):
            nudges_used += 1
            _cprint("yellow", f"{label} FILE NUDGE",
                    f"file expected but generate_file never called "
                    f"(data_fetched={data_called}) → re-prompting "
                    f"({nudges_used}/{MAX_NUDGES})")
            messages.append({"role": "assistant", "content": content})
            if not data_called:
                nudge = (
                    "STOP — you wrote a report in chat using data you did "
                    "NOT retrieve. The names/values above are INVENTED and "
                    "unacceptable. Do this now: (1) call `odoo_search_read` "
                    "on the REAL model (projects → `project.project`, tasks "
                    "→ `project.task`) requesting the fields you need; "
                    "(2) THEN call `generate_file` with the requested "
                    "filename and the report built from the REAL rows as "
                    "`content`. Never fabricate records."
                )
            else:
                nudge = (
                    "You wrote the report in chat instead of creating the "
                    "file. Do NOT paste it here. Call `generate_file` NOW "
                    "with the requested filename (e.g. a .pdf) and the FULL "
                    "report — built ONLY from the real data you already "
                    "fetched — as the `content` argument (Markdown or "
                    "HTML). Your reply after that is just the download link."
                )
            messages.append({"role": "user", "content": nudge})
            forced_finish = False  # allow tools again so it can act
            continue

        if not executed_calls:
            _cprint("yellow", f"{label} NO TOOL",
                    f"model answered directly ({len(content)} chars)")
        else:
            _cprint("green", f"{label} DONE",
                    f"{len(executed_calls)} tool call(s) — {len(content)} chars final answer")

        # Empty reply, but a file was expected and we DID fetch real data:
        # the model fetched then went silent without writing the report or
        # calling generate_file. Don't give up — nudge it once to emit the
        # document so auto-capture (or its own generate_file) can finish.
        if (not content.strip() and expect_file and data_called
                and not file_called and nudges_used < MAX_NUDGES):
            nudges_used += 1
            _cprint("yellow", f"{label} EMPTY→FILE NUDGE",
                    f"empty reply after data fetch → asking for the document "
                    f"({nudges_used}/{MAX_NUDGES})")
            messages.append({
                "role": "user",
                "content": (
                    "You fetched the data but returned nothing. Now WRITE "
                    "the document: either call `generate_file` with the "
                    "requested filename and the full content built from the "
                    "real rows above, OR output the complete report (Markdown "
                    "or HTML) directly. Use ONLY the real data already "
                    "fetched. Do not return an empty message."
                ),
            })
            forced_finish = False
            continue

        # The model returned no text. Before this fix the JS rendered a
        # silent "(réponse vide)" bubble — useless to debug. Build an
        # actionable message that explains *why* nothing came back.
        if not content.strip():
            diag = _diagnose_empty_response(
                env, provider_id, executed_calls, msg, iteration + 1,
            )
            _cprint("red", f"{label} EMPTY",
                    "model produced no text — surfacing diagnostic")
            return {
                "ok": False,
                "error": diag,
                "tool_calls": executed_calls,
            }
        return {"ok": True, "response": content, "model": _cfg()["chat_model"],
                "tool_calls": executed_calls}

    _cprint("red", f"{label} MAX ITER",
            f"hit {MAX_TOOL_ITERATIONS} iterations without final answer")
    return {
        "ok": False,
        "error": (
            _("The model did not finish within %(n)s iterations (tool loop "
              "stopped). Tools already run: %(tools)s. Rephrase your request "
              "in smaller steps, or switch to a more capable model.",
              n=MAX_TOOL_ITERATIONS,
              tools=", ".join(c["name"] for c in executed_calls) or _("none"))
        ),
        "tool_calls": executed_calls,
    }


def _diagnose_empty_response(env, provider_id, executed_calls, last_msg, iters_used):
    """Build a human-readable explanation when the LLM returns no text.

    Empty content with no tool_calls is almost always one of:
      • the local model crashed / OOM / context overflow (the server returns
        an empty message with done_reason='length' or 'load'),
      • a remote provider hit its content filter,
      • the model finished a tool loop without writing a final recap.

    Surfacing model name, provider, executed tools and last finish reason
    gives the user something concrete to act on instead of "(réponse vide)".
    """
    # Resolve the actual model that ran, not the global default.
    model_label = _cfg().get("chat_model") or "?"
    provider_label = "local (Settings → DIGI-ERP AI)"
    try:
        if provider_id and str(provider_id).isdigit():
            prov = env["digi_erp.model.llm.provider"].sudo().browse(int(provider_id))
            if prov.exists():
                model_label = prov.model_name or model_label
                provider_label = (
                    f"{prov.name!r} ({'self-hosted' if prov.is_local else prov.provider_type})"
                )
    except Exception:
        pass

    done_reason = ""
    if isinstance(last_msg, dict):
        # A local server exposes `done_reason` on the message envelope, OpenAI
        # uses `finish_reason` — surface whichever is present.
        done_reason = (
            last_msg.get("done_reason")
            or last_msg.get("finish_reason")
            or ""
        )

    tool_recap = (
        ", ".join(c["name"] for c in executed_calls) if executed_calls else "aucun"
    )

    parts = [
        _("The model returned no text."),
        f"• Provider : {provider_label}",
        u"• " + _("Model: %s", model_label),
        u"• " + _("Iterations used: %s", iters_used),
        u"• " + _("Tools run: %s", tool_recap),
    ]
    if done_reason:
        parts.append(f"• Raison de fin : {done_reason}")

    # Best-guess remediation based on done_reason / context.
    reason_lc = (done_reason or "").lower()
    if reason_lc in ("length", "max_tokens"):
        parts.append(
            _("Likely cause: the answer was cut off by the token limit. "
              "Raise 'Max output tokens' on the model, or ask for a "
              "shorter answer.")
        )
    elif reason_lc in ("load", "unload"):
        parts.append(
            _("Likely cause: the AI server unloaded the model mid-request "
              "(out of memory, or unloaded after idling). Raise 'Keep "
              "alive' on the model.")
        )
    elif reason_lc in ("content_filter", "safety"):
        parts.append(
            _("Likely cause: the AI service blocked the answer (content "
              "filter). Rephrase the request.")
        )
    elif executed_calls and not done_reason:
        parts.append(
            _("Likely cause: the model ran the tools but produced no final "
              "recap. Ask again ('summarise what you just did'), or switch "
              "to a more capable model.")
        )
    else:
        parts.append(
            _("Possible causes: the context is full (reduce the context "
              "window or shorten the history), the model was unloaded from "
              "memory, or the model is too small for the request. Check the "
              "Odoo log and the state of your AI server.")
        )
    return "\n".join(parts)


# ── Cross-lingual normalisation ──────────────────────────────────────────
# Two explicit steps so it's clear what's happening:
#   1. _detect_language(text)            → ISO code via `langdetect`
#   2. _translate_to_english(text, lang) → English via `deep_translator`
#
# Together they wrap the user's input so:
#   • the regex classifier ALWAYS sees English (and stays robust),
#   • small LLMs reason on English (where they're stronger),
#   • the model is told "reply in <user language>" so the conversation
#     stays in the user's language end-to-end.
#
# Both steps fail gracefully (libs missing, Google unreachable, garbled
# detection) — the chat never breaks because of a translation hiccup.

import functools as _functools


# Map ISO codes from langdetect → human-readable name we put in the
# "reply in" hint. Models recognize these names reliably.
_LANG_NAMES = {
    "ar": "Arabic", "he": "Hebrew", "fa": "Persian", "ur": "Urdu",
    "fr": "French", "es": "Spanish", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh-cn": "Chinese", "zh-tw": "Chinese (Traditional)", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "tr": "Turkish", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "fi": "Finnish", "no": "Norwegian",
    "hi": "Hindi", "bn": "Bengali", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay", "tl": "Filipino",
    "el": "Greek", "cs": "Czech", "ro": "Romanian", "hu": "Hungarian",
    "uk": "Ukrainian", "bg": "Bulgarian", "hr": "Croatian", "sr": "Serbian",
    "sk": "Slovak", "sl": "Slovenian",
}


# STEP 1 — language detection. Tiny, pure-Python, no network.
@_functools.lru_cache(maxsize=1024)
def _detect_language(text):
    """Return the ISO 639-1 language code for `text`, or 'en' on
    failure / when the langdetect package isn't installed. Cached
    per worker so the same prompt is detected only once."""
    if not text or not text.strip():
        return "en"
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0   # deterministic detection
        return detect(text).lower()
    except ImportError:
        _logger.warning(
            "DIGI-ERP AI: `langdetect` not installed — cross-lingual "
            "routing disabled. Run `pip install langdetect`.")
        return "en"
    except Exception:
        # Garbled input / detector confused on very short text.
        # Treat as English so the chat still works.
        return "en"


# STEP 2 — actual translation (only invoked when detection said non-en).
@_functools.lru_cache(maxsize=1024)
def _translate_to_english(text, source_lang):
    """Translate `text` from `source_lang` (ISO code) to English via
    deep-translator. Returns the translated string, or the original on
    failure. Cached per worker."""
    if not text or not text.strip() or source_lang.startswith("en"):
        return text
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(
            source=source_lang, target="en",
        ).translate(text)
        return translated or text
    except ImportError:
        _logger.warning(
            "DIGI-ERP AI: `deep_translator` not installed — translation "
            "disabled, keeping the prompt in `%s`. Run "
            "`pip install deep-translator`.", source_lang)
        return text
    except Exception as e:
        _logger.warning(
            "DIGI-ERP AI: translation failed (lang=%s, err=%s) — keeping "
            "original prompt.", source_lang, e)
        return text


def _english_for_classifier(user_prompt):
    """Return an English version of `user_prompt` for the regex-based
    classifiers (`_classify_query_intent`, `_detect_components`).

    The LLM itself NEVER sees this translation — only the classifiers
    do. Routing the user's words through Google Translate loses typos,
    nuance and the user's own "respond in X" directive, all of which
    modern multilingual LLMs handle natively. So the original prompt
    goes to the model untouched; the English shadow exists only to
    keep our English-only regexes accurate cross-lingual.

    Returns the original prompt when:
      • already English,
      • too long (translation services rate-limit / degrade),
      • `langdetect` or `deep_translator` aren't installed,
      • the translation library returned the input unchanged.
    """
    if not user_prompt:
        return user_prompt
    if len(user_prompt) > 5000:
        return user_prompt

    source_lang = _detect_language(user_prompt.strip())
    if source_lang.startswith("en"):
        return user_prompt

    english = _translate_to_english(user_prompt.strip(), source_lang)
    if english == user_prompt:
        return user_prompt

    _cprint("cyan", "CLASSIFIER TRANSLATE",
            f"src={source_lang} ({_LANG_NAMES.get(source_lang, source_lang)})  "
            f"orig={user_prompt[:60]!r}  → en={english[:60]!r}")
    return english


# ── Multi-company scoping ──────────────────────────────────────────────────
#
# Odoo scopes multi-company records through `allowed_company_ids` in the
# context: the res.company record rules read it to decide which companies'
# records are visible. Our tool loop was running under whatever single
# company happened to be the env default, so searches silently missed
# records belonging to the user's OTHER selected companies. This helper
# builds a company-aware env once per turn; the whole tool loop inherits it.
#
# Security: we NEVER trust the client to widen access. The requested company
# ids are intersected with the user's OWN `company_ids` (their real allowed
# set). Anything outside is dropped. Empty / invalid selection → fall back to
# ALL the user's allowed companies (so the default behaviour already fixes
# the "only sees one company" bug, before anyone touches the selector).

def _resolve_company_ids(env, requested_ids):
    """Return a clean, ordered list of company ids to activate for this turn.

    `requested_ids` is whatever the client sent (list/CSV/None). The result
    is always a non-empty subset of the user's allowed companies, ordered
    with the user's current/default company first so a single-company user
    is completely unaffected."""
    user = env.user
    allowed = user.company_ids            # the user's REAL allowed set (ACL)
    allowed_ids = allowed.ids
    if not allowed_ids:
        # Degenerate (shouldn't happen for a real user) — use the env company.
        return env.company.ids

    # Normalise the request into a list of ints.
    req = []
    if isinstance(requested_ids, (list, tuple)):
        raw = requested_ids
    elif isinstance(requested_ids, str):
        raw = requested_ids.split(",")
    else:
        raw = []
    for x in raw:
        try:
            req.append(int(str(x).strip()))
        except (TypeError, ValueError):
            continue

    # Keep only companies the user is actually allowed into.
    chosen = [cid for cid in req if cid in allowed_ids]
    if not chosen:
        # No valid explicit selection → default to ALL allowed companies.
        chosen = list(allowed_ids)

    # Put the user's default company first (Odoo treats the head of
    # allowed_company_ids as the "current" company for defaults/new records).
    default_cid = user.company_id.id
    if default_cid in chosen:
        chosen = [default_cid] + [c for c in chosen if c != default_cid]
    # De-dup while preserving order.
    seen, ordered = set(), []
    for c in chosen:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _company_scoped_env(env, requested_ids):
    """Clone `env` with `allowed_company_ids` set so the tool loop sees every
    selected company. Returns (scoped_env, company_records)."""
    company_ids = _resolve_company_ids(env, requested_ids)
    scoped = env(context=dict(
        env.context,
        allowed_company_ids=company_ids,
    ))
    companies = scoped["res.company"].browse(company_ids)
    return scoped, companies


def _company_scope_block(companies):
    """A short system-prompt note telling the model which companies its data
    tools currently see, so it phrases answers correctly (and doesn't claim a
    total is 'the company's' when several are in scope)."""
    if not companies:
        return ""
    names = ", ".join(companies.mapped("name"))
    if len(companies) == 1:
        return (f"=== COMPANY SCOPE ===\nAll your data tools operate within a "
                f"SINGLE company: {names}. Every search / count / export is "
                f"limited to it.\n=== END COMPANY SCOPE ===")
    return (f"=== COMPANY SCOPE ===\nYour data tools currently span "
            f"{len(companies)} companies: {names}. Searches, counts and "
            f"exports include records from ALL of them together. When a total "
            f"could be ambiguous, say it covers these companies, and break it "
            f"down per company if the user needs it (there is a company_id "
            f"field on most models).\n=== END COMPANY SCOPE ===")


def _run_with_tools(env, user_prompt, history=None, extra_context="",
                     provider_id=None, conv=None, companies=None,
                     cancel_check=None):
    """Boucle native function-calling pour une question utilisateur classique.

    `conv` (optional) — conversation record; passed through so the
    Auto router can apply the never-demote tier ceiling across turns.

    `companies` (optional) — the res.company recordset the (already
    company-scoped) `env` is operating within. Used only to tell the model,
    in the system prompt, which companies its data tools currently see.

    Two-track language handling:
      • The system-prompt builder runs `_classify_query_intent` on the
        English shadow so its regex stays accurate cross-lingual.
      • The LLM receives the ORIGINAL prompt — typos, nuance and the
        user's own "respond in X" directive are preserved. Modern
        multilingual models reason fine in the user's language.
    """
    english_shadow = _english_for_classifier(user_prompt)
    intent = _classify_query_intent(english_shadow)
    # An IMAGE request is also intent='file', but it's served by
    # `generate_image` — NOT by the data→generate_file report path. The
    # file nudge/auto-capture must NOT fire for it (otherwise "generate a
    # simple image" got hijacked into an odoo_search_read on projects).
    is_image = bool(_COMPONENT_PATTERNS["image"].search(english_shadow))
    expect_data_file = (intent == "file") and not is_image
    # Desired output extension for the file-capture fallback. Pick what the
    # user explicitly named; default to .pdf for a report-style request.
    file_ext = ".pdf"
    m = re.search(r"\.?\b(pdf|docx|xlsx|pptx|csv|txt)\b",
                  english_shadow, re.IGNORECASE)
    if m:
        file_ext = "." + m.group(1).lower()
    # Prepend the company-scope note so the model knows which companies its
    # data tools currently span (multi-company awareness).
    scope_note = _company_scope_block(companies) if companies is not None else ""
    if scope_note:
        extra_context = (scope_note + "\n\n" + extra_context) if extra_context \
            else scope_note
    messages = [{"role": "system",
                 "content": _build_system_prompt(env.user, english_shadow,
                                                 extra_context, conv=conv)}]
    messages.extend(_history_to_messages(history))
    messages.append({"role": "user", "content": user_prompt})
    result = _run_messages_with_tools(env, messages, label="DIGI-ERP AI",
                                      provider_id=provider_id, conv=conv,
                                      expect_file=expect_data_file,
                                      file_ext=file_ext, intent=intent,
                                      cancel_check=cancel_check)
    # Surface the classified intent so the endpoint can build follow-up
    # suggestions without re-running the (possibly network-bound) English
    # shadow translation a second time.
    if isinstance(result, dict):
        result.setdefault("intent", intent)
    return result


# ── Activity receipts + follow-up suggestions ──────────────────────────────────
#
# These two helpers power the "advanced assistant" feel WITHOUT any extra LLM
# cost: both are derived purely from data we already have — the tool calls the
# model executed this turn, plus the classified intent. `_summarize_tool_calls`
# turns the raw executed calls into a compact, human-readable receipt ("🔍
# Interrogé account.move · 42 résultats") that the UI shows under the answer so
# the user can SEE the assistant actually worked. `_suggest_followups` proposes
# a few tappable next steps so the chat feels like it's thinking one move ahead.

# tool name -> (emoji, verb phrase) for the receipt line.
# Verbs are written to read naturally in front of a *business* noun
# (see _MODEL_LABELS), e.g. "Looked up the invoices", not "Queried
# account.move". The goal is that a non-technical user understands every
# line without knowing Odoo's model names or method names.
#
# The phrases are built lazily through `_activity_meta()` rather than stored
# here already translated: this dict is evaluated at import time, when there
# is no user and therefore no language, so `_()` at this level would freeze
# whatever locale the server happened to boot in.
_ACTIVITY_META = {
    "whoami":                 ("👤", lambda: _("Checked who you are")),
    "odoo_models_search":     ("🧭", lambda: _("Looked for where the information lives")),
    "odoo_fields_get":        ("📖", lambda: _("Reviewed the information available on")),
    "odoo_search_read":       ("🔍", lambda: _("Looked up")),
    "odoo_read":              ("📄", lambda: _("Looked at the detail of")),
    "odoo_search_count":      ("🔢", lambda: _("Counted")),
    "odoo_call":              ("⚙️", lambda: _("Analysed")),
    "export_records_to_file": ("📤", lambda: _("Exported")),
    "generate_file":          ("📝", lambda: _("Prepared your file")),
    "modify_document":        ("✏️", lambda: _("Updated your document")),
    "generate_image":         ("🎨", lambda: _("Created an image")),
}


def _activity_meta(name):
    """(icon, verb) for a tool, with the verb resolved in the user's language."""
    icon, verb = _ACTIVITY_META.get(name, ("•", None))
    return icon, (verb() if verb else name)


# Odoo technical model name -> plain business noun shown to the user.
# Covers the models the assistant touches most often. Anything not listed
# falls back to a cleaned-up version of the technical name (see
# _humanize_model), so a new model still reads acceptably.
# Same lazy-callable reason as _ACTIVITY_META above.
_MODEL_LABELS = {
    "account.move":        lambda: _("the invoices"),
    "account.move.line":   lambda: _("the invoice lines"),
    "account.payment":     lambda: _("the payments"),
    "sale.order":          lambda: _("the sales orders"),
    "sale.order.line":     lambda: _("the sales order lines"),
    "sale.report":         lambda: _("the sales statistics"),
    "purchase.order":      lambda: _("the purchase orders"),
    "purchase.report":     lambda: _("the purchase statistics"),
    "crm.lead":            lambda: _("the opportunities"),
    "res.partner":         lambda: _("the contacts"),
    "res.users":           lambda: _("the users"),
    "product.template":    lambda: _("the products"),
    "product.product":     lambda: _("the products"),
    "stock.picking":       lambda: _("the deliveries"),
    "stock.move":          lambda: _("the stock moves"),
    "stock.quant":         lambda: _("the stock on hand"),
    "project.project":     lambda: _("the projects"),
    "project.task":        lambda: _("the tasks"),
    "hr.employee":         lambda: _("the employees"),
    "hr.leave":            lambda: _("the time off"),
    "hr.department":       lambda: _("the departments"),
    "account.analytic.line": lambda: _("the timesheets"),
    "mrp.production":      lambda: _("the manufacturing orders"),
    "pos.order":           lambda: _("the point-of-sale orders"),
}


def _humanize_model(model):
    """Fallback plain label for a model not in _MODEL_LABELS: drop the
    technical prefix and turn dots/underscores into spaces."""
    if not model:
        return ""
    label = _MODEL_LABELS.get(model)
    if label:
        return label()
    tail = model.split(".")[-1].replace("_", " ").strip()
    return _("the %s", tail) if tail else model


def _summarize_tool_calls(tool_calls):
    """Compact, human-readable receipt of what the model DID this turn.

    Returns a list of {icon, label, detail, ok} dicts — small enough to ship to
    the browser and persist. Never carries row payloads: only the model, the
    key params (method / filename) and a one-line outcome. Pure/zero-cost."""
    steps = []
    for call in tool_calls or []:
        name = call.get("name") or ""
        params = call.get("params") if isinstance(call.get("params"), dict) else {}
        result = call.get("result")
        icon, verb = _activity_meta(name)
        model = params.get("model") or ""

        detail, ok = "", True
        if isinstance(result, dict):
            if result.get("error"):
                ok, detail = False, _("failed")
            elif isinstance(result.get("count"), int):
                detail = _("%s result(s)", result["count"])
            elif result.get("filename"):
                detail = result["filename"]
        elif isinstance(result, int):
            detail = str(result)

        human_model = _humanize_model(model)
        if name == "odoo_call":
            # Never show the raw method name (read_group / name_search / …).
            # Common analytical methods just read as "analysed the data".
            meth = params.get("method") or ""
            _CALC_METHODS = {
                "read_group", "_read_group", "read_progress_bar",
                "web_read_group", "formatted_read_group",
            }
            if meth in _CALC_METHODS:
                label = (_("Analysed %s", human_model) if human_model
                         else _("Analysed the data"))
            elif human_model:
                label = f"{verb} {human_model}"
            else:
                label = verb
            detail = ""
        elif name == "odoo_models_search":
            # The user doesn't need to see our internal search keyword.
            label = verb
        elif name == "generate_file":
            label = verb
            detail = detail or params.get("filename") or ""
        elif human_model and name in (
            "odoo_search_read", "odoo_read", "odoo_search_count",
            "odoo_fields_get", "export_records_to_file",
        ):
            label = f"{verb} {human_model}"
        else:
            label = verb
        step = {"icon": icon, "label": label, "detail": detail, "ok": ok}
        # Collapse consecutive identical actions (e.g. several read_group
        # calls on the same model) so the receipt stays short and readable.
        # Keep the latest outcome; keep a detail if the earlier one had none.
        if steps and steps[-1]["label"] == label and steps[-1]["icon"] == icon:
            prev = steps[-1]
            prev["ok"] = prev["ok"] and ok
            if not prev["detail"] and detail:
                prev["detail"] = detail
            continue
        steps.append(step)
    return steps


# Per-model curated refinements — tasteful next-step chips keyed on the model
# the turn actually touched. Kept short (the UI shows ≤3). Lazy callables for
# the same reason as _ACTIVITY_META: this runs at import time, before there is
# a user whose language could be honoured.
_FOLLOWUP_BY_MODEL = {
    "account.move":     lambda: [_("Unpaid only"), _("Group by customer"),
                                 _("Export to PDF")],
    "account.move.line": lambda: [_("Group by account"), _("Total amount"),
                                  _("Export to Excel")],
    "project.project":  lambda: [_("By manager"), _("Late only"),
                                 _("Export to PDF")],
    "project.task":     lambda: [_("Mine only"), _("By stage"),
                                 _("Group by project")],
    "hr.employee":      lambda: [_("By department"), _("Export the list"),
                                 _("Active only")],
    "hr.leave":         lambda: [_("Pending only"), _("This month"),
                                 _("By employee")],
    "res.partner":      lambda: [_("Customers only"), _("Vendors only"),
                                 _("Export to Excel")],
    "sale.order":       lambda: [_("Confirmed only"), _("Total revenue"),
                                 _("By customer")],
    "purchase.order":   lambda: [_("Confirmed only"), _("By vendor"),
                                 _("Total amount")],
    "stock.picking":    lambda: [_("To do only"), _("By warehouse"),
                                 _("Late ones")],
    "product.template": lambda: [_("Out of stock"), _("By category"),
                                 _("Export the list")],
    "crm.lead":         lambda: [_("Won only"), _("By salesperson"),
                                 _("This quarter")],
}


def _suggest_followups(tool_calls, user_prompt=""):
    """Propose ≤3 tappable next-step chips, rule-based & zero-cost.

    Only fires when a real DATA tool ran this turn (so greetings, identity
    answers and pure chit-chat get none). Prefers curated per-model
    refinements; falls back to a generic set based on whether the turn
    counted vs listed vs produced a file."""
    calls = tool_calls or []
    data_tools = {"odoo_search_read", "odoo_read", "odoo_search_count",
                  "export_records_to_file"}
    used = [c.get("name") for c in calls]
    if not any(n in data_tools for n in used):
        return []

    # Primary model = the one the data tools hit most often.
    from collections import Counter
    models = Counter(
        (c.get("params") or {}).get("model")
        for c in calls
        if c.get("name") in data_tools
        and isinstance(c.get("params"), dict)
        and (c.get("params") or {}).get("model")
    )
    if models:
        top_model = models.most_common(1)[0][0]
        if top_model in _FOLLOWUP_BY_MODEL:
            return _FOLLOWUP_BY_MODEL[top_model]()

    # Generic fallback keyed on the shape of what happened.
    produced_file = any(n in ("export_records_to_file", "generate_file")
                        for n in used)
    counted = "odoo_search_count" in used and "odoo_search_read" not in used
    if produced_file:
        return [_("Add more detail"), _("A different format")]
    if counted:
        return [_("See the detailed list"), _("Export to PDF")]
    return [_("Export these results"), _("Refine the filter")]


# ──────────────────────────────────────────────────────────────────────────────


class DigiErpAiChatController(http.Controller):

    # ── Page portail ──────────────────────────────────────────────────────────
    @http.route('/my/ai-solution', type='http', auth='ai_user', website=True)
    def ai_chat_portal_page(self, **kwargs):
        """Page de chat dans le portail utilisateur."""
        values = {
            'page_name': 'ai_chat',
            'user_name': request.env.user.name,
        }
        return request.render('digi_erp_ai_solution.portal_ai_chat', values)

    # ── Page chat (login obligatoire pour utiliser les outils Odoo) ──────────
    @http.route('/ai-solution', type='http', auth='ai_user', website=True)
    def ai_chat_page(self, **kwargs):
        """Page principale du chat. Réservée aux membres DIGI-ERP AI ; `request.env`
        porte l'utilisateur connecté lors des appels d'outils."""
        values = {
            'page_name': 'ai_chat',
            'user_name': request.env.user.name,
        }
        return request.render('digi_erp_ai_solution.portal_ai_chat', values)

    # ── LLM providers (per-user API keys for OpenAI/Claude/Gemini/Grok/…) ────
    # Configuration UI lives in the backend (DIGI-ERP AI → Configuration → AI Models).
    # Only the read-only list endpoint is exposed to the chat UI.

    @http.route('/ai-solution/providers', type='json', auth='ai_user',
                methods=['POST'], csrf=False)
    def list_providers(self, **kwargs):
        """Return the user's configured LLM providers for the chat dropdown.
        Each user is responsible for creating their own (DIGI-ERP AI → LLM
        Providers); when none are configured the chat falls back to the
        global settings from Settings → DIGI-ERP AI."""
        # sudo() is safe here BECAUSE of to_public_dict(), which whitelists
        # the fields it emits and never includes api_key. Providers are shared
        # catalogue data (see rule_ai_model_shared), so there is nothing
        # per-user to leak by widening the read.
        provs = request.env["digi_erp.model.llm.provider"].sudo().search(
            [("active", "=", True)])
        return {
            "ok": True,
            "providers": [p.to_public_dict() for p in provs],
        }

    @http.route('/ai-solution/companies', type='json', auth='ai_user',
                methods=['POST'], csrf=False)
    def list_companies(self, **kwargs):
        """Return the companies the current user is allowed into, for the chat's
        company selector. `current` marks the ones active by default (the whole
        allowed set — the assistant defaults to spanning them all). `default`
        marks the user's main company. Only the user's OWN allowed companies are
        ever returned, so the selector can never grant access they don't have."""
        user = request.env.user
        allowed = user.company_ids
        return {
            "ok": True,
            "multi_company": len(allowed) > 1,
            "default_id": user.company_id.id,
            "companies": [
                {"id": c.id, "name": c.name,
                 "default": c.id == user.company_id.id}
                for c in allowed
            ],
        }

    # ── Conversation persistante par utilisateur ─────────────────────────────

    def _get_conversation(self, conversation_id):
        """Récupère une conversation appartenant à l'utilisateur courant.
        Retourne None si l'id n'existe pas ou n'appartient pas à l'utilisateur."""
        if not conversation_id:
            return None
        try:
            cid = int(conversation_id)
        except (TypeError, ValueError):
            return None
        # ir.rule garantit déjà l'isolement, mais on est explicite.
        conv = request.env["digi_erp.model.chat.conversation"].search(
            [("id", "=", cid), ("user_id", "=", request.env.user.id)], limit=1,
        )
        return conv or None

    def _ensure_conversation(self, conversation_id, source="portal"):
        """Renvoie la conversation demandée, ou en crée une nouvelle.

        `source` tags a freshly-created conversation ('portal' for the
        full-page chat, 'embedded' for the sidebar). An existing conversation
        keeps whatever source it already has."""
        conv = self._get_conversation(conversation_id)
        if conv:
            return conv
        # `name` is deliberately omitted: the model default is a lambda that
        # translates "New conversation" into the creating user's language.
        # Passing a literal here would pin every thread to one language and
        # re-introduce the bug the `is_unnamed` flag exists to kill.
        return request.env["digi_erp.model.chat.conversation"].create({
            "source": source,
        })

    def _serialize_conversation(self, conv):
        return {
            "id": conv.id,
            "name": conv.name,
            "last_message_at": conv.last_message_at and conv.last_message_at.isoformat(),
            "message_count": conv.message_count,
        }

    def _serialize_message(self, msg):
        out = {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "tool_name": msg.tool_name,
            "attachment_filename": msg.attachment_filename,
            "create_date": msg.create_date and msg.create_date.isoformat(),
            "model_label": msg.model_label or "",
        }
        # Activity receipt (assistant messages only) — parsed back so the UI
        # can re-render the "what I did" chip after a reload.
        if msg.activity_json:
            try:
                out["activity"] = json.loads(msg.activity_json)
            except Exception:
                pass
        if msg.attachment_id:
            att = msg.attachment_id
            out["attachment_id"] = att.id
            out["attachment_mimetype"] = att.mimetype or ""
            out["attachment_size"] = att.file_size or 0
            out["attachment_url"] = f"/web/content/{att.id}"
            out["attachment_download_url"] = (
                f"/web/content/{att.id}?download=1"
            )
        return out

    def _load_history(self, conv):
        """Charge un historique borné (par nb messages et par caractères) à
        passer au modèle pour qu'il ait de la mémoire.

        IMPORTANT: only `user` and `assistant` messages are loaded —
        `tool_call` / `tool_result` are dropped here, NOT later. They are
        never replayed to the model (see `_history_to_messages`), so
        counting their (often huge — e.g. a full employee-search result)
        content against HISTORY_CHARS_LIMIT used to silently evict the
        real conversation: the assistant recap holding the data got
        truncated out, and follow-ups like "make a PDF of this data"
        ended up with no data → hallucinated/fake rows. Budgeting only
        what we actually send keeps the data-bearing recaps in context
        far longer."""
        msgs = conv.message_ids.sorted("id", reverse=True)
        # Keep only the roles the model will actually receive, most
        # recent first, capped by message count.
        kept = []
        for m in msgs:
            if m.role not in ("user", "assistant"):
                continue
            kept.append(m)
            if len(kept) >= HISTORY_MESSAGES_LIMIT:
                break
        msgs = list(reversed(kept))  # back to chronological order
        history, total = [], 0
        for m in msgs:
            entry = {"role": m.role, "content": m.content or "", "tool_name": m.tool_name}
            total += len(entry["content"])
            history.append(entry)
            if total > HISTORY_CHARS_LIMIT:
                # On tronque par la tête : on retire les plus anciens.
                while total > HISTORY_CHARS_LIMIT and len(history) > 1:
                    dropped = history.pop(0)
                    total -= len(dropped["content"])
                break
        return history

    def _finalize_result(self, result, prompt=""):
        """Enrich a successful run result with the UX extras the frontend
        renders: the activity receipt and follow-up suggestion chips. Both are
        zero-cost (derived from the tool calls already executed). No-op on
        failure results so error payloads stay clean."""
        if isinstance(result, dict) and result.get("ok"):
            tool_calls = result.get("tool_calls") or []
            result["activity"] = _summarize_tool_calls(tool_calls)
            result["suggestions"] = _suggest_followups(tool_calls, prompt)
        return result

    def _persist_assistant_turn(self, conv, run_result):
        """Persiste les appels d'outils intermédiaires + la réponse finale.
        On reste en env utilisateur (pas de sudo) pour que user_id soit correct
        via le champ related conversation_id.user_id."""
        Msg = request.env["digi_erp.model.chat.message"]
        for call in run_result.get("tool_calls") or []:
            Msg.create([
                {
                    "conversation_id": conv.id,
                    "role": "tool_call",
                    "tool_name": call.get("name"),
                    "content": json.dumps(
                        {"tool": call.get("name"), "params": call.get("params") or {}},
                        ensure_ascii=False,
                    ),
                },
                {
                    "conversation_id": conv.id,
                    "role": "tool_result",
                    "tool_name": call.get("name"),
                    "content": _truncate_json(call.get("result"), limit=MAX_TOOL_RESULT_LEN),
                },
            ])
        if run_result.get("ok"):
            # Persist the compact activity receipt alongside the answer so the
            # "what I did" chip survives a page reload. Computed here if the
            # endpoint didn't already attach it. Best-effort JSON.
            activity = run_result.get("activity")
            if activity is None:
                activity = _summarize_tool_calls(run_result.get("tool_calls"))
            try:
                activity_json = json.dumps(activity, ensure_ascii=False) if activity else False
            except Exception:
                activity_json = False
            Msg.create({
                "conversation_id": conv.id,
                "role": "assistant",
                "content": run_result.get("response") or "",
                "activity_json": activity_json,
                # Which model wrote this — so the "answered by" line survives
                # a reload just like the activity receipt does.
                "model_label": run_result.get("model_label") or False,
            })

    # ── Conversations : list / new / rename / delete / messages ──────────────

    @http.route('/ai-solution/conversations', type='json', auth='ai_user', methods=['POST'], csrf=False)
    def list_conversations(self, **kwargs):
        convs = request.env["digi_erp.model.chat.conversation"].search([])
        return {"ok": True, "conversations": [self._serialize_conversation(c) for c in convs]}

    @http.route('/ai-solution/conversations/new', type='json', auth='ai_user', methods=['POST'], csrf=False)
    def new_conversation(self, name=None, **kwargs):
        # A caller-supplied name is a real title, so the thread is already
        # named and must not be overwritten by the first-message autonamer.
        name = (name or "").strip()
        vals = {"name": name, "is_unnamed": False} if name else {}
        conv = request.env["digi_erp.model.chat.conversation"].create(vals)
        return {"ok": True, "conversation": self._serialize_conversation(conv)}

    @http.route('/ai-solution/conversations/<int:conversation_id>/rename',
                type='json', auth='ai_user', methods=['POST'], csrf=False)
    def rename_conversation(self, conversation_id, name='', **kwargs):
        conv = self._get_conversation(conversation_id)
        if not conv:
            return {"ok": False, "error": _("Conversation not found.")}
        new_name = (name or "").strip()
        if new_name:
            conv.write({"name": new_name, "is_unnamed": False})
        return {"ok": True, "conversation": self._serialize_conversation(conv)}

    @http.route('/ai-solution/conversations/<int:conversation_id>/autotitle',
                type='json', auth='ai_user', methods=['POST'], csrf=False)
    def autotitle_conversation(self, conversation_id, **kwargs):
        """Generate a concise, human-readable title from the first exchange.

        Called by the client AFTER the first answer is rendered, so it never
        blocks the reply. One cheap LLM call on the lightweight tier (local
        the default local model if no lightweight one is configured); on ANY failure we
        keep the existing (truncated-prompt) name. This is what gives the
        sidebar its "ChatGPT settles a smart title a second later" feel."""
        conv = self._get_conversation(conversation_id)
        if not conv:
            return {"ok": False, "error": _("Conversation not found.")}
        # Only auto-title once, and only from a real first exchange.
        first_user = conv.message_ids.filtered(lambda m: m.role == "user")[:1]
        first_bot = conv.message_ids.filtered(lambda m: m.role == "assistant")[:1]
        if not first_user or not first_bot:
            return {"ok": True, "conversation": self._serialize_conversation(conv)}

        q = (first_user.content or "").strip()[:600]
        a = (first_bot.content or "").strip()[:600]
        prov = _pick_lightweight_provider(request.env)
        provider_id = prov.id if prov else None
        messages = [
            {"role": "system", "content": (
                "You name chat conversations. Given the first user message and "
                "the assistant's reply, output ONLY a short title of 3 to 6 "
                "words — no quotes, no punctuation at the end, no prefix like "
                "'Title:'. Use the SAME language as the user."
            )},
            {"role": "user", "content": f"USER:\n{q}\n\nASSISTANT:\n{a}\n\nTitle:"},
        ]
        try:
            rpc = _call_chat(request.env, provider_id, messages,
                             tools=None, conv=conv)
            title = ""
            if rpc.get("ok"):
                title = ((rpc.get("message") or {}).get("content") or "").strip()
            # Sanitise: single line, strip surrounding quotes, cap length.
            title = title.splitlines()[0].strip().strip('"\'“”«»').strip() if title else ""
            if title:
                if len(title) > 60:
                    title = title[:60].rstrip() + "…"
                conv.write({"name": title, "is_unnamed": False})
                _cprint("green", "AUTOTITLE", f"conv#{conv.id} → {title!r}")
        except Exception:
            _logger.exception("DIGI-ERP AI: autotitle failed for conv#%s",
                              conv.id)
        return {"ok": True, "conversation": self._serialize_conversation(conv)}

    @http.route('/ai-solution/conversations/<int:conversation_id>/delete',
                type='json', auth='ai_user', methods=['POST'], csrf=False)
    def delete_conversation(self, conversation_id, **kwargs):
        conv = self._get_conversation(conversation_id)
        if not conv:
            return {"ok": False, "error": _("Conversation not found.")}
        conv.unlink()
        return {"ok": True}

    @http.route('/ai-solution/conversations/<int:conversation_id>/messages',
                type='json', auth='ai_user', methods=['POST'], csrf=False)
    def conversation_messages(self, conversation_id, **kwargs):
        conv = self._get_conversation(conversation_id)
        if not conv:
            return {"ok": False, "error": _("Conversation not found.")}
        return {
            "ok": True,
            "conversation": self._serialize_conversation(conv),
            "messages": [self._serialize_message(m) for m in conv.message_ids],
        }

    # ── Endpoint JSON : prompt + boucle d'outils + persistance ───────────────
    @http.route('/ai-solution/ask', type='json', auth='ai_user', methods=['POST'], csrf=False)
    def ai_ask(self, prompt='', conversation_id=None, use_tools=True,
                  provider_id=None, company_ids=None, **kwargs):
        prompt = (prompt or '').strip()
        if not prompt:
            return {'ok': False, 'error': 'Prompt vide.'}

        # Multi-company: run the tool loop under the user's SELECTED companies
        # (validated against their allowed set). Defaults to all allowed.
        scoped_env, companies = _company_scoped_env(request.env, company_ids)

        _cprint("magenta", "REQUEST /ask",
                f"uid={request.env.uid}  conv={conversation_id}  "
                f"use_tools={use_tools}  provider={provider_id or 'local'}  "
                f"companies={companies.ids}  "
                f"prompt={prompt[:80]!r}")

        conv = self._ensure_conversation(conversation_id)
        _cprint("blue", "CONVERSATION", f"id={conv.id}  name={conv.name!r}")

        # Cancellation: clear any stale Stop note for this conversation, then
        # build a fresh-read check the tool loop / stream poll to abort
        # the moment the user clicks Stop (which fires POST /ai-solution/cancel
        # from a different worker). See models/cancel.py for the mechanism.
        cancel_model = request.env["digi_erp.model.chat.cancel"].sudo()
        cancel_model.clear(conv.id)
        cancel_check = lambda: cancel_model.is_cancelled(conv.id)

        # 1. Persiste le message utilisateur AVANT l'appel modèle. On garde
        #    une référence pour pouvoir le SUPPRIMER si le LLM échoue : un
        #    message orphelin (sans réponse) reviendrait sinon dans l'historique
        #    du prochain tour et polluerait le contexte, et il resterait visible
        #    après un refresh côté UI.
        user_msg = request.env["digi_erp.model.chat.message"].create({
            "conversation_id": conv.id,
            "role": "user",
            "content": prompt,
        })

        # 2. Charge l'historique (sans le message courant — on le passe explicite).
        history = self._load_history(conv)
        # Le dernier élément ajouté est le message de l'utilisateur courant.
        # _run_with_tools attend le prompt courant à part, donc on retire le tail.
        if history and history[-1]["role"] == "user" and history[-1]["content"] == prompt:
            history = history[:-1]
        _cprint("blue", "HISTORY", f"{len(history)} messages loaded")
        # First real exchange in this thread → the client should fetch a smart
        # auto-title afterwards (the create() hook only set a truncated one).
        is_first_exchange = not history

        # 3. Boucle d'outils (env scopé aux sociétés sélectionnées).
        if use_tools:
            result = _run_with_tools(scoped_env, prompt, history=history,
                                     provider_id=provider_id, conv=conv,
                                     companies=companies,
                                     cancel_check=cancel_check)
        else:
            # No-tools path: simple chat completion through the chosen provider.
            messages = [{"role": "user", "content": prompt}]
            rpc = _call_chat(request.env, provider_id, messages,
                             tools=None, conv=conv,
                             cancel_check=cancel_check)
            if rpc.get("ok"):
                raw = (rpc["message"].get("content") or "").strip()
                if raw:
                    result = {
                        "ok": True,
                        "response": raw,
                        "model": "remote" if provider_id else _cfg()["chat_model"],
                        "model_label": rpc.get("model_label") or "",
                        "tool_calls": [],
                    }
                else:
                    # Same empty-response symptom as the tools path:
                    # surface a meaningful diagnostic instead of letting
                    # the JS render "(réponse vide)".
                    result = {
                        "ok": False,
                        "error": _diagnose_empty_response(
                            request.env, provider_id, [], rpc.get("message"), 1,
                        ),
                        "tool_calls": [],
                    }
            else:
                result = {**rpc, "tool_calls": []}

        # The turn is over (normally, on error, or cancelled): remove the
        # Stop mailbox note so it can't leak into the NEXT turn on this
        # conversation. Safe to call unconditionally.
        cancel_model.clear(conv.id)

        # Cancelled by the user: drop the orphan user message (like any
        # non-ok turn) and return a clean, non-error signal so the client
        # shows "interrupted" rather than a red error bubble.
        if result.get("cancelled"):
            try:
                user_msg.unlink()
            except Exception:
                pass
            _cprint("yellow", "REQUEST /ask CANCELLED", f"conv={conv.id}")
            return {
                "ok": False,
                "cancelled": True,
                "response": "",
                "conversation_id": conv.id,
                "conversation_name": conv.name,
            }

        # 4. Si le LLM a échoué, on supprime carrément le message user pour
        #    qu'il disparaisse de la conversation au prochain refresh (et donc
        #    qu'il ne pollue plus jamais l'historique). On le fait AVANT de
        #    persister la sortie : si c'est un échec, il n'y a de toute façon
        #    pas de réponse assistant à enregistrer.
        if not result.get("ok"):
            user_msg.unlink()
        elif result.get("pending_action"):
            # A write is awaiting user confirmation — nothing was written yet.
            # Persist the assistant's INTRO text so the thread shows what it
            # proposed, but do NOT record any tool result (there is none). The
            # confirmation card itself is offered live; the actual result is
            # persisted later by /ai-solution/confirm-action.
            self._finalize_result(result, prompt)
            request.env["digi_erp.model.chat.message"].create({
                "conversation_id": conv.id,
                "role": "assistant",
                "content": result.get("response") or "",
            })
        else:
            self._finalize_result(result, prompt)
            self._persist_assistant_turn(conv, result)

        ok_label = "green" if result.get("ok") else "red"
        _cprint(ok_label, "REQUEST /ask DONE", f"ok={result.get('ok')}  tool_calls={len(result.get('tool_calls') or [])}")

        result["conversation_id"] = conv.id
        result["conversation_name"] = conv.name
        # Tell the client whether a smart auto-title is worth fetching: only on
        # the first successful exchange (the create() hook set just a truncated
        # placeholder name from the raw prompt).
        result["needs_title"] = bool(result.get("ok") and is_first_exchange)
        return result

    @http.route('/ai-solution/cancel', type='json', auth='ai_user',
                methods=['POST'], csrf=False)
    def ai_cancel(self, conversation_id=None, **kwargs):
        """Stop button: flag the running turn for this conversation so the
        busy worker aborts the model. Handled by a DIFFERENT worker than the
        one running /ask, so this only writes+commits the cancel note; the
        running turn polls it (see models/cancel.py) and hangs up on the server.

        Guarded to the caller's own conversation so a user can't cancel
        someone else's turn."""
        if not conversation_id:
            return {"ok": False, "error": "conversation_id requis."}
        conv = self._get_conversation(conversation_id)
        if not conv:
            return {"ok": False, "error": _("Conversation not found.")}
        request.env["digi_erp.model.chat.cancel"].sudo().request_cancel(
            conv.id, request.env.uid)
        _cprint("yellow", "REQUEST /cancel", f"conv={conv.id} uid={request.env.uid}")
        return {"ok": True, "cancelled": True}

    @http.route('/ai-solution/confirm-action', type='json', auth='ai_user',
                methods=['POST'], csrf=False)
    def confirm_action(self, tool=None, params=None, conversation_id=None,
                       confirm=True, company_ids=None, **kwargs):
        """Execute (or discard) a write action the user has reviewed.

        The chat loop pauses on any create/write/state-change and returns a
        `pending_action` = {tool, params, preview}. The client shows a
        before→after card; on Confirm it POSTs the SAME {tool, params} here.

        Security: the call is re-dispatched through the normal tool gateway
        under the CURRENT user's env, so Odoo ACLs / record rules re-apply.
        The client cannot smuggle a forbidden method — `unlink`/`execute*`
        are still blocked in the dispatcher, and we re-assert the write
        classification here so this endpoint can ONLY run genuine writes.
        """
        # Cancel path: user declined. Nothing to execute; record the choice
        # in the thread so the conversation stays truthful.
        conv = self._ensure_conversation(conversation_id)
        if not confirm:
            request.env["digi_erp.model.chat.message"].create({
                "conversation_id": conv.id,
                "role": "assistant",
                "content": "❌ " + _("Action cancelled. No data was changed."),
            })
            return {"ok": True, "cancelled": True,
                    "response": _("Action cancelled. No data was changed."),
                    "conversation_id": conv.id}

        tool = (tool or "").strip()
        params = params or {}
        if not isinstance(params, dict):
            return {"ok": False, "error": _("Invalid action parameters.")}
        # Hard gate: this endpoint executes ONLY confirmed writes. Anything
        # that isn't classified as a write is refused (a read never needs
        # confirmation and must not be laundered through here).
        if not is_write_call(tool, params):
            return {"ok": False,
                    "error": _("This action is not a change awaiting confirmation.")}

        scoped_env, _companies = _company_scoped_env(request.env, company_ids)
        _cprint("magenta", "CONFIRM-ACTION",
                f"uid={request.env.uid} exec {params.get('model')}."
                f"{params.get('method')} confirmed by user")

        result = tool_dispatch(scoped_env, tool, params)
        failed = isinstance(result, dict) and result.get("error")

        # Persist the tool call + its result so the thread reflects what
        # actually happened after confirmation.
        Msg = request.env["digi_erp.model.chat.message"]
        Msg.create([
            {
                "conversation_id": conv.id,
                "role": "tool_call",
                "tool_name": tool,
                "content": json.dumps({"tool": tool, "params": params},
                                      ensure_ascii=False),
            },
            {
                "conversation_id": conv.id,
                "role": "tool_result",
                "tool_name": tool,
                "content": _truncate_json(result, limit=MAX_TOOL_RESULT_LEN),
            },
        ])

        if failed:
            msg = "❌ " + _("The action failed: %s", result.get("error"))
            Msg.create({"conversation_id": conv.id, "role": "assistant",
                        "content": msg})
            return {"ok": False, "error": result.get("error"),
                    "conversation_id": conv.id}

        done = "✅ " + _("Done. The action was carried out.")
        Msg.create({"conversation_id": conv.id, "role": "assistant",
                    "content": done})
        return {"ok": True, "response": done, "result": result,
                "conversation_id": conv.id}

    # ── Embedded context-aware sidebar ───────────────────────────────────────
    #
    # Same engine as /ask, but the request carries the SCREEN CONTEXT the user
    # is looking at (model, record id, view type, active ids, company). We turn
    # that into a compact reference block appended to the system prompt so the
    # assistant already knows "you are on sale.order #42, form view" without the
    # user spelling it out. Field VALUES are NOT trusted from the client — the
    # assistant fetches them with its tools under the user's own ACL.

    def _build_screen_context_block(self, ctx):
        """Turn the client-supplied screen context dict into a short reference
        block for the system prompt. Defensive: every field is optional and
        validated; unknown models are dropped (never trust the client to name
        a real model — the tools re-check ACL anyway)."""
        if not isinstance(ctx, dict):
            return ""
        env = request.env
        model = (ctx.get("model") or "").strip()
        # Only accept a model the registry actually knows; otherwise ignore it.
        if model and model not in env:
            model = ""
        view_type = (ctx.get("view_type") or "").strip()[:20]
        res_id = ctx.get("res_id")
        try:
            res_id = int(res_id) if res_id not in (None, "", False, "new") else None
        except (TypeError, ValueError):
            res_id = None
        active_ids = ctx.get("active_ids") or []
        if not isinstance(active_ids, (list, tuple)):
            active_ids = []
        active_ids = [int(i) for i in active_ids
                      if isinstance(i, (int, str)) and str(i).isdigit()][:50]

        # Terse on purpose: the "speak business, never echo a technical name"
        # rule already opens the system prompt, so it is not repeated here.
        lines = ["=== CURRENT SCREEN (resolve \"this\", \"here\", \"the current "
                 "record\" against it; fetch real values with your tools, never "
                 "invent them) ==="]
        lines.append(f"User: {env.user.name}. Company: {env.company.name}.")
        if model:
            label = env["ir.model"]._get(model).name or model
            lines.append(
                f"Screen: \"{label}\" — say that label to the user; the "
                f"technical model {model} is for your tool calls only.")
            if res_id:
                lines.append(
                    f"Viewing ONE record, id={res_id} ({view_type or 'form'} "
                    f"view). \"this\" / \"the current …\" means it — act on "
                    f"id={res_id} directly.")
            elif active_ids:
                lines.append(
                    f"Viewing a selection of {len(active_ids)} record(s), "
                    f"ids={active_ids}. \"these\" / \"the selected\" means them.")
            else:
                lines.append(
                    f"Viewing a {view_type or 'list'}, no record selected. "
                    f"\"these records\" means the whole model.")
        else:
            lines.append("No specific screen in focus.")
        lines.append("=== END SCREEN ===")
        return "\n".join(lines)

    @http.route('/ai-solution/ask-context', type='json', auth='ai_user',
                methods=['POST'], csrf=False)
    def ai_ask_context(self, prompt='', conversation_id=None,
                          provider_id=None, screen_context=None,
                          company_ids=None, **kwargs):
        """Embedded-sidebar ask. Identical persistence/tool-loop to /ask, plus
        the current-screen context injected into the system prompt."""
        prompt = (prompt or '').strip()
        if not prompt:
            return {'ok': False, 'error': 'Prompt vide.'}

        # Multi-company: prefer an explicit selection; otherwise inherit the
        # companies the user has active on the screen (the backend web client
        # sends allowed_company_ids in the screen context).
        sctx = screen_context or {}
        req_companies = company_ids
        if req_companies in (None, "", []):
            req_companies = (sctx.get("allowed_company_ids")
                             or sctx.get("company_ids"))
        scoped_env, companies = _company_scoped_env(request.env, req_companies)

        ctx_block = self._build_screen_context_block(sctx)
        _cprint("magenta", "REQUEST /ask-context",
                f"uid={request.env.uid}  conv={conversation_id}  "
                f"model={(screen_context or {}).get('model')}  "
                f"res_id={(screen_context or {}).get('res_id')}  "
                f"prompt={prompt[:70]!r}")

        conv = self._ensure_conversation(conversation_id, source="embedded")

        user_msg = request.env["digi_erp.model.chat.message"].create({
            "conversation_id": conv.id,
            "role": "user",
            "content": prompt,
        })

        history = self._load_history(conv)
        if history and history[-1]["role"] == "user" and history[-1]["content"] == prompt:
            history = history[:-1]
        is_first_exchange = not history

        result = _run_with_tools(scoped_env, prompt, history=history,
                                 extra_context=ctx_block,
                                 provider_id=provider_id, conv=conv,
                                 companies=companies)

        if not result.get("ok"):
            user_msg.unlink()
        elif result.get("pending_action"):
            # Write awaiting confirmation — persist only the intro, no result.
            self._finalize_result(result, prompt)
            request.env["digi_erp.model.chat.message"].create({
                "conversation_id": conv.id,
                "role": "assistant",
                "content": result.get("response") or "",
            })
        else:
            self._finalize_result(result, prompt)
            self._persist_assistant_turn(conv, result)

        ok_label = "green" if result.get("ok") else "red"
        _cprint(ok_label, "REQUEST /ask-context DONE",
                f"ok={result.get('ok')}  tool_calls={len(result.get('tool_calls') or [])}")

        result["conversation_id"] = conv.id
        result["conversation_name"] = conv.name
        result["needs_title"] = bool(result.get("ok") and is_first_exchange)
        return result

    @http.route('/ai-solution/embedded/conversations', type='json',
                auth='ai_user', methods=['POST'], csrf=False)
    def embedded_list_conversations(self, **kwargs):
        convs = request.env["digi_erp.model.chat.conversation"].search(
            [("source", "=", "embedded")])
        return {"ok": True,
                "conversations": [self._serialize_conversation(c) for c in convs]}

    @http.route('/ai-solution/embedded/conversations/new', type='json',
                auth='ai_user', methods=['POST'], csrf=False)
    def embedded_new_conversation(self, name=None, **kwargs):
        name = (name or "").strip()
        vals = {"source": "embedded"}
        if name:
            vals.update(name=name, is_unnamed=False)
        conv = request.env["digi_erp.model.chat.conversation"].create(vals)
        return {"ok": True, "conversation": self._serialize_conversation(conv)}

    # ── Endpoint multipart : prompt + document + persistance ─────────────────
    # CSRF stays ON here, unlike the type='json' routes above. This is the only
    # type='http' POST in the module, so it is the only one Odoo's
    # HttpDispatcher actually checks — and it accepts a file upload that
    # starts a full AI turn, i.e. exactly what a cross-site POST would want.
    # chat.js puts `odoo.csrf_token` in the FormData to match.
    @http.route('/ai-solution/ask-with-file', type='http', auth='ai_user',
                methods=['POST'])
    def ai_ask_with_file(self, prompt='', conversation_id=None,
                            provider_id=None, company_ids=None, **kwargs):
        def _json(payload, status=200):
            return Response(json.dumps(payload), status=status,
                            content_type='application/json; charset=utf-8')

        # Top-level safety net: any uncaught exception below must return JSON,
        # not Odoo's HTML 500 page. The chat JS does res.json() on the
        # response, so an HTML body produces the "Unexpected token '<'..."
        # error in the browser.
        try:
            return self._do_ask_with_file(prompt, conversation_id, provider_id,
                                          company_ids=company_ids)
        except Exception as e:
            _logger.exception("DIGI-ERP AI /ask-with-file crashed")
            return _json({
                "ok": False,
                "error": f"Server error: {type(e).__name__}: {e}",
            }, 500)

    def _do_ask_with_file(self, prompt, conversation_id, provider_id,
                          company_ids=None):
        """Implementation extracted so the route handler can wrap it in a
        single try/except and guarantee JSON responses on any error path."""
        def _json(payload, status=200):
            return Response(json.dumps(payload), status=status,
                            content_type='application/json; charset=utf-8')

        # Multi-company scope for the tool loop (validated against allowed).
        scoped_env, companies = _company_scoped_env(request.env, company_ids)

        prompt = (prompt or '').strip()
        upload = request.httprequest.files.get('file')
        _cprint("magenta", "REQUEST /ask-with-file", f"uid={request.env.uid}  file={getattr(upload, 'filename', None)}")

        if not upload or not upload.filename:
            return _json({'ok': False, 'error': _("No file received.")}, 400)

        blob = upload.read()
        _cprint("blue", "FILE UPLOAD", f"{upload.filename}  {len(blob)} bytes")
        if len(blob) > MAX_FILE_BYTES:
            _cprint("yellow", "FILE TOO LARGE", f"{len(blob)} bytes > {MAX_FILE_BYTES}")
            return _json({
                'ok': False,
                'error': f"Fichier trop volumineux (max {MAX_FILE_BYTES // (1024 * 1024)} Mo)."
            }, 413)

        # ── OCR strategy: dedicated model / single-call / classic chain ───
        # Settings decides how an uploaded image is read (see
        # resolve_ocr_strategy):
        #   • single_call → the chat model is vision-capable and no dedicated
        #     OCR model is set: send image + prompt to it in ONE call. It
        #     reads the image AND fulfils the request together — no separate
        #     OCR round-trip. This is the optimisation the user asked for.
        #   • dedicated / classic → fall through to the OCR chain below,
        #     which extracts text first; the chat model then answers on it.
        # We also keep the old behaviour of bypassing the chain when the
        # user asked a question ABOUT the image (vision Q&A) — that is now a
        # subset of single_call.
        from ..services.ai_config import resolve_ocr_strategy
        _name_lc = (upload.filename or "").lower()
        is_image_upload = _name_lc.endswith(IMAGE_EXTS)
        ocr_strategy = resolve_ocr_strategy(request.env, provider_id)
        use_single_call = (
            is_image_upload
            and ocr_strategy["mode"] == "single_call"
        )
        if use_single_call:
            _cprint("cyan", "IMAGE SINGLE-CALL",
                    f"{upload.filename} — vision chat model reads the image "
                    f"AND answers in one call (provider "
                    f"#{ocr_strategy['provider_id']}, OCR chain bypassed)")
            vision_answer, verr = _ocr_via_vision_model(
                upload.filename, blob,
                provider_id=ocr_strategy["provider_id"],
                instruction=prompt,
            )
            if vision_answer and not verr:
                # The vision model already answered the user's question.
                # Persist the turn and return — no tool loop needed.
                conv = self._ensure_conversation(conversation_id)
                user_msg = request.env["digi_erp.model.chat.message"].create({
                    "conversation_id": conv.id,
                    "role": "user",
                    "content": prompt or "Describe this image.",
                    "attachment_filename": upload.filename,
                })
                try:
                    import base64 as _b64v
                    attv = request.env["ir.attachment"].sudo().create({
                        "name":      upload.filename or "image",
                        "datas":     _b64v.b64encode(blob),
                        "res_model": "digi_erp.model.chat.message",
                        "res_id":    user_msg.id,
                        "mimetype":  (upload.mimetype or "image/png"),
                    })
                    user_msg.attachment_id = attv.id
                except Exception:
                    _logger.exception("DIGI-ERP AI: failed to persist image upload")
                self._persist_assistant_turn(conv, {
                    "ok": True, "response": vision_answer, "tool_calls": [],
                })
                # Name the model that actually looked at the image. In
                # single_call mode that is the chat selection by definition
                # (resolve_ocr_strategy only returns this mode when no
                # dedicated OCR model is set), so the picker and the "answered
                # by" badge agree instead of both reporting the local default.
                _vis_prov = (request.env["digi_erp.model.llm.provider"].sudo()
                             .browse(ocr_strategy["provider_id"]))
                return _json({
                    "ok": True,
                    "response": vision_answer,
                    "model": (_vis_prov.model_name if _vis_prov.exists()
                              else _cfg()["chat_model"]),
                    "model_label": _provider_label(
                        request.env, _vis_prov if _vis_prov.exists() else None),
                    "tool_calls": [],
                    "conversation_id": conv.id,
                    "conversation_name": conv.name,
                })
            # Vision call failed → fall through to the OCR chain so the
            # user still gets *something* (the text) rather than an error.
            _cprint("yellow", "IMAGE VISION FAIL",
                    f"{verr!r} — falling back to OCR chain")

        # Text-extraction path (dedicated OCR model, classic engine, or a
        # single-call fallthrough). The llm_vision step inside the chain
        # must target the OCR model the strategy chose — the dedicated OCR
        # provider when one is configured, else the chat provider. Falls
        # back to the chat provider_id when the strategy has none.
        ocr_provider_id = ocr_strategy.get("provider_id") or provider_id
        # In "dedicated" mode the admin explicitly picked an AI model to do
        # the OCR, so lead the chain with that vision model (the classic
        # engines stay behind it as fallbacks). Otherwise honour the
        # admin's primary-engine choice from Settings.
        force_ocr_chain = None
        if ocr_strategy["mode"] == "dedicated":
            force_ocr_chain = ("llm_vision", "paddleocr", "surya", "tesseract")
        text, err = _extract_text_from_file(upload.filename, blob,
                                            provider_id=ocr_provider_id,
                                            ocr_chain=force_ocr_chain)
        if err and not text:
            _cprint("red", "FILE EXTRACT ERR", err)
            return _json({'ok': False, 'error': err}, 400)
        _cprint("green", "FILE EXTRACTED", f"{len(text or '')} chars from {upload.filename}")

        if not prompt:
            prompt = "Summarise and analyse the attached document."

        conv = self._ensure_conversation(conversation_id)

        # Trace du message utilisateur (avec la mention du document joint).
        user_msg = request.env["digi_erp.model.chat.message"].create({
            "conversation_id": conv.id,
            "role": "user",
            "content": prompt,
            "attachment_filename": upload.filename,
        })

        # Save the actual uploaded file as an ir.attachment linked to the
        # message — lets the UI preview / download it both this turn and
        # any time the conversation is reloaded.
        try:
            import base64 as _b64
            att = request.env["ir.attachment"].sudo().create({
                "name":      upload.filename or "uploaded-file",
                "datas":     _b64.b64encode(blob),
                "res_model": "digi_erp.model.chat.message",
                "res_id":    user_msg.id,
                "mimetype":  (upload.mimetype or
                              "application/octet-stream"),
            })
            user_msg.attachment_id = att.id
        except Exception:
            # Failure to persist the file is non-fatal — extraction
            # already ran on the in-memory blob; the user just loses
            # the preview/download chip.
            _logger.exception(
                "DIGI-ERP AI: failed to persist chat upload as attachment")

        history = self._load_history(conv)
        if history and history[-1]["role"] == "user" and history[-1]["content"] == prompt:
            history = history[:-1]

        truncated_text = (text or '')[:30000]

        # ── Conversational document analysis ──────────────────────────────
        # The chat upload is for QUESTIONS about a file: "summarise this CV",
        # "what's this person's email", "is this invoice paid", etc. The AI
        # gets the doc text as context and answers naturally, with the same
        # Odoo tool-use loop available in case the user wants to cross-check
        # against the database ("find this candidate in HR").
        #
        # Structured record creation (mapping fields, line items, publishing
        # to account.move / sale.order / ...) lives in a separate flow:
        # the backend menu  DIGI-ERP AI → Document Imports. That UI uploads
        # the same file through doc.import, runs classify+extract+project,
        # and lets the user review before publishing. The chat does not
        # touch digi_erp.model.doc.import.
        # The OCR'd / extracted text goes INLINE into the prompt the
        # model receives — NOT into a separate system-context block.
        # Why: small models treat the user message as
        # the real instruction and half-ignore system context. If the
        # user typed "extract data from this image", a model that only
        # sees that in the user turn — with the text tucked away in
        # system context — replies "please provide the image". Putting
        # the text right next to the question removes any doubt.
        #
        # `prompt` (the clean original) is what we saved to the DB and
        # show in the conversation; `model_prompt` (original + inlined
        # text) is only what we feed the LLM this turn.
        if not (truncated_text or "").strip():
            model_prompt = (
                f"{prompt}\n\n"
                f"[SYSTEM NOTE: the backend could not extract any text "
                f"from the uploaded file '{upload.filename}' (likely a "
                f"scanned image with no OCR layer, or an unsupported "
                f"format). Tell the user honestly that the file's text "
                f"could not be read — do NOT invent or guess content.]"
            )
        else:
            # CRITICAL structure: our INSTRUCTIONS go BEFORE the document
            # block; the block between the BEGIN/END markers holds ONLY
            # the document text. Earlier versions mixed instructions and
            # text in one block, so when told to "reply with the text
            # verbatim" the model dutifully echoed our own scaffolding
            # (markers, OCR notes, the ⚠️ rules) back to the user.
            # `attachment_id_hint` is the source file's id — surfaced
            # here so the LLM can pass it to `modify_document` when
            # the user asks for a design-preserving edit. May be 0 if
            # persistence failed earlier; the tool refuses cleanly.
            attachment_id_hint = (
                user_msg.attachment_id.id if user_msg.attachment_id
                else 0
            )

            # For supported office formats (.docx/.xlsx/.pptx/.pdf)
            # also extract the MARKED representation: each editable
            # unit numbered `[1] …`, `[2] …`. The LLM keeps the same
            # markers in its reply when calling `modify_document`,
            # which then re-walks the source in identical order and
            # applies the swap — lossless design preservation.
            marked_block = ""
            try:
                from ..services import structured_doc as _sd
                if (attachment_id_hint
                        and _sd.supported_for_modify(upload.filename)):
                    marked_text = _sd.extract_marked(
                        upload.filename, blob, max_chars=24000,
                    )
                    if marked_text.strip():
                        marked_block = (
                            f"\n---\n"
                            f"MARKED VIEW (use this — and only this — "
                            f"when calling `modify_document`). Each "
                            f"`[N] …` line is one editable unit in the "
                            f"source file. To modify the file, call "
                            f"`modify_document(source_attachment_id="
                            f"{attachment_id_hint}, new_marked=...)` "
                            f"and put back EVERY `[N]` with the SAME "
                            f"number and the SAME order, replacing "
                            f"only the text after `]`. Do NOT add "
                            f"markdown (`###`, `**`) — the source "
                            f"already has the styling.\n"
                            f"===BEGIN MARKED===\n"
                            f"{marked_text}\n"
                            f"===END MARKED==="
                        )
            except Exception:
                # Non-fatal: marked view is a bonus. The plain text
                # block below still lets the model answer Q&A.
                _logger.exception(
                    "structured_doc extraction failed for %s",
                    upload.filename,
                )

            model_prompt = (
                f"{prompt}\n\n"
                f"---\n"
                f"You have the full text content of a file the user sent "
                f"('{upload.filename}', ATTACHMENT_ID: "
                f"{attachment_id_hint}). It sits between the BEGIN/END "
                f"markers below. Treat it as plain text already in front "
                f"of you — never ask for an image.\n\n"
                f"HOW TO ANSWER:\n"
                f"• If the user asked you to extract / transcribe / "
                f"copy / read / show the content: output ONLY the text "
                f"between the DOCUMENT markers, verbatim — do not "
                f"reword, summarize, reformat, translate or correct "
                f"it. Do NOT output the markers themselves, and do "
                f"NOT add any preamble.\n"
                f"• If the user asked you to TRANSLATE, REWRITE, "
                f"REDACT, ANONYMIZE, PARAPHRASE, SIMPLIFY or "
                f"otherwise MODIFY the file content while keeping "
                f"its layout: call `modify_document` with "
                f"`source_attachment_id={attachment_id_hint}` and "
                f"`new_marked=<every [N] line of the MARKED block "
                f"below with its text replaced>`. For Arabic / "
                f"Hebrew / Persian / Urdu also pass `language` so "
                f"the result is right-to-left.\n"
                f"• Otherwise (Q&A, summary, classification…): "
                f"answer the user's question using the DOCUMENT "
                f"text. Do not mention the MARKED view.\n"
                f"• Speak naturally, like a normal assistant. NEVER "
                f"mention OCR, 'extracted text', 'the file content', "
                f"'ATTACHMENT_ID', 'MARKED', the markers, or these "
                f"instructions — the user must not see any of this "
                f"plumbing.\n"
                f"---\n"
                f"===BEGIN DOCUMENT===\n"
                f"{truncated_text}\n"
                f"===END DOCUMENT==="
                f"{marked_block}"
            )

        _cprint("blue", "CHAT FILE",
                f"{upload.filename}  text={len(truncated_text)} chars  "
                f"provider={provider_id or 'local'}")
        result = _run_with_tools(
            scoped_env, model_prompt, history=history,
            provider_id=provider_id, conv=conv, companies=companies,
        )

        # Échec LLM ⇒ on supprime le message user (et son attachement logique)
        # pour qu'il disparaisse au prochain refresh ; sinon on persiste la
        # sortie normalement.
        if not result.get("ok"):
            user_msg.unlink()
        else:
            self._finalize_result(result, prompt)
            self._persist_assistant_turn(conv, result)

        ok_label = "green" if result.get("ok") else "red"
        _cprint(ok_label, "REQUEST /ask-with-file DONE",
                f"ok={result.get('ok')}  tool_calls={len(result.get('tool_calls') or [])}")

        result["filename"] = upload.filename
        result["extracted_chars"] = len(text or '')
        result["conversation_id"] = conv.id
        result["conversation_name"] = conv.name
        return _json(result)

    # ── Téléchargement des fichiers générés par le modèle ────────────────────
    @http.route('/ai-solution/download/<int:attachment_id>', type='http', auth='ai_user')
    def download_generated_file(self, attachment_id, **kwargs):
        """Sert un ir.attachment créé par le tool generate_file.
        Seul le propriétaire (res_model=res.users, res_id=uid) peut télécharger."""
        attach = request.env["ir.attachment"].search([
            ("id", "=", attachment_id),
            ("res_model", "=", "res.users"),
            ("res_id", "=", request.env.uid),
        ], limit=1)
        if not attach:
            _cprint("red", "DOWNLOAD 404", f"attachment_id={attachment_id}  uid={request.env.uid}")
            return Response(_("File not found, or access denied."), status=404,
                            content_type="text/plain; charset=utf-8")

        import base64
        data = base64.b64decode(attach.datas or b"")
        _cprint("green", "DOWNLOAD", f"attachment_id={attachment_id}  {attach.name}  {len(data)} bytes")
        return Response(
            data,
            status=200,
            content_type=attach.mimetype or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{attach.name}"',
                "Content-Length": str(len(data)),
            },
        )
