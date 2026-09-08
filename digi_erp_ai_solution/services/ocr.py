# -*- coding: utf-8 -*-
"""DIGI-ERP AI — OCR pipeline.

Layered approach:

    raw bytes
       │
       ▼
    preprocess(bytes)            ← PIL: grayscale, deskew, auto-contrast,
       │                            denoise, upscale if too small
       ▼
    primary engine (Tesseract)   ← fast, CPU, language-pack driven
       │
       │ (if text too short / error / not installed)
       ▼
    fallback engine (LLM-vision) ← slow, uses the user-selected vision model
       │
       ▼
    text → fed to the LLM extractor

Each engine is in its own try/except so a missing optional dependency
(tesseract, PIL, opencv) degrades gracefully instead of crashing the
upload.
"""

import io
import logging
import os

_logger = logging.getLogger(__name__)

# ── protobuf compatibility shim for paddlepaddle ─────────────────────────────
# paddlepaddle ships .proto files compiled against an OLD protobuf (3.x).
# A modern protobuf (4.x+/5.x+/7.x) C++ descriptor pool rejects them with:
#   "Couldn't build proto file into descriptor pool: Invalid default ..."
# Forcing protobuf's pure-Python implementation makes the descriptor pool
# lenient enough to load paddle's protos.
#
# IMPORTANT: this env var is only honoured if it's set BEFORE the very
# first `import google.protobuf` in the process. Setting it here works
# only if nothing else imported protobuf earlier. For a guaranteed fix,
# also set it at the service level — see the project README / the error
# message raised in _force_paddle_preimport().
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# ── oneDNN (MKL-DNN) compatibility shim for paddlepaddle 3.x ──────────────────
# paddlepaddle 3.x's new PIR executor + oneDNN CPU backend has a bug:
#   "(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support
#    [pir::ArrayAttribute<pir::DoubleAttribute>]  (... onednn_instruction.cc)"
# Disabling the oneDNN acceleration layer makes paddle fall back to its
# standard CPU kernels — slightly slower, but correct. For document OCR
# the difference is negligible next to the rest of the pipeline.
# paddle reads FLAGS_* env vars when the `paddle` module initialises, and
# ocr.py is imported well before paddle, so setting it here is reliable.
# paddle 3.x renamed MKL-DNN → oneDNN, so we set BOTH flag spellings.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")


# ── ANSI helpers (mirror the rest of the addon) ──────────────────────────────
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


# ── Image preprocessing ──────────────────────────────────────────────────────

