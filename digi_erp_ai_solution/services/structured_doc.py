# -*- coding: utf-8 -*-
"""Structured extraction + in-place modification for office documents.

Solves the "translate / rewrite / redact a file while keeping its
design" problem with a single protocol that works across formats.

Flow
----
  1. extract_marked(filename, blob)
       → str  — every editable text unit numbered: `[N] content`.
       The LLM sees this and modifies each `[N]` line freely (translate,
       rewrite, redact…), keeping the SAME N and the SAME number of
       lines.
  2. apply_marked(filename, source_blob, modified_text, language=None)
       → (bytes, mime, ext)  — re-walks the source in the SAME
       deterministic order, finds the N-th editable unit, replaces its
       text with the modified version. Original fonts, headings,
       tables, images, slides, sheets, etc. are preserved.

No state is carried between calls: the N → location mapping is
reconstructed from walk order. Same code, same order, same N → same
location. Stateless and bullet-proof.

Supported file types
--------------------
  .docx  — paragraphs (body + table cells + headers/footers)
  .xlsx  /.xlsm  — string-valued cells across all sheets
  .pptx  — paragraphs inside every shape that has a text_frame
  .pdf   — text-PDF paragraphs; apply rebuilds as DOCX (PDF in-place
           editing is too brittle for v1 — we keep the content + RTL
           layout, lose the original PDF design)

RTL languages (Arabic, Hebrew, Persian, Urdu) flip every touched
paragraph to bidi-RTL + right-aligned automatically when `language`
is provided.
"""
import io
import re
import logging

_logger = logging.getLogger(__name__)


# ── Public API ───────────────────────────────────────────────────────────


SUPPORTED_EXTENSIONS = (".docx", ".xlsx", ".xlsm", ".pptx", ".pdf")


def supported_for_modify(filename):
    """True if this file type can be round-tripped via the marker
    protocol (extract → LLM → apply)."""
    return _ext(filename) in SUPPORTED_EXTENSIONS


def extract_marked(filename, blob, max_chars=24000):
    """Build the marker-numbered representation of `blob`.

    Returns a string like:
        [1] Document Title
        [2] Welcome to our product.
        [3] Click here to start.

    `max_chars` caps the total length so very large documents don't
    blow up the LLM context window — extraction stops at the first
    marker that would exceed the cap and appends a truncation notice.
    """
    ext = _ext(filename)
    if ext == ".docx":
        return _extract_docx(blob, max_chars)
    if ext in (".xlsx", ".xlsm"):
        return _extract_xlsx(blob, max_chars)
    if ext == ".pptx":
        return _extract_pptx(blob, max_chars)
    if ext == ".pdf":
        return _extract_pdf(blob, max_chars)
    # Plain text fallback — one paragraph per non-blank line
    text = blob.decode("utf-8", errors="replace") if isinstance(blob, (bytes, bytearray)) else str(blob)
    return _build_from_lines(
        [ln for ln in text.split("\n") if ln.strip()], max_chars,
    )


def apply_marked(filename, source_blob, modified_text, language=None):
    """Apply `modified_text` (the LLM's marker-aligned response) to
    `source_blob`. Returns `(bytes, mime, suggested_ext)`.

    The output keeps the original file type for DOCX/XLSX/PPTX. For
    PDF the output is a DOCX (PDF in-place editing is deferred — see
    module docstring)."""
    ext = _ext(filename)
    edits = _parse_marked(modified_text)
    if not edits:
        raise ValueError(
            "No `[N] …` marker found in the modified content — "
            "the model must return the markers intact."
        )
    if ext == ".docx":
        return _apply_docx(source_blob, edits, language)
    if ext in (".xlsx", ".xlsm"):
        return _apply_xlsx(source_blob, edits, language)
    if ext == ".pptx":
        return _apply_pptx(source_blob, edits, language)
    if ext == ".pdf":
        return _apply_pdf_as_docx(source_blob, edits, language)
    raise ValueError(
        f"apply_marked: unsupported extension {ext!r}"
    )


# ── Helpers (internal) ───────────────────────────────────────────────────


def _ext(filename):
    return ("." + filename.rsplit(".", 1)[-1].lower()
            if "." in (filename or "") else "")