def preprocess_image(blob, *, max_side=2200, min_side=900):
    """PIL-only preprocessing pipeline for noisy scans and photos.

    Returns re-encoded JPEG bytes. Safe to call on already-clean images —
    every step is reversible / idempotent enough to not hurt OCR quality
    on a digital screenshot.

    Pipeline:
      1. Decode → RGB → grayscale (OCR engines prefer single-channel).
      2. Deskew if the page is rotated more than ~1° (uses PIL's rotate
         + a coarse projection-based angle detection).
      3. Auto-contrast (PIL ImageOps.autocontrast) to stretch the
         histogram — fixes faded scans.
      4. Light sharpen (Unsharp mask) to recover fine glyph edges.
      5. Resize: cap longest edge at `max_side`, upscale to `min_side`
         if the input is too small for OCR.
      6. Re-encode as high-quality JPEG.

    Returns the original blob on any failure — preprocessing should
    never block OCR.
    """
    try:
        from PIL import Image, ImageOps, ImageFilter
    except ImportError:
        _cprint("yellow", "OCR PREPROC SKIP", "PIL not installed")
        return blob
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()  # force decode before any rotate/convert
        if img.mode not in ("RGB", "L"):
            # Palette images that carry transparency in a `transparency`
            # byte string cannot go straight to RGB — PIL warns and drops
            # the alpha, which on a product shot flattens a transparent
            # background to black and buries the text. Route those through
            # RGBA first, then composite onto white: OCR wants dark ink on
            # a light ground, not on whatever the alpha happened to hide.
            if img.mode == "P" and "transparency" in img.info:
                img = img.convert("RGBA")
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")

        # 1. Grayscale
        img = img.convert("L")

        # 2. Deskew (cheap projection-based angle detection)
        angle = _detect_skew_angle(img)
        if abs(angle) > 0.8:  # ignore noise under 1°
            img = img.rotate(angle, resample=Image.BICUBIC, expand=True,
                             fillcolor=255)
            _cprint("cyan", "OCR PREPROC", f"deskew {angle:+.1f}°")

        # 3. Auto-contrast (1% cutoff dampens speckles)
        img = ImageOps.autocontrast(img, cutoff=1)

        # 4. VERY light sharpen. A strong unsharp mask (percent≥120)
        #    helps blurry scans but ADDS edge artifacts on already-crisp
        #    text (digital screenshots, clean exports) — those artifacts
        #    make the OCR recogniser mis-read characters. percent=60 +
        #    threshold=4 only touches genuinely soft edges and leaves
        #    sharp text untouched.
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0,
                                                  percent=60,
                                                  threshold=4))

        # 5. Resize bounds
        w, h = img.size
        long_side = max(w, h)
        short_side = min(w, h)
        scale = 1.0
        if long_side > max_side:
            scale = max_side / float(long_side)
        elif short_side < min_side:
            scale = min_side / float(short_side)
        if abs(scale - 1.0) > 1e-3:
            img = img.resize((int(w * scale), int(h * scale)),
                              Image.LANCZOS)
            _cprint("cyan", "OCR PREPROC",
                    f"resize ×{scale:.2f} → {img.size}")

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92,
                                optimize=True)
        return out.getvalue()
    except Exception as e:
        _logger.warning("OCR preprocess failed (%s) — using raw bytes", e)
        return blob


def _detect_skew_angle(img):
    """Coarse skew detection via horizontal projection variance.

    Tries a handful of small angles around 0°, picks the one whose row-sum
    variance is highest — that's typically the right de-skew angle.
    Fast (a few resizes), no extra deps. Good enough for ±5° rotations
    which are the common case from a phone-camera scan."""
    try:
        from PIL import Image
        import statistics
    except ImportError:
        return 0.0
    try:
        # Downscale heavily for speed — we only need the angle.
        w, h = img.size
        scale = min(1.0, 600 / max(w, h))
        small = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)

        best_angle, best_score = 0.0, -1.0
        for angle in (-4, -2, -1, -0.5, 0, 0.5, 1, 2, 4):
            rot = small.rotate(angle, resample=Image.BILINEAR,
                                expand=False, fillcolor=255)
            px = list(rot.getdata())
            ww, hh = rot.size
            # Sum darkness per row, then variance — sharp text bands give
            # high variance when rows are well-aligned.
            row_sums = []
            for y in range(hh):
                row = px[y * ww:(y + 1) * ww]
                row_sums.append(sum(255 - p for p in row))
            score = statistics.pvariance(row_sums) if len(row_sums) > 1 else 0
            if score > best_score:
                best_score, best_angle = score, angle
        return best_angle
    except Exception:
        return 0.0


# ── PaddleOCR engine ─────────────────────────────────────────────────────────
#
# Best-in-class local OCR for printed multilingual text. Uses PP-OCRv4
# detection + recognition models (~200 MB, auto-downloaded on first use).
# Initialisation is expensive (~3 s + GPU/CPU warm-up), so we cache one
# instance per language code in a module-level dict.

_paddle_instances = {}

# Tracks whether we've already forced paddle's submodules to load. The
# import is fairly expensive (~1 s) so we only do it once per process.
_paddle_preimport_done = False


def _force_paddle_preimport():
    """Defeat the "partially initialized module 'paddle' has no attribute
    'tensor'" circular-import error.

    What it is: under Python 3.12 + paddlepaddle 3.3.x, PaddleOCR's init
    sometimes references `paddle.tensor` while paddle's own __init__ is
    still running. Python raises ImportError("partially initialized
    module …") because the submodule hasn't been registered yet on the
    parent package.

    The fix: pre-import paddle's submodules ourselves, IN ORDER, BEFORE
    PaddleOCR gets a chance to touch them. Once paddle is fully loaded
    in `sys.modules`, the circular reference inside PaddleOCR resolves
    cleanly.

    Idempotent — safe to call repeatedly; the expensive imports are
    cached by Python's import system after the first call."""
    global _paddle_preimport_done
    if _paddle_preimport_done:
        return
    import importlib
    # Order matters: import each leaf BEFORE anything that depends on it.
    # `paddle.framework` and `paddle.base` underpin `paddle.tensor`, so
    # they go first. Stop at the first ImportError — that indicates a
    # genuinely broken install (not the circular-import quirk), and the
    # caller's try/except will surface a clean error message.
    for modname in ("paddle",
                    "paddle.framework",
                    "paddle.base",
                    "paddle.tensor"):
        importlib.import_module(modname)
    _paddle_preimport_done = True


# Map common Tesseract codes to PaddleOCR's single-language codes.
# PaddleOCR can only target ONE primary language per instance; you'd
# need separate instances for English + Arabic + French. We pick the
# first code in the user's list.
_PADDLE_LANG_MAP = {
    "eng": "en",  "en":   "en",
    "fra": "fr",  "fr":   "fr",
    "ara": "arabic", "ar":   "arabic",
    "deu": "german", "de":   "german",
    "spa": "es",  "es":   "es",
    "ita": "it",  "it":   "it",
    "por": "pt",  "pt":   "pt",
    "rus": "ru",  "ru":   "ru",
    "chi_sim": "ch", "zh": "ch",
}


def _paddle_lang(languages):
    """Pick a single PaddleOCR language code from a Tesseract-style list."""
    first = (languages or "en").replace("+", ",").split(",")[0].strip().lower()
    return _PADDLE_LANG_MAP.get(first, first)


# Light per-script recognition models. PaddleOCR 3.x ships small "mobile"
# recognition models per script — a fraction of the RAM of the generic
# 'server' recogniser it loads by default. Forcing the right one keeps
# memory low (avoids MemoryError on small prod boxes) AND accuracy high.
_PADDLE_REC_MODEL = {
    "fr":     "latin_PP-OCRv5_mobile_rec",
    "en":     "latin_PP-OCRv5_mobile_rec",
    "es":     "latin_PP-OCRv5_mobile_rec",
    "it":     "latin_PP-OCRv5_mobile_rec",
    "pt":     "latin_PP-OCRv5_mobile_rec",
    "german": "latin_PP-OCRv5_mobile_rec",
    "arabic": "arabic_PP-OCRv5_mobile_rec",
    "ru":     "eslav_PP-OCRv5_mobile_rec",
    "ch":     "PP-OCRv5_mobile_rec",
}


def _paddle_rec_model(lang):
    """Light recognition model for `lang`. Defaults to the Latin mobile
    recogniser — most business documents are Latin-script."""
    return _PADDLE_REC_MODEL.get(lang, "latin_PP-OCRv5_mobile_rec")