# `[N] text`  — N must be 1+ digits; the space after `]` is optional so
# the model can emit `[1]text` and it still parses. Continuation lines
# (no `[N]` prefix) are folded into the previous marker so multi-line
# paragraphs survive the round-trip.
_MARK_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")


def _parse_marked(text):
    """`[1] foo\\n[2] bar\\nmore` → {1:'foo', 2:'bar\\nmore'}."""
    out = {}
    current_n = None
    for raw in (text or "").split("\n"):
        m = _MARK_RE.match(raw)
        if m:
            n = int(m.group(1))
            out[n] = m.group(2)
            current_n = n
        elif current_n is not None and raw.strip():
            out[current_n] = out[current_n] + "\n" + raw
    return out


def _build_from_lines(lines, max_chars):
    out = []
    total = 0
    for i, ln in enumerate(lines, start=1):
        line = f"[{i}] {ln}"
        if total + len(line) > max_chars:
            out.append(f"[…content truncated after {i-1} units…]")
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


_RTL_TOKENS = (
    "ar", "arab", "he", "hebr", "iw",
    "fa", "pers", "fars", "ur", "urdu", "yi", "yidd",
)

# Unicode blocks dominated by right-to-left scripts. We scan the
# translated text for these so we can flip a paragraph to RTL without
# needing the LLM to remember to pass `language`. Auto-detection is
# the primary signal; the `language` arg is just a fallback hint.
_RTL_RANGES = (
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0700, 0x074F),   # Syriac
    (0x0750, 0x077F),   # Arabic Supplement
    (0x0780, 0x07BF),   # Thaana
    (0x07C0, 0x07FF),   # NKo
    (0x0800, 0x083F),   # Samaritan
    (0x0840, 0x085F),   # Mandaic
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB1D, 0xFB4F),   # Hebrew Presentation Forms
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)


def _is_rtl(lang):
    if not lang:
        return False
    s = str(lang).lower().strip()
    return any(t in s for t in _RTL_TOKENS)


def _is_rtl_text(text):
    """Decide if `text` is predominantly RTL by scanning its letters.
    Digits, punctuation and spaces are neutral and ignored — they
    appear in both scripts and would skew the count."""
    if not text:
        return False
    import unicodedata
    rtl = ltr = 0
    for ch in text:
        if unicodedata.category(ch)[0] != "L":
            continue
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _RTL_RANGES):
            rtl += 1
        else:
            ltr += 1
    return rtl > 0 and rtl >= ltr


# ── DOCX ─────────────────────────────────────────────────────────────────


def _docx_walk(doc):
    """Yield every paragraph in a STABLE order: body → table cells
    (table-major, row-major) → header/footer per section. Same order
    in extract and apply, so marker N maps to the same paragraph."""
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p
    for section in doc.sections:
        for p in section.header.paragraphs:
            yield p
        for p in section.footer.paragraphs:
            yield p


def _extract_docx(blob, max_chars):
    from docx import Document
    doc = Document(io.BytesIO(blob))
    lines = []
    total = 0
    n = 0
    for p in _docx_walk(doc):
        text = (p.text or "").strip()
        if not text:
            continue
        n += 1
        line = f"[{n}] {text}"
        if total + len(line) > max_chars:
            lines.append(f"[…content truncated after {n-1} units…]")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _set_or_replace(parent, qn, tag_local, OxmlElement, val="1"):
    """Force-set `parent/<w:tag>` to `val`, removing any pre-existing
    sibling with the same tag first. Required because source DOCX may
    already carry `<w:bidi w:val="0"/>` (explicit LTR) and a naive
    append-if-missing would leave that LTR flag in place, defeating the
    whole RTL transformation."""
    for existing in parent.findall(qn(f"w:{tag_local}")):
        parent.remove(existing)
    el = OxmlElement(f"w:{tag_local}")
    el.set(qn("w:val"), val)
    parent.append(el)