def _get_paddle(lang):
    """Lazy-cached PaddleOCR instance for `lang`.

    PaddleOCR has had multiple breaking API changes across 2.x and 3.x:
      • 2.x:  `use_angle_cls=True, lang=…, show_log=False`
      • 3.0-3.4:  `use_textline_orientation=True, lang=…`
      • 3.5+: minimal init `lang=…`, orientation is on by default

    We try signatures in order of preference (newest first) and use the
    first one that doesn't raise. Anything but the simplest signature
    can fail with TypeError (unknown kwarg) OR ValueError (rejected
    value), so we catch both."""
    if lang not in _paddle_instances:
        # Silence paddle's noisy import-time warnings (ccache, etc.).
        # They land in py.warnings with full stack traces that pollute
        # the log every cold-start. Pure cosmetic — they don't affect OCR.
        import warnings as _w
        _w.filterwarnings("ignore", category=UserWarning,
                          module=r"paddle\..*")
        _w.filterwarnings("ignore", category=DeprecationWarning,
                          module=r"paddleocr\..*")

        # CRITICAL: pre-import paddle in full before PaddleOCR touches it.
        # Defeats the "partially initialized module 'paddle' has no
        # attribute 'tensor'" circular-import error on Python 3.12 +
        # paddlepaddle 3.3.x. See _force_paddle_preimport().
        try:
            _force_paddle_preimport()
        except ImportError as e:
            raise RuntimeError(
                f"paddlepaddle is installed but its modules can't be "
                f"loaded ({type(e).__name__}: {e}). The install is "
                f"likely incomplete. In the SAME Python that runs Odoo, "
                f"run:\n"
                f"    pip uninstall -y paddleocr paddlepaddle paddlex\n"
                f"    pip install --no-cache-dir paddlepaddle paddleocr\n"
                f"    python3 -c 'import paddle.tensor; print(\"OK\")'\n"
                f"The last command must print OK before restarting Odoo."
            )

        from paddleocr import PaddleOCR
        # Memory budget is the priority on a small prod box. PaddleOCR 3.x
        # by default loads FIVE models per instance:
        #   doc-orientation + doc-unwarping + textline-orientation
        #   + detection + recognition
        # and the recognition one defaults to the heavy 'server' variant.
        # Five models — several GB — OOMs a memory-constrained host.
        #
        # We strip it to the TWO models we actually need:
        #   • detection  → PP-OCRv5_mobile_det   (light)
        #   • recognition→ <script>_PP-OCRv5_mobile_rec (light, per-script)
        # and disable the three orientation/unwarp models — we ALREADY
        # deskew in our own preprocess_image(), so they're redundant.
        #
        # enable_mkldnn=False keeps paddle off the broken oneDNN PIR path.
        # If a PaddleOCR version rejects a kwarg the constructor raises
        # TypeError/ValueError and we fall through to the next attempt.
        rec_model = _paddle_rec_model(lang)
        light = {
            "text_detection_model_name":   "PP-OCRv5_mobile_det",
            "text_recognition_model_name": rec_model,
        }
        no_orient = {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping":            False,
            "use_textline_orientation":     False,
        }
        attempts = (
            # 3.x — 2 light models, no orientation models (LOWEST memory)
            {**light, **no_orient, "enable_mkldnn": False},
            # 3.x — light detection only + no orientation models
            {"text_detection_model_name": "PP-OCRv5_mobile_det",
             **no_orient, "enable_mkldnn": False},
            # 3.x — light detection, default recognition
            {"lang": lang, "enable_mkldnn": False,
             "text_detection_model_name": "PP-OCRv5_mobile_det"},
            # 3.x — defaults
            {"lang": lang, "enable_mkldnn": False},
            {"lang": lang},
            # 2.x
            {"lang": lang, "use_angle_cls": True,
             "show_log": False, "enable_mkldnn": False},
        )
        last_err = None
        for kwargs in attempts:
            try:
                _paddle_instances[lang] = PaddleOCR(**kwargs)
                last_err = None
                break
            except (TypeError, ValueError) as e:
                last_err = e
                continue
        if last_err is not None:
            raise last_err
    return _paddle_instances[lang]


def _poly_to_bbox(poly):
    """Convert a PaddleOCR polygon or box to an axis-aligned integer
    [x0, y0, x1, y1]. Accepts a 4-point polygon [[x,y],...], a flat
    [x0,y0,x1,y1], or a numpy array of either shape. Returns None on any
    malformed input — the caller just skips that box."""
    try:
        rows = list(poly)
        if rows and hasattr(rows[0], "__len__"):
            # [[x,y], [x,y], ...]  (polygon)
            xs = [float(p[0]) for p in rows]
            ys = [float(p[1]) for p in rows]
        else:
            # flat [x0, y0, x1, y1, ...]
            vals = [float(v) for v in rows]
            xs, ys = vals[0::2], vals[1::2]
        if not xs or not ys:
            return None
        return [round(min(xs), 1), round(min(ys), 1),
                round(max(xs), 1), round(max(ys), 1)]
    except Exception:
        return None


def _paddle_extract_polys(item, count):
    """Pull the per-line polygons/boxes out of a PaddleOCR result item.
    Returns a list aligned 1:1 with the `count` recognised texts, or None
    if no usable geometry is present."""
    polys = None
    if hasattr(item, "get"):
        polys = (item.get("rec_polys") or item.get("rec_boxes")
                 or item.get("dt_polys"))
    if polys is None:
        for attr in ("rec_polys", "rec_boxes", "dt_polys"):
            polys = getattr(item, attr, None)
            if polys is not None:
                break
    if polys is None:
        return None
    try:
        if len(polys) != count:
            return None
    except TypeError:
        return None
    return polys


def _run_paddleocr(blob, languages="fra+eng", layout_out=None):
    """PaddleOCR. Returns (text, error).

    If `layout_out` (a dict) is passed in, it is populated with the page
    geometry and the per-line bounding boxes:
        layout_out = {"width": W, "height": H,
                      "boxes": [{"text": str, "bbox": [x0,y0,x1,y1]}, ...]}
    Coordinates are in pixels of the (possibly downscaled) image PaddleOCR
    actually saw — `width`/`height` let the UI scale them to any render
    size. Used by the editable-PDF preview feature."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError as e:
        return None, f"PaddleOCR prerequisites missing: {e}"
    try:
        import paddleocr  # noqa: F401  — fail fast with a clear error
    except ImportError as e:
        # Distinguish "genuinely not installed" from "installed but its
        # import chain blew up" (e.g. a broken/forbidden cv2 .so). The
        # old code reported BOTH as "not installed", which sent people
        # reinstalling a package that was already there.
        msg = str(e)
        if "No module named 'paddleocr'" in msg or \
           "No module named \"paddleocr\"" in msg:
            return None, ("PaddleOCR not installed. Install with: "
                          "`pip install paddlepaddle paddleocr` "
                          "(or paddlepaddle-gpu for CUDA).")
        # paddleocr IS installed — a dependency failed to import. Surface
        # the REAL error so it's actionable.
        return None, (f"PaddleOCR is installed but failed to import — a "
                      f"dependency could not load: {type(e).__name__}: {e}")
    except Exception as e:
        # Non-ImportError failures during import (OSError from a bad .so,
        # RLIMIT_AS denying an mmap, etc.) — surface them verbatim too.
        return None, (f"PaddleOCR import failed: {type(e).__name__}: {e}")
    try:
        img = Image.open(io.BytesIO(blob))
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Cap the image so PaddleOCR's detection model stays within a
        # sane CPU-memory budget. Detection feature-map memory scales
        # with image AREA — a 3000×4000 scan can demand 3+ GB and OOM.
        # 2048 px is high enough to keep small UI / document text legible
        # to the recogniser (1600 px was shrinking 1080p screenshots
        # enough to garble small glyphs) while the MOBILE detection model
        # keeps the allocation well under 1 GB at that size.
        _PADDLE_MAX_SIDE = 2048
        w, h = img.size
        if max(w, h) > _PADDLE_MAX_SIDE:
            scale = _PADDLE_MAX_SIDE / float(max(w, h))
            img = img.resize((max(1, int(w * scale)),
                              max(1, int(h * scale))), Image.LANCZOS)
            _cprint("cyan", "OCR PADDLE RESIZE",
                    f"{w}×{h} → {img.size[0]}×{img.size[1]} "
                    f"(memory cap)")
        arr = np.array(img)
        # Page geometry the OCR actually saw — needed so the UI can scale
        # the boxes to whatever size it renders the PDF/image at.
        page_w, page_h = img.size

        lang = _paddle_lang(languages)
        ocr = _get_paddle(lang)

        # PaddleOCR 3.x prefers .predict(); 2.x uses .ocr(arr, cls=True).
        # Prefer the new API where available.
        if hasattr(ocr, "predict"):
            result = ocr.predict(arr)
        else:
            result = ocr.ocr(arr, cls=True)

        # Result format differs sharply between versions:
        #   2.x:  [[ [bbox, (text, conf)], ... ]]  (nested lists)
        #   3.x:  [ OCRResult, ... ]  where OCRResult is a DICT subclass;
        #         the recognised text is under the key 'rec_texts'.
        # The 3.x dict keys are NOT exposed as attributes, so we must use
        # item['rec_texts'] / item.get(...), not getattr().
        lines = []
        layout_boxes = []      # filled when layout_out was requested
        if not result:
            return None, None

        for item in result:
            texts = None

            # 3.x — OCRResult is dict-like. Try dict access first.
            if hasattr(item, "get"):
                texts = (item.get("rec_texts")
                         or item.get("texts")
                         or item.get("rec_text"))

            # Some builds expose them as attributes instead.
            if not texts:
                for attr in ("rec_texts", "texts", "rec_text"):
                    v = getattr(item, attr, None)
                    if v:
                        texts = v
                        break

            if texts:
                lines.extend(str(t) for t in texts if t)
                # Pair each recognised text with its bounding box for the
                # editable-PDF preview (only when the caller asked).
                if layout_out is not None:
                    polys = _paddle_extract_polys(item, len(texts))
                    if polys is not None:
                        for t, poly in zip(texts, polys):
                            bb = _poly_to_bbox(poly)
                            if bb and t:
                                layout_boxes.append(
                                    {"text": str(t), "bbox": bb})
                continue

            # 2.x — item is itself a list of [bbox, (text, conf)] rows.
            if isinstance(item, list):
                for entry in item:
                    if not entry or len(entry) < 2:
                        continue
                    txt_conf = entry[1]
                    if isinstance(txt_conf, (tuple, list)) and txt_conf:
                        lines.append(str(txt_conf[0]))

        text = "\n".join(lines).strip()

        # Hand the page geometry + boxes back to the caller (Phase 1 of
        # the editable-PDF feature). Always set the keys when a layout
        # was requested, even if no boxes were found.
        if layout_out is not None:
            layout_out["width"] = page_w
            layout_out["height"] = page_h
            layout_out["boxes"] = layout_boxes
            _cprint("cyan", "OCR PADDLE LAYOUT",
                    f"{len(layout_boxes)} boxes  page={page_w}×{page_h}")

        # Diagnostic: if we STILL got nothing, log the result structure
        # so the format can be inspected from the server log.
        if not text:
            sample = result[0] if result else None
            keys = (list(sample.keys())
                    if hasattr(sample, "keys") else "n/a")
            _cprint("yellow", "OCR PADDLE EMPTY",
                    f"parsed 0 lines  result_type={type(result).__name__}  "
                    f"item0_type={type(sample).__name__}  item0_keys={keys}")

        return (text or None), None
    except Exception as e:
        return None, f"PaddleOCR error: {e}"


# ── Surya engine ─────────────────────────────────────────────────────────────
#
# Strong layout detection on complex documents (multi-column, tables,
# mixed scripts). Larger model footprint (~1.3 GB) but no per-language
# config — handles 90+ languages in one go.

_surya_det = None  # detection predictor (cached singleton)
_surya_rec = None  # recognition predictor (cached singleton)


def _get_surya_predictors():
    """Lazy-load the Surya predictor singletons. Model files are downloaded
    to ~/.cache/datalab/ on first call (~1.3 GB)."""
    global _surya_det, _surya_rec
    if _surya_det is None or _surya_rec is None:
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor
        _surya_det = DetectionPredictor()
        _surya_rec = RecognitionPredictor()
    return _surya_det, _surya_rec


def _run_surya(blob, languages="fr,en"):
    """Surya OCR. Returns (text, error)."""
    try:
        from PIL import Image
    except ImportError as e:
        return None, f"Surya prerequisites missing: {e}"
    try:
        import surya  # noqa: F401
    except ImportError:
        return None, ("Surya not installed. Install with: "
                      "`pip install surya-ocr`.")
    try:
        img = Image.open(io.BytesIO(blob))
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Surya wants a list of ISO language codes per image (we pass one
        # image, so one list). Translate Tesseract '+' separators.
        langs = [lc.strip() for lc in
                 (languages or "en").replace("+", ",").split(",") if lc.strip()]

        det, rec = _get_surya_predictors()
        predictions = rec([img], [langs], det)

        # predictions: list of OCRResult, one per image. Each has
        # `text_lines` with .text on each line.
        lines = []
        for page in predictions:
            for line in getattr(page, "text_lines", []) or []:
                t = getattr(line, "text", "")
                if t:
                    lines.append(t)
        text = "\n".join(lines).strip()
        return (text or None), None
    except Exception as e:
        return None, f"Surya error: {e}"


# ── Tesseract engine ─────────────────────────────────────────────────────────

def _run_tesseract(blob, languages="eng+fra"):
    """Run Tesseract on `blob`. Returns (text, error_message).

    Languages: pass a `+`-joined list of Tesseract language codes. The
    user installs language packs separately (`apt install
    tesseract-ocr-fra tesseract-ocr-ara …`). 'eng+fra' is a sane default
    for a French/English mixed environment.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        return None, f"Tesseract dependencies not installed: {e}"
    try:
        img = Image.open(io.BytesIO(blob))
        # OCR config:
        #   --oem 3  → default LSTM + legacy combined engine
        #   --psm 6  → 'assume a single uniform block of text' — robust
        #              default for full pages and receipts. (PSM 3 is also
        #              valid but PSM 6 is more lenient on multi-column
        #              invoices.)
        text = pytesseract.image_to_string(
            img, lang=languages, config="--oem 3 --psm 6",
        )
        return (text or "").strip(), None
    except pytesseract.TesseractNotFoundError:
        return None, ("Tesseract binary not found. Install with: "
                      "`apt install tesseract-ocr tesseract-ocr-fra "
                      "tesseract-ocr-eng tesseract-ocr-ara`.")
    except Exception as e:
        return None, f"Tesseract error: {e}"