def _apply_rtl_docx_paragraph(p, OxmlElement, qn, WD_ALIGN_PARAGRAPH):
    """Mark `p` as bidi-RTL at every level Word looks at:
      • pPr/w:bidi="1"       — paragraph direction is RTL
      • pPr/jc=right         — text aligned to the right edge
      • clear pPr/w:ind       — drop left-indent that pushes RTL text
                                 off the right side of the page
      • rPr/w:rtl="1" on each run — runs are RTL-script

    Just setting alignment-right is NOT enough. Without bidi, Word
    keeps left-to-right flow with the cursor on the right edge — text
    *looks* aligned right but the line wraps in the wrong direction
    and Latin sub-strings (numbers, brand names) appear in the wrong
    order. Also, an existing `<w:bidi w:val=\"0\"/>` from the source
    template would explicitly suppress RTL — we have to REPLACE such
    elements, not just skip them."""
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    pPr = p._element.get_or_add_pPr()
    _set_or_replace(pPr, qn, "bidi", OxmlElement, val="1")
    # Indents are direction-sensitive in Word: an `ind w:left=...`
    # attribute pushes RTL text TOWARDS the page centre instead of
    # towards the right edge. Drop the whole element — the source's
    # paragraph style still carries the spacing it needs.
    for ind in pPr.findall(qn("w:ind")):
        pPr.remove(ind)
    for r in p.runs:
        rPr = r._element.get_or_add_rPr()
        _set_or_replace(rPr, qn, "rtl", OxmlElement, val="1")


def _apply_docx(blob, edits, language):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document(io.BytesIO(blob))
    lang_rtl = _is_rtl(language)
    n = 0
    touched_any_rtl = False
    for p in _docx_walk(doc):
        if not (p.text or "").strip():
            continue
        n += 1
        if n not in edits:
            continue
        new_text = edits[n]
        # Preserve the first run's formatting; clear the others so we
        # don't leave fragments of the original text behind.
        if p.runs:
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
        else:
            p.add_run(new_text)
        # Auto-detect RTL from THIS paragraph's text first. The
        # `language` hint only applies if the LLM bothered to pass
        # one — auto-detection works either way and even handles
        # mixed-direction documents (e.g. a French doc with Arabic
        # quotes interspersed) on a per-paragraph basis.
        if _is_rtl_text(new_text) or lang_rtl:
            _apply_rtl_docx_paragraph(
                p, OxmlElement, qn, WD_ALIGN_PARAGRAPH,
            )
            touched_any_rtl = True
    # Also flip the SECTION to RTL when we've written RTL paragraphs
    # — this controls margins, headers/footers and column order in
    # Word. Force-replace any existing bidi (the source template very
    # often has `<w:bidi w:val=\"0\"/>` explicitly set, which would
    # nail the page layout to LTR even though the paragraphs are
    # RTL).
    if touched_any_rtl:
        for section in doc.sections:
            sectPr = section._sectPr
            if sectPr is not None:
                _set_or_replace(
                    sectPr, qn, "bidi", OxmlElement, val="1",
                )
    buf = io.BytesIO()
    doc.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document",
        ".docx",
    )


# ── XLSX ─────────────────────────────────────────────────────────────────


def _xlsx_walk(wb):
    """Yield every cell whose value is a non-empty STRING. Numeric /
    boolean / date cells are skipped — translating a number makes no
    sense. Order: sheet-major, then row-major (top→bottom, left→right)."""
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                v = cell.value
                if v is None or not isinstance(v, str):
                    continue
                if not v.strip():
                    continue
                yield cell


def _extract_xlsx(blob, max_chars):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(blob), data_only=True)
    lines = []
    total = 0
    n = 0
    for cell in _xlsx_walk(wb):
        n += 1
        text = str(cell.value).strip()
        # Multiline cells: keep as one logical unit; replace internal
        # newlines with literal \n so the marker parser doesn't think
        # they're continuation lines we want to fold back.
        text = text.replace("\n", "\\n")
        line = f"[{n}] {text}"
        if total + len(line) > max_chars:
            lines.append(f"[…content truncated after {n-1} cells…]")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _apply_xlsx(blob, edits, language):
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    wb = load_workbook(io.BytesIO(blob))
    lang_rtl = _is_rtl(language)
    n = 0
    for cell in _xlsx_walk(wb):
        n += 1
        if n not in edits:
            continue
        new_value = edits[n].replace("\\n", "\n")
        cell.value = new_value
        # Per-cell RTL handling: if the new text is RTL (or the user
        # named an RTL language), set horizontal=right + readingOrder=2.
        # Excel uses readingOrder 2 to mean "RTL", regardless of the
        # workbook's global default — without it numeric-only sheets
        # stay LTR even when the labels are Arabic.
        if _is_rtl_text(new_value) or lang_rtl:
            cur = cell.alignment or Alignment()
            cell.alignment = Alignment(
                horizontal="right",
                vertical=cur.vertical,
                text_rotation=cur.text_rotation,
                wrap_text=cur.wrap_text,
                shrink_to_fit=cur.shrink_to_fit,
                indent=cur.indent,
                readingOrder=2,
            )
    buf = io.BytesIO()
    wb.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet",
        ".xlsx",
    )