# ── Top-level entry point ────────────────────────────────────────────────────

# Engines registered for the fallback chain. Each is `(name, callable)`
# where `callable(blob, **opts) -> (text, error)`.
def _engine_paddleocr(blob, **opts):
    return _run_paddleocr(blob, languages=opts.get("languages", "fra+eng"),
                          layout_out=opts.get("layout_out"))


def _engine_surya(blob, **opts):
    return _run_surya(blob, languages=opts.get("languages", "fr,en"))


def _engine_tesseract(blob, **opts):
    return _run_tesseract(blob, languages=opts.get("languages", "eng+fra"))


def _engine_llm_vision(blob, **opts):
    """Lazy import the controller's existing LLM-vision OCR so we don't
    introduce a circular import at module load."""
    try:
        from ..controllers.main import _ocr_via_vision_model
    except ImportError as e:
        return None, f"LLM-vision OCR unavailable: {e}"
    text, err = _ocr_via_vision_model(
        opts.get("filename", "image"), blob,
        provider_id=opts.get("provider_id"),
    )
    return text, err


_ENGINES = {
    "paddleocr":  _engine_paddleocr,
    "surya":      _engine_surya,
    "tesseract":  _engine_tesseract,
    "llm_vision": _engine_llm_vision,
}


# Default chain: try the best-quality engine first, then a layout-aware
# second pass, then the LLM-vision catch-all. Each step degrades
# gracefully on a missing optional dep (the engine returns
# "<engine> not installed" and we fall through to the next one).
_DEFAULT_CHAIN = ("paddleocr", "surya", "llm_vision")


def ocr_image(blob, *,
              primary=None,
              fallback=None,
              chain=None,
              min_chars=20,
              preprocess=True,
              filename="image",
              provider_id=None,
              languages="fra+eng",
              return_layout=False):
    """Run the configured OCR chain on `blob`.

    Returns `(text, error)` by default. When `return_layout=True` returns
    `(text, error, layout)` where `layout` is
        {"width": W, "height": H,
         "boxes": [{"text": str, "bbox": [x0,y0,x1,y1]}, ...]}
    or None. Layout is only available when the PaddleOCR engine produced
    the winning result (it's the only box-aware engine); for any other
    winning engine `layout` is None.

    Args:
        blob: raw image bytes (JPEG/PNG/whatever PIL can open).
        primary, fallback: legacy two-engine API. If both are None, the
            multi-engine `chain` is used (recommended).
        chain: ordered tuple of engine names to try in sequence; first
            one returning >= `min_chars` wins. Defaults to
            ('paddleocr', 'surya', 'llm_vision').
        min_chars: heuristic threshold below which we treat an engine
            output as "failed" and fall through to the next one. 20 is
            conservative; pages with only a few words still pass.
        preprocess: apply the PIL preprocessing pipeline before OCR.
        filename: informational, used in log + LLM-vision filename.
        provider_id: passed to LLM-vision so a chat-dropdown selection
            drives that step too.
        languages: language list (e.g. 'fra+eng+ara'); each engine maps
            it to its own language config internally.
        return_layout: if True, also capture + return the box layout.
    """
    # Resolve the chain. Explicit primary/fallback args override
    # `chain`, which itself overrides the module default.
    if primary or fallback:
        steps = tuple(s for s in (primary, fallback) if s)
    elif chain:
        steps = tuple(chain)
    else:
        steps = _DEFAULT_CHAIN

    raw_len = len(blob)
    if preprocess:
        blob = preprocess_image(blob)
        _cprint("magenta", "OCR PIPELINE",
                f"{filename}  preproc: {raw_len} → {len(blob)} bytes  "
                f"chain={steps}")

    # When a layout is wanted, this dict is passed into each engine.
    # Only the PaddleOCR engine fills it. It's reset before every engine
    # attempt so a falling-through paddle result can't be mistaken for a
    # later engine's geometry.
    layout_box = {} if return_layout else None

    def _try(engine_name):
        fn = _ENGINES.get(engine_name)
        if not fn:
            return None, f"Unknown OCR engine: {engine_name!r}"
        if layout_box is not None:
            layout_box.clear()
        _cprint("blue", "OCR ENGINE TRY", f"{engine_name}")
        return fn(blob, filename=filename, provider_id=provider_id,
                  languages=languages, layout_out=layout_box)

    def _ret(text, error):
        """Shape the return value to match `return_layout`."""
        if not return_layout:
            return text, error
        lay = layout_box if (layout_box and layout_box.get("boxes")) else None
        return text, error, lay

    last_text, last_err = None, None
    for step in steps:
        text, err = _try(step)
        if text and len(text.strip()) >= min_chars:
            _cprint("green", "OCR ENGINE OK",
                    f"{step}  {len(text)} chars from {filename}")
            # Log a preview of the actual text so OCR QUALITY is visible
            # in the log — a high char-count of garbage still "passes"
            # the length check, so we surface the content to judge it.
            preview = " ".join(text.split())[:200]
            _cprint("cyan", "OCR TEXT PREVIEW", f"{preview!r}")
            # Layout only belongs to the WINNING engine. _try() cleared
            # layout_box before this step, so it now holds this engine's
            # geometry (paddle) or nothing (any other engine).
            return _ret(text, None)
        # Remember the best-so-far in case every engine in the chain
        # underperforms — at least return SOMETHING.
        if text and (not last_text or len(text) > len(last_text)):
            last_text, last_err = text, err
        elif err:
            last_err = err
        _cprint("yellow", "OCR ENGINE WEAK",
                f"{step} → {len(text or '')} chars (err: {err}); "
                f"trying next in chain")

    if last_text:
        # A weak best-effort result — its engine isn't known to be
        # paddle, so don't claim a layout for it.
        if layout_box is not None:
            layout_box.clear()
        return _ret(last_text, None)
    if layout_box is not None:
        layout_box.clear()
    return _ret(None, last_err or "OCR failed across the entire engine chain.")