# ── PPTX ─────────────────────────────────────────────────────────────────


def _pptx_walk(prs):
    """Yield (paragraph) for every text paragraph in every shape with
    a text_frame. Order: slide-major, then shape order on each slide,
    then paragraph order in each shape."""
    for slide in prs.slides:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for p in shape.text_frame.paragraphs:
                yield p


def _pptx_para_text(p):
    return "".join(r.text or "" for r in p.runs)


def _extract_pptx(blob, max_chars):
    from pptx import Presentation
    prs = Presentation(io.BytesIO(blob))
    lines = []
    total = 0
    n = 0
    for p in _pptx_walk(prs):
        text = _pptx_para_text(p).strip()
        if not text:
            continue
        n += 1
        line = f"[{n}] {text}"
        if total + len(line) > max_chars:
            lines.append(f"[…content truncated after {n-1} units…]")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _apply_pptx(blob, edits, language):
    from pptx import Presentation
    from pptx.enum.text import PP_ALIGN
    prs = Presentation(io.BytesIO(blob))
    lang_rtl = _is_rtl(language)
    n = 0
    for p in _pptx_walk(prs):
        if not _pptx_para_text(p).strip():
            continue
        n += 1
        if n not in edits:
            continue
        new_text = edits[n]
        if p.runs:
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
        # If no runs exist we can't synthesise one without losing the
        # paragraph's style — leave it; that paragraph just won't be
        # translated. In practice every text-bearing PPTX paragraph
        # has at least one run.
        if _is_rtl_text(new_text) or lang_rtl:
            p.alignment = PP_ALIGN.RIGHT
            # PPTX paragraphs carry their RTL flag in pPr/@rtl="1".
            # python-pptx doesn't expose a high-level setter, so we
            # poke the XML directly — without this the text is just
            # right-aligned LTR and Arabic glyphs render left-to-right.
            pPr = p._pPr
            if pPr is None:
                pPr = p._p.get_or_add_pPr()
            pPr.set("rtl", "1")
    buf = io.BytesIO()
    prs.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation",
        ".pptx",
    )


# ── PDF ──────────────────────────────────────────────────────────────────


def _extract_pdf(blob, max_chars):
    """Text-PDF extraction: one non-blank text line per marker. Scanned
    PDFs return empty here; the caller can fall back to the existing
    OCR path for Q&A use cases, but `modify_document` won't work for
    them in v1."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(blob))
    lines = []
    total = 0
    n = 0
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            continue
        for raw in txt.split("\n"):
            s = raw.strip()
            if not s:
                continue
            n += 1
            line = f"[{n}] {s}"
            if total + len(line) > max_chars:
                lines.append(f"[…content truncated after {n-1} units…]")
                return "\n".join(lines)
            lines.append(line)
            total += len(line) + 1
    return "\n".join(lines)


def _apply_pdf_as_docx(blob, edits, language):
    """PDFs are too brittle to edit in place across all the layouts
    real-world files use (multi-column, embedded fonts, vector text…).
    Rebuild the modified content as a DOCX — the content + RTL layout
    are preserved, the original PDF's visual design is not. Clear and
    safe trade-off for v1."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    lang_rtl = _is_rtl(language)
    touched_any_rtl = False
    for n in sorted(edits.keys()):
        text = edits[n]
        p = doc.add_paragraph(text)
        if _is_rtl_text(text) or lang_rtl:
            _apply_rtl_docx_paragraph(
                p, OxmlElement, qn, WD_ALIGN_PARAGRAPH,
            )
            touched_any_rtl = True
    if touched_any_rtl:
        for section in doc.sections:
            sectPr = section._sectPr
            if sectPr is not None and sectPr.find(qn("w:bidi")) is None:
                sectPr.append(OxmlElement("w:bidi"))
    buf = io.BytesIO()
    doc.save(buf)
    return (
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document",
        ".docx",
    )
