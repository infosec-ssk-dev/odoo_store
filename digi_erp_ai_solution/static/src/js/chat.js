/** @odoo-module **/
/**
 * DIGI-ERP AI — full-page chat, Odoo 19 frontend
 *
 *   POST /ai-solution/ask  →  {prompt}
 *   ← {ok, response, model} | {ok: false, error}
 *
 * The real HTTP call to the model happens server-side
 * (controllers/main.py) to avoid CORS problems.
 *
 * Declared as an @odoo-module purely so `_t` is importable: every string a
 * user can read on this page goes through it, and the .po files carry the
 * French and Arabic. Nothing else about the file changed — the body is still
 * the same self-executing function it always was.
 */

import { _t } from "@web/core/l10n/translation";

(function () {
    "use strict";

    // ── DOM ──────────────────────────────────────────────────────────────────
    const messagesEl   = document.getElementById("dai-chat-messages");
    const inputEl      = document.getElementById("dai-chat-input");
    const sendBtn      = document.getElementById("dai-chat-send-btn");
    const clearBtn     = document.getElementById("dai-chat-clear-btn");
    const typingEl     = document.getElementById("dai-chat-typing");
    const typingTextEl = document.getElementById("dai-chat-typing-text");
    const attachBtn    = document.getElementById("dai-chat-attach-btn");
    const fileInputEl  = document.getElementById("dai-chat-file-input");
    const fileChipEl   = document.getElementById("dai-chat-file-chip");
    const fileNameEl   = document.getElementById("dai-chat-file-name");
    const fileSizeEl   = document.getElementById("dai-chat-file-size");
    const fileRemoveEl = document.getElementById("dai-chat-file-remove");
    const convListEl   = document.getElementById("dai-chat-conv-list");
    const newConvBtn   = document.getElementById("dai-chat-new-conv-btn");
    // Custom model picker (replaces the native <select>).
    const pickerEl     = document.getElementById("dai-chat-model-picker");
    const pickerTrigger = pickerEl && pickerEl.querySelector(".dai-chat-model-trigger");
    const pickerText    = pickerEl && pickerEl.querySelector(".dai-chat-model-trigger-text");
    const pickerMenu    = pickerEl && pickerEl.querySelector(".dai-chat-model-menu");

    if (!messagesEl || !inputEl || !sendBtn) return; // page sans chat

    // ── Limites côté client (alignées avec le serveur) ───────────────────────
    const MAX_FILE_BYTES = 5 * 1024 * 1024;
    const ALLOWED_EXT = [
        // Text-based.
        ".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".htm",
        ".py", ".js", ".css", ".tsv", ".yml", ".yaml",
        // PDFs and Office documents.
        ".pdf", ".docx",
        ".xlsx", ".xlsm", ".xls",
        ".pptx",
        // Images (go through the OCR pipeline).
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
        // Archives (recursively extracted).
        ".zip",
    ];

    let attachedFile = null;
    let currentConversationId = null;
    let conversations = [];
    // Empty string = no provider selected → backend falls back to the
    // global settings (Settings → DIGI-ERP AI).
    let currentProviderId = "";
    // Last prompt the user actually sent (text only). Kept so the
    // "Regénérer" action on a bot bubble can re-run the exact same turn.
    let lastUserPrompt = "";
    // AbortController for the in-flight /ask request, so the Stop button
    // can cancel a turn mid-flight. null when nothing is running.
    let currentAbort = null;
    // Handle for the phased-status timer (see setLoading). Cleared when
    // loading ends so a late tick never overwrites the idle state.
    let statusTimer = null;
    // Multi-company: the user's allowed companies and the currently-selected
    // subset the assistant's data tools should span. Empty selection = all.
    let companiesCache = [];
    let selectedCompanyIds = [];   // array of ints

    // ── Helper : appel JSON-RPC vers le contrôleur Odoo ─────────────────────
    async function rpc(url, params) {
        const res = await fetch(url, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
                jsonrpc: "2.0",
                method:  "call",
                id:      Date.now(),
                params:  params || {},
            }),
        });
        if (res.redirected || res.status === 401 || res.status === 403) {
            throw new Error("AUTH_REQUIRED");
        }
        const json = await res.json();
        return json.result || json;
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    /** Fait défiler vers le bas */
    function scrollBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    /** Escape a string for safe insertion into HTML — text OR attribute.
     *
     * The QUOTES are the point. renderMarkdown() interpolates the result into
     * `src="..."` and `href="..."`, so a value carrying a double quote closes
     * the attribute and everything after it is read as markup. The old
     * implementation (textContent -> innerHTML) escaped only & < >, which made
     * an image or a link whose URL contained a quote — something the model
     * will happily echo back from a user message or an uploaded file —
     * execute in the chat bubble.
     *
     * The backtick is deliberately NOT escaped, unlike in Odoo's own escape():
     * every attribute we build is double-quoted, so a backtick cannot break
     * out of one, and escaping it would stop renderMarkdown from recognising
     * inline `code` spans.
     */
    const ESC_MAP = {
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#x27;",
    };
    function esc(str) {
        return String(str === null || str === undefined ? "" : str)
            .replace(/[&<>"']/g, (c) => ESC_MAP[c]);
    }

    /**
     * Mini-parseur Markdown → HTML.
     * ALL HTML is escaped first, then we turn the
     * Markdown patterns into safe tags. No raw input ever reaches the DOM.
     */
    // Common LaTeX → Unicode mappings. Some LLMs (especially small local
    // ones) wrap arrows / operators in $...$ math mode. We don't render
    // LaTeX, so we map the most frequent commands to their Unicode glyphs
    // and strip the surrounding $ delimiters.
    const _LATEX_TO_UNICODE = {
        "\\rightarrow": "→", "\\to": "→",
        "\\leftarrow":  "←", "\\gets": "←",
        "\\Rightarrow": "⇒", "\\Leftarrow": "⇐",
        "\\leftrightarrow": "↔", "\\Leftrightarrow": "⇔",
        "\\uparrow":    "↑", "\\downarrow": "↓",
        "\\times": "×", "\\div": "÷", "\\pm": "±", "\\mp": "∓",
        "\\leq": "≤", "\\geq": "≥", "\\neq": "≠", "\\approx": "≈",
        "\\infty": "∞", "\\partial": "∂", "\\nabla": "∇",
        "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
        "\\epsilon": "ε", "\\theta": "θ", "\\lambda": "λ",
        "\\mu": "μ", "\\pi": "π", "\\sigma": "σ", "\\phi": "φ",
        "\\omega": "ω", "\\Omega": "Ω", "\\Delta": "Δ", "\\Sigma": "Σ",
        "\\sum": "∑", "\\prod": "∏", "\\int": "∫",
        "\\bullet": "•", "\\cdot": "·", "\\dots": "…", "\\ldots": "…",
        "\\,": " ", "\\;": " ", "\\!": "", "\\quad": "  ", "\\qquad": "    ",
    };

    function _stripLatex(src) {
        if (!src) return src;
        if (src.indexOf("$") === -1 && src.indexOf("\\") === -1) return src;
        const peel = (inner) => inner.replace(/\\[a-zA-Z]+|\\[^a-zA-Z]/g,
            cmd => _LATEX_TO_UNICODE[cmd] !== undefined
                ? _LATEX_TO_UNICODE[cmd]
                : (cmd.startsWith("\\") ? cmd.slice(1) : cmd)
        );
        // $$...$$ first (display math), then $...$ (inline math).
        src = src.replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => peel(inner));
        src = src.replace(/\$([^\$\n]+)\$/g, (_, inner) => peel(inner));
        return src;
    }

    function renderMarkdown(src) {
        // 0. Strip / convert LaTeX math notation before anything else, so
        //    `$\rightarrow$` doesn't reach the user as raw source.
        src = _stripLatex(src);

        // 1. Extraire les blocs ``` pour ne pas les transformer
        const codeBlocks = [];
        src = src.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
            const idx = codeBlocks.length;
            codeBlocks.push({ lang, code });
            return ` CODEBLOCK${idx} `;
        });

        // 2. Échapper tout
        let html = esc(src);

        // 3. Tableaux pipe-style : | a | b |\n| --- | --- |\n| 1 | 2 |
        html = html.replace(
            /(^|\n)((?:\|[^\n]*\|\s*\n)+)/g,
            (match, lead, block) => {
                const lines = block.trim().split("\n");
                if (lines.length < 2) return match;
                const sep = lines[1];
                if (!/^\s*\|?\s*:?-{3,}/.test(sep)) return match;
                const splitRow = (l) =>
                    l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
                const head = splitRow(lines[0]);
                const rows = lines.slice(2).map(splitRow);
                let t = '<table class="dai-chat-md-table"><thead><tr>';
                head.forEach(h => (t += `<th>${h}</th>`));
                t += "</tr></thead><tbody>";
                rows.forEach(r => {
                    t += "<tr>";
                    r.forEach(c => (t += `<td>${c}</td>`));
                    t += "</tr>";
                });
                t += "</tbody></table>";
                return lead + t;
            }
        );

        // 4. Titres ###### ... #
        html = html.replace(/^(#{1,6})\s+(.+)$/gm, (_, h, t) => {
            const lvl = h.length;
            return `<h${lvl} class="dai-chat-md-h${lvl}">${t.trim()}</h${lvl}>`;
        });

        // 5. Filet horizontal --- ou ***
        html = html.replace(/^\s*(?:---|\*\*\*|___)\s*$/gm, '<hr class="dai-chat-md-hr">');

        // 6. Listes (regroupe lignes consécutives)
        // Non ordonnée
        html = html.replace(/(?:^|\n)((?:\s*[-*+]\s+.+\n?)+)/g, (m, block) => {
            const items = block.trim().split(/\n/).map(l =>
                l.replace(/^\s*[-*+]\s+/, "").trim()
            );
            return "\n<ul class=\"dai-chat-md-ul\">" + items.map(i => `<li>${i}</li>`).join("") + "</ul>";
        });
        // Ordonnée
        html = html.replace(/(?:^|\n)((?:\s*\d+\.\s+.+\n?)+)/g, (m, block) => {
            const items = block.trim().split(/\n/).map(l =>
                l.replace(/^\s*\d+\.\s+/, "").trim()
            );
            return "\n<ol class=\"dai-chat-md-ol\">" + items.map(i => `<li>${i}</li>`).join("") + "</ol>";
        });

        // 7. Gras / italique / code inline / liens
        html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
        html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
        html = html.replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
        html = html.replace(/`([^`\n]+)`/g, '<code class="dai-chat-md-code">$1</code>');
        // Images ![alt](url) — must run BEFORE the link rule, otherwise
        // the link rule would consume the `[alt](url)` portion and the
        // `!` would be left dangling. The preview lets `generate_image`
        // show the picture inline in the chat bubble.
        html = html.replace(
            /!\[([^\]]*)\]\(([^\s)]+)\)/g,
            (_, alt, url) =>
                `<img class="dai-chat-md-img" src="${url}" alt="${alt}" loading="lazy">`
        );
        // Liens de téléchargement → bouton spécial
        html = html.replace(
            /\[([^\]]+)\]\((\/ai-solution\/download\/[^\s)]+)\)/g,
            (_, label, url) => {
                const cleanLabel = label.replace(/[\u{1F300}-\u{1FFFF}]/gu, "").trim();
                return `<a href="${url}" class="dai-chat-download-btn" download>`
                    + `<i class="fa fa-download"></i> ${cleanLabel}</a>`;
            }
        );
        // Liens externes ordinaires
        html = html.replace(
            /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
        );

        // 8. Paragraphes — double \n entre blocs hors balise
        html = html
            .split(/\n{2,}/)
            .map(chunk => {
                const trimmed = chunk.trim();
                if (!trimmed) return "";
                if (/^<(h\d|ul|ol|table|hr|pre|blockquote)/.test(trimmed)) return trimmed;
                return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
            })
            .join("\n");

        // 9. Réinsérer les blocs de code
        html = html.replace(/ CODEBLOCK(\d+) /g, (_, i) => {
            const { lang, code } = codeBlocks[parseInt(i, 10)];
            if ((lang || "").toLowerCase() === "chart" && window.DaiCharts) {
                const ph = window.DaiCharts.chartPlaceholder(code.trim());
                if (ph) {
                    return ph;
                }
                // Invalid chart JSON -> fall back to a readable code block.
            }
            return `<pre class="dai-chat-md-pre"><code class="dai-chat-md-code-block${lang ? " language-" + esc(lang) : ""}">${esc(code)}</code></pre>`;
        });

        return html;
    }

    function _formatTimestamp(date) {
        if (!date) return "";
        const d = (date instanceof Date) ? date : new Date(date);
        if (isNaN(d)) return "";
        const now = new Date();
        const isToday = d.toDateString() === now.toDateString();
        const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        if (isToday) return time;
        return d.toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) + " " + time;
    }

    /**
     * Ajoute une bulle dans la zone de messages.
     * @param {"user"|"bot"} who
     * @param {string} text        texte brut (Markdown pour le bot, échappé pour l'utilisateur)
     * @param {boolean} isError
     * @param {Date|string} [ts]   horodatage optionnel (Date ou ISO string)
     */
    /**
     * Reveal a markdown response progressively (ChatGPT-style typewriter).
     * Re-renders markdown on each tick so tables / code blocks form correctly
     * as the text grows. Caret blinking comes from the .dai-chat-streaming class.
     */
    function streamMarkdownInto(bubble, fullText) {
        const tokens = fullText.match(/\s+|\S+/g) || [];
        if (!tokens.length) {
            bubble.innerHTML = "";
            return;
        }
        // Target ~25 ms tick, total duration capped at ~3 s so long replies
        // don't feel sluggish. Short replies still get a perceptible animation.
        const targetMs = Math.min(3000, 250 + tokens.length * 18);
        const tickMs = 25;
        const totalTicks = Math.max(1, Math.floor(targetMs / tickMs));
        const batchSize = Math.max(1, Math.ceil(tokens.length / totalTicks));

        bubble.classList.add("dai-chat-streaming");
        let i = 0;

        function step() {
            i = Math.min(tokens.length, i + batchSize);
            const partial = tokens.slice(0, i).join("");
            bubble.innerHTML = renderMarkdown(partial);
            scrollBottom();
            if (i < tokens.length) {
                setTimeout(step, tickMs);
            } else {
                bubble.classList.remove("dai-chat-streaming");
                if (window.DaiCharts) { window.DaiCharts.hydrateCharts(bubble); }
            }
        }
        step();
    }

    function _fileIconFor(mt) {
        mt = (mt || "").toLowerCase();
        if (mt.startsWith("image/")) return "fa-file-image-o";
        if (mt === "application/pdf") return "fa-file-pdf-o";
        if (mt.includes("spreadsheet") || mt.includes("excel")
            || mt.includes("csv")) return "fa-file-excel-o";
        if (mt.includes("presentation") || mt.includes("powerpoint"))
            return "fa-file-powerpoint-o";
        if (mt.includes("word") || mt.includes("wordprocessingml"))
            return "fa-file-word-o";
        if (mt.startsWith("text/")) return "fa-file-text-o";
        if (mt.includes("zip") || mt.includes("archive")
            || mt.includes("compressed")) return "fa-file-archive-o";
        return "fa-file-o";
    }

    /** Build the attachment chip rendered inside a user message bubble.
     *  Image attachments get a thumbnail; everything else gets an icon.
     *  Both kinds expose Preview (open in new tab) + Download links,
     *  plus filename and size.
     *
     *  `att` shape:
     *     {name, mimetype, size, url, download_url}
     *  When `url` is null/missing (legacy messages whose file was never
     *  persisted), the chip degrades gracefully to filename-only. */
    function renderAttachmentChip(att) {
        const mt = att.mimetype || "";
        const isImage = mt.startsWith("image/");
        const url = att.url || "";
        const dl = att.download_url || (url ? url + "?download=1" : "");
        const name = att.name || "file";
        const size = att.size ? formatBytes(att.size) : "";

        const wrap = document.createElement("div");
        wrap.className = "dai-chat-attach"
            + (isImage && url ? " dai-chat-attach--image" : "")
            + (!url ? " dai-chat-attach--ghost" : "");

        // Thumbnail (images) or icon tile (everything else).
        if (isImage && url) {
            const link = document.createElement("a");
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.className = "dai-chat-attach-thumb";
            const img = document.createElement("img");
            img.src = url;
            img.alt = name;
            link.appendChild(img);
            wrap.appendChild(link);
        } else {
            const tile = document.createElement("div");
            tile.className = "dai-chat-attach-icon";
            tile.innerHTML = `<i class="fa ${_fileIconFor(mt)}"></i>`;
            wrap.appendChild(tile);
        }

        // Filename + size + action buttons.
        const meta = document.createElement("div");
        meta.className = "dai-chat-attach-meta";
        const nameEl = document.createElement("div");
        nameEl.className = "dai-chat-attach-name";
        nameEl.textContent = name;
        nameEl.title = name;
        meta.appendChild(nameEl);
        if (size) {
            const sizeEl = document.createElement("div");
            sizeEl.className = "dai-chat-attach-size";
            sizeEl.textContent = size;
            meta.appendChild(sizeEl);
        }
        if (url) {
            const actions = document.createElement("div");
            actions.className = "dai-chat-attach-actions";
            const open = document.createElement("a");
            open.href = url;
            open.target = "_blank";
            open.rel = "noopener noreferrer";
            open.className = "dai-chat-attach-btn";
            open.innerHTML = '<i class="fa fa-external-link"></i> Open';
            actions.appendChild(open);
            const dlBtn = document.createElement("a");
            dlBtn.href = dl;
            dlBtn.download = name;
            dlBtn.className = "dai-chat-attach-btn";
            dlBtn.innerHTML = '<i class="fa fa-download"></i> Download';
            actions.appendChild(dlBtn);
            meta.appendChild(actions);
        }
        wrap.appendChild(meta);
        return wrap;
    }

    /**
     * Build the collapsible "activity receipt" shown under a bot answer.
     * `activity` is the list of {icon, label, detail, ok} produced by the
     * backend (_summarize_tool_calls). Returns null when there's nothing
     * to show, so text-only answers stay clean.
     */
    function renderActivityReceipt(activity) {
        if (!Array.isArray(activity) || !activity.length) return null;

        const details = document.createElement("details");
        details.className = "dai-chat-activity";

        const summary = document.createElement("summary");
        summary.className = "dai-chat-activity-summary";
        const n = activity.length;
        summary.innerHTML =
            '<i class="fa fa-bolt dai-chat-activity-spark"></i>'
            + `<span>${n} action${n > 1 ? "s" : ""} performed</span>`
            + '<i class="fa fa-angle-down dai-chat-activity-caret"></i>';
        details.appendChild(summary);

        const list = document.createElement("div");
        list.className = "dai-chat-activity-steps";
        activity.forEach(step => {
            const row = document.createElement("div");
            row.className = "dai-chat-activity-step"
                + (step && step.ok === false ? " dai-chat-activity-step--fail" : "");
            const ico = document.createElement("span");
            ico.className = "dai-chat-activity-step-icon";
            ico.textContent = (step && step.icon) || "•";
            const lbl = document.createElement("span");
            lbl.className = "dai-chat-activity-step-label";
            lbl.textContent = (step && step.label) || "";
            row.appendChild(ico);
            row.appendChild(lbl);
            if (step && step.detail) {
                const det = document.createElement("span");
                det.className = "dai-chat-activity-step-detail";
                det.textContent = step.detail;
                row.appendChild(det);
            }
            list.appendChild(row);
        });
        details.appendChild(list);
        return details;
    }

    /**
     * Build the row of tappable follow-up suggestion chips. Clicking a chip
     * drops its text into the composer and sends it, so the user can drill
     * in without typing. Returns null when there are no suggestions.
     */
    function renderSuggestions(suggestions) {
        if (!Array.isArray(suggestions) || !suggestions.length) return null;
        const wrap = document.createElement("div");
        wrap.className = "dai-chat-suggestions";
        suggestions.forEach(s => {
            const txt = (s || "").toString().trim();
            if (!txt) return;
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "dai-chat-suggestion-chip";
            chip.textContent = txt;
            chip.addEventListener("click", () => {
                if (inputEl.disabled) return;      // a turn is already running
                inputEl.value = txt;
                sendMessage();
            });
            wrap.appendChild(chip);
        });
        return wrap.children.length ? wrap : null;
    }

    /**
     * Build the copy / regenerate action row shown under a finished bot
     * answer. `rawText` is the original markdown so "Copier" yields the
     * source, not the rendered HTML.
     */
    function renderMessageActions(rawText) {
        const row = document.createElement("div");
        row.className = "dai-chat-msg-actions";

        const copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "dai-chat-msg-action";
        copyBtn.title = _t("Copy the answer");
        copyBtn.innerHTML = '<i class="fa fa-clone"></i>';
        copyBtn.addEventListener("click", () => {
            const done = () => {
                copyBtn.innerHTML = '<i class="fa fa-check"></i>';
                setTimeout(() => { copyBtn.innerHTML = '<i class="fa fa-clone"></i>'; }, 1400);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(rawText || "").then(done, () => {});
            } else {
                const ta = document.createElement("textarea");
                ta.value = rawText || "";
                document.body.appendChild(ta);
                ta.select();
                try { document.execCommand("copy"); done(); } catch (_) {}
                document.body.removeChild(ta);
            }
        });
        row.appendChild(copyBtn);

        const regenBtn = document.createElement("button");
        regenBtn.type = "button";
        regenBtn.className = "dai-chat-msg-action";
        regenBtn.title = _t("Regenerate the answer");
        regenBtn.innerHTML = '<i class="fa fa-refresh"></i>';
        regenBtn.addEventListener("click", () => {
            if (inputEl.disabled || !lastUserPrompt) return;
            inputEl.value = lastUserPrompt;
            sendMessage();
        });
        row.appendChild(regenBtn);

        return row;
    }

    /**
     * Confirmation card for a WRITE the assistant wants to perform.
     *
     * The backend pauses on any create / update / status-change and returns
     * `pending_action = {tool, params, preview}`. Nothing has been written
     * yet. We render a "before → after" review with Confirm / Cancel. Only
     * the FRIENDLY labels from `preview` are shown — never the technical
     * model/field/method names (those live in `params`, which we echo back
     * verbatim on confirm but never display).
     */
    function appendPendingAction(pending, ts = null) {
        const preview = (pending && pending.preview) || {};
        const changes = preview.changes || [];

        const wrap = document.createElement("div");
        wrap.className = "dai-chat-msg dai-chat-msg--bot";

        const avatar = document.createElement("div");
        avatar.className = "dai-chat-avatar";
        avatar.innerHTML = '<img class="dai-chat-avatar-logo" '
            + 'src="/digi_erp_ai_solution/static/src/img/digi-erp.png" '
            + 'alt="DIGI-ERP AI"/>';

        const inner = document.createElement("div");
        inner.className = "dai-chat-msg-inner";

        const card = document.createElement("div");
        card.className = "dai-chat-confirm-card";

        // Header — the friendly summary ("Créer une facture …").
        const head = document.createElement("div");
        head.className = "dai-chat-confirm-head";
        head.innerHTML = '<i class="fa fa-shield"></i> '
            + esc(preview.summary || "Action to confirm");
        card.appendChild(head);

        const sub = document.createElement("div");
        sub.className = "dai-chat-confirm-sub";
        sub.textContent = _t("Review the changes below. "
            + "Nothing is saved until you confirm.");
        card.appendChild(sub);

        // Before → after table (only for create/write; method actions have
        // no field diff and show a note instead).
        if (changes.length) {
            const tbl = document.createElement("table");
            tbl.className = "dai-chat-confirm-table";
            tbl.innerHTML =
                "<thead><tr><th>" + esc(_t("Field")) + "</th><th>"
                + esc(_t("Before")) + "</th><th>" + esc(_t("After")) + "</th>"
                + "</tr></thead>";
            const tb = document.createElement("tbody");
            changes.forEach((c) => {
                const tr = document.createElement("tr");
                const before = (c.before === undefined || c.before === null || c.before === "")
                    ? "—" : String(c.before);
                const after = (c.after === undefined || c.after === null || c.after === "")
                    ? "—" : String(c.after);
                tr.innerHTML =
                    "<td class='dai-chat-confirm-field'>" + esc(c.label || "") + "</td>"
                    + "<td class='dai-chat-confirm-before'>" + esc(before) + "</td>"
                    + "<td class='dai-chat-confirm-after'>" + esc(after) + "</td>";
                tb.appendChild(tr);
            });
            tbl.appendChild(tb);
            card.appendChild(tbl);
        }

        if (preview.note) {
            const note = document.createElement("div");
            note.className = "dai-chat-confirm-note";
            note.innerHTML = '<i class="fa fa-exclamation-triangle"></i> '
                + esc(preview.note);
            card.appendChild(note);
        }

        // Action buttons.
        const actions = document.createElement("div");
        actions.className = "dai-chat-confirm-actions";

        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "dai-chat-confirm-btn dai-chat-confirm-cancel";
        cancelBtn.innerHTML = '<i class="fa fa-times"></i> ' + esc(_t("Cancel"));

        const okBtn = document.createElement("button");
        okBtn.type = "button";
        okBtn.className = "dai-chat-confirm-btn dai-chat-confirm-ok";
        okBtn.innerHTML = '<i class="fa fa-check"></i> ' + esc(_t("Confirm"));

        let settled = false;
        const settle = (label) => {
            settled = true;
            okBtn.disabled = true;
            cancelBtn.disabled = true;
            actions.innerHTML = "";
            const done = document.createElement("div");
            done.className = "dai-chat-confirm-settled";
            done.textContent = label;
            card.appendChild(done);
        };

        cancelBtn.addEventListener("click", async () => {
            if (settled) return;
            settle("❌ Action cancelled.");
            try {
                await rpc("/ai-solution/confirm-action", {
                    tool: pending.tool,
                    params: pending.params,
                    conversation_id: currentConversationId,
                    confirm: false,
                    company_ids: selectedCompanyIds,
                });
            } catch (_) { /* fail-open: card already shows cancelled */ }
        });

        okBtn.addEventListener("click", async () => {
            if (settled) return;
            okBtn.disabled = true;
            cancelBtn.disabled = true;
            okBtn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> …';
            try {
                const r = await rpc("/ai-solution/confirm-action", {
                    tool: pending.tool,
                    params: pending.params,
                    conversation_id: currentConversationId,
                    confirm: true,
                    company_ids: selectedCompanyIds,
                });
                if (r && r.ok) {
                    settle("✅ Done. The action was carried out.");
                } else {
                    settled = true;
                    okBtn.disabled = false;
                    cancelBtn.disabled = false;
                    okBtn.innerHTML = '<i class="fa fa-check"></i> ' + esc(_t("Confirm"));
                    settled = false;
                    appendMessage("bot",
                        "❌ " + ((r && r.error) || _t("The action failed.")),
                        true, new Date());
                }
            } catch (err) {
                okBtn.disabled = false;
                cancelBtn.disabled = false;
                okBtn.innerHTML = '<i class="fa fa-check"></i> ' + esc(_t("Confirm"));
                appendMessage("bot",
                    "❌ " + _t("Network error: %s", err.message), true, new Date());
            }
        });

        actions.appendChild(cancelBtn);
        actions.appendChild(okBtn);
        card.appendChild(actions);

        inner.appendChild(card);

        const timeEl = document.createElement("span");
        timeEl.className = "dai-chat-msg-time";
        timeEl.textContent = _formatTimestamp(ts || new Date());
        inner.appendChild(timeEl);

        wrap.appendChild(avatar);
        wrap.appendChild(inner);
        messagesEl.appendChild(wrap);
        scrollBottom();
    }

    function appendMessage(who, text, isError = false, ts = null, opts = {}) {
        const wrap = document.createElement("div");
        wrap.className = `dai-chat-msg dai-chat-msg--${who}`;

        const avatar = document.createElement("div");
        avatar.className = "dai-chat-avatar";
        // Bot answers are branded with the DIGI-ERP logo (not a generic
        // robot glyph). object-fit keeps the portrait logo un-stretched
        // inside the round avatar. The user keeps a simple person icon.
        avatar.innerHTML = who === "bot"
            ? '<img class="dai-chat-avatar-logo" '
              + 'src="/digi_erp_ai_solution/static/src/img/digi-erp.png" '
              + 'alt="DIGI-ERP AI"/>'
            : '<i class="fa fa-user"></i>';

        const bubble = document.createElement("div");
        bubble.className = "dai-chat-bubble" + (isError ? " dai-chat-bubble--error" : "");
        if (who === "bot" && !isError) {
            bubble.classList.add("dai-chat-md");
            if (opts.stream) {
                streamMarkdownInto(bubble, text);
            } else {
                bubble.innerHTML = renderMarkdown(text);
                if (window.DaiCharts) { window.DaiCharts.hydrateCharts(bubble); }
            }
        } else {
            bubble.innerHTML = esc(text);
        }

        // Attachment chip (preview + download) — anchored after the text.
        if (opts.attachment) {
            bubble.appendChild(renderAttachmentChip(opts.attachment));
        }

        const label = _formatTimestamp(ts || new Date());
        const timeEl = document.createElement("span");
        timeEl.className = "dai-chat-msg-time";
        timeEl.textContent = label;
        // Bot answers also say WHICH model wrote them, right after the time.
        // In Auto mode the router can pick a different model per message, so
        // this is the only way the user can tell who actually answered.
        if (who === "bot" && !isError && opts.modelLabel) {
            const modelEl = document.createElement("span");
            modelEl.className = "dai-chat-msg-model";
            modelEl.title = _t("Answered by %s", opts.modelLabel);
            modelEl.innerHTML =
                '<i class="fa fa-microchip dai-chat-msg-model-icon"></i>'
                + `<span>${esc(opts.modelLabel)}</span>`;
            timeEl.appendChild(modelEl);
        }

        const inner = document.createElement("div");
        inner.className = "dai-chat-msg-inner";
        inner.appendChild(bubble);

        // Bot answers can carry an activity receipt, follow-up suggestion
        // chips, and a copy/regenerate action row. They live between the
        // bubble and the timestamp so they read as an "answer footer".
        const isBotAnswer = who === "bot" && !isError;
        if (isBotAnswer) {
            const receipt = renderActivityReceipt(opts.activity);
            if (receipt) inner.appendChild(receipt);

            // Actions + suggestions are attached after the (possibly
            // streamed) text is fully rendered, so a mid-stream reflow
            // never jumps the layout under the user's cursor.
            const attachFooter = () => {
                inner.appendChild(renderMessageActions(text));
                const chips = renderSuggestions(opts.suggestions);
                if (chips) inner.appendChild(chips);
                scrollBottom();
            };
            if (opts.stream) {
                // streamMarkdownInto caps its animation at ~3 s; add the
                // footer just after it settles.
                const tokens = (text || "").match(/\s+|\S+/g) || [];
                const delay = Math.min(3000, 250 + tokens.length * 18) + 120;
                setTimeout(attachFooter, delay);
            } else {
                attachFooter();
            }
        }

        inner.appendChild(timeEl);

        wrap.appendChild(avatar);
        wrap.appendChild(inner);
        messagesEl.appendChild(wrap);
        scrollBottom();
        return bubble;
    }

    /** Famille de modèle courte et lisible à partir de l'identifiant technique. */
    function modelFamily(modelName) {
        const m = (modelName || "").toLowerCase();
        if (!m) return "";
        const map = [
            ["claude", "Claude"], ["gpt", "GPT"], ["gemini", "Gemini"],
            ["qwen", "Qwen"], ["gemma", "Gemma"], ["llama", "Llama"],
            ["mistral", "Mistral"], ["grok", "Grok"], ["deepseek", "DeepSeek"],
            ["phi", "Phi"],
        ];
        for (const [needle, label] of map) {
            if (m.includes(needle)) return label;
        }
        return modelName.split(/[:@]/)[0];
    }

    /** Libellé du modèle courant, ex. « DIGI-ERP AI (Claude) », pour « … réfléchit ». */
    function currentModelLabel() {
        if (currentProviderId === "auto") return "DIGI-ERP AI (Auto)";
        const cur = providersCache.find(p => String(p.id) === String(currentProviderId));
        const family = modelFamily(cur && cur.model_name);
        return family ? `DIGI-ERP AI (${family})` : "DIGI-ERP AI";
    }

    // Phased status lines shown while a turn runs. They EVOLVE over time so
    // the wait feels like the assistant is progressing, not frozen. Kept
    // intentionally generic (we can't see the real server steps in this
    // blocking architecture) but tuned per broad intent so a data question
    // reads differently from a document one. Zero LLM cost.
    // Built on each turn, not once at load: _t must run after the
    // translations for the active language are in place.
    function _statusPhases() {
        return {
            file: [
                _t("reading the document"),
                _t("going through the content"),
                _t("structuring the answer"),
                _t("writing the answer"),
            ],
            data: [
                _t("understanding the question"),
                _t("building the Odoo query"),
                _t("querying the database"),
                _t("formatting the results"),
            ],
            default: [
                _t("thinking"),
                _t("gathering the context"),
                _t("writing the answer"),
            ],
        };
    }

    function _guessStatusPhase(prompt, hasFile) {
        if (hasFile) return "file";
        const p = (prompt || "").toLowerCase();
        if (/\b(how many|list|show|display|export|count|total|sum|customers?|invoices?|orders?|projects?|employees?|combien|liste|montre|affiche|exporte|clients?|factures?|commandes?|projets?|employ|somme|nombre)\b/.test(p)) {
            return "data";
        }
        return "default";
    }

    function _stopStatusCycle() {
        if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
    }

    function _startStatusCycle(prompt, hasFile) {
        if (!typingTextEl) return;
        const phases = _statusPhases()[_guessStatusPhase(prompt, hasFile)];
        const label = currentModelLabel();
        let i = 0;
        const paint = () => {
            typingTextEl.textContent = `${label} ${phases[i]}…`;
        };
        paint();
        _stopStatusCycle();
        // Advance ~every 2.2 s, holding on the final phase (don't loop back
        // to the start — that would read as "stuck").
        statusTimer = setInterval(() => {
            if (i < phases.length - 1) { i += 1; paint(); }
            else { _stopStatusCycle(); }
        }, 2200);
    }

    /** Active / désactive les contrôles pendant l'envoi.
     *  `opts` = {prompt, hasFile} feed the phased status label. */
    function setLoading(loading, opts = {}) {
        inputEl.disabled    = loading;
        if (attachBtn) attachBtn.disabled = loading;
        // While loading, the send button becomes a Stop button that aborts
        // the in-flight turn. It must stay enabled for that.
        sendBtn.disabled = false;
        sendBtn.classList.toggle("dai-chat-send-btn--stop", loading);
        sendBtn.title = loading ? _t("Stop") : _t("Send");
        sendBtn.innerHTML = loading
            ? '<i class="fa fa-stop"></i>'
            : '<i class="fa fa-paper-plane"></i>';
        if (loading) {
            _startStatusCycle(opts.prompt, opts.hasFile);
        } else {
            _stopStatusCycle();
        }
        typingEl.classList.toggle("d-none", !loading);
        if (loading) scrollBottom();
    }

    // ── Pièce jointe ────────────────────────────────────────────────────────

    function formatBytes(b) {
        if (b < 1024) return b + " o";
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " Ko";
        return (b / 1024 / 1024).toFixed(2) + " Mo";
    }

    function showFileChip(file) {
        fileNameEl.textContent = file.name;
        fileSizeEl.textContent = "(" + formatBytes(file.size) + ")";
        fileChipEl.classList.remove("d-none");
        inputEl.placeholder = _t("Ask a question about the document… (or leave empty for a summary)");
    }

    function clearFileChip() {
        attachedFile = null;
        if (fileInputEl) fileInputEl.value = "";
        fileChipEl.classList.add("d-none");
        fileNameEl.textContent = "";
        fileSizeEl.textContent = "";
        inputEl.placeholder = _t("Ask your question… (Enter to send, Shift+Enter for a new line)");
    }

    function isAllowed(name) {
        const lower = (name || "").toLowerCase();
        return ALLOWED_EXT.some(ext => lower.endsWith(ext));
    }

    // ── Envoi de message ─────────────────────────────────────────────────────

    async function sendMessage() {
        // If a turn is already running, the send button is a Stop button.
        if (currentAbort) { stopGeneration(); return; }

        const prompt = inputEl.value.trim();
        if (!prompt && !attachedFile) return;

        const hasFile = !!attachedFile;
        const fileForRequest = attachedFile;
        // Remember the text prompt for the "Regénérer" action.
        if (prompt) lastUserPrompt = prompt;

        // 1. Affiche le message utilisateur. Quand il y a une pièce
        //    jointe, on rend la puce (vignette + Open / Download) avec
        //    une URL blob locale pour un aperçu instantané. Quand la
        //    conversation est rechargée plus tard, le serveur fournira
        //    l'URL /web/content/<id> persistante.
        const sendOpts = {};
        if (hasFile) {
            sendOpts.attachment = {
                name: fileForRequest.name,
                size: fileForRequest.size,
                mimetype: fileForRequest.type || "",
                url: URL.createObjectURL(fileForRequest),
                download_url: URL.createObjectURL(fileForRequest),
            };
        }
        appendMessage("user", prompt, false, null, sendOpts);
        inputEl.value = "";
        inputEl.style.height = "auto";
        clearFileChip();

        // 1b. Make sure the conversation EXISTS before we send, so we hold its
        //     id up-front. Without this, a brand-new thread has no id until
        //     /ask returns — and the Stop button couldn't tell the server
        //     which turn to cancel on that very first message. Silent: no
        //     banner, no clearing (unlike the "New conversation" button).
        if (!currentConversationId) {
            try {
                const c = await rpc("/ai-solution/conversations/new", {});
                if (c && c.ok && c.conversation) {
                    currentConversationId = c.conversation.id;
                }
            } catch (_) {
                // Fail open: fall back to server-side auto-create in /ask.
                // Stop just won't reach the backend for this first message.
            }
        }

        // 2. Active l'indicateur de chargement (statut évolutif) + abort.
        setLoading(true, { prompt, hasFile });
        currentAbort = new AbortController();

        try {
            let data;

            let res;
            if (hasFile) {
                const fd = new FormData();
                // This is the one endpoint that is type='http' rather than
                // jsonrpc, so Odoo's HttpDispatcher DOES enforce CSRF on it
                // (the jsonrpc routes never reach that check, which is why
                // csrf=False is a no-op there and load-bearing here).
                // `odoo.csrf_token` is defined by web.layout, which this page
                // renders through.
                fd.append("csrf_token", odoo.csrf_token);
                fd.append("prompt", prompt);
                if (currentConversationId) fd.append("conversation_id", currentConversationId);
                if (currentProviderId) fd.append("provider_id", currentProviderId);
                if (selectedCompanyIds.length) {
                    fd.append("company_ids", selectedCompanyIds.join(","));
                }
                fd.append("file", fileForRequest, fileForRequest.name);
                res = await fetch("/ai-solution/ask-with-file",
                                  { method: "POST", body: fd, signal: currentAbort.signal });
            } else {
                res = await fetch("/ai-solution/ask", {
                    method:  "POST",
                    headers: { "Content-Type": "application/json" },
                    signal:  currentAbort.signal,
                    body:    JSON.stringify({
                        jsonrpc: "2.0",
                        method:  "call",
                        id:      Date.now(),
                        params:  {
                            prompt,
                            conversation_id: currentConversationId,
                            provider_id: currentProviderId,
                            company_ids: selectedCompanyIds,
                        },
                    }),
                });
            }

            if (res.redirected || res.status === 401 || res.status === 403) {
                setLoading(false);
                currentAbort = null;
                appendMessage(
                    "bot",
                    "❌ " + _t("You must be logged in to use the chat.") + " "
                    + "[Se connecter](/web/login)",
                    true,
                );
                return;
            }

            const json = await res.json();
            data = json.result || json;
            setLoading(false);
            currentAbort = null;

            // Le serveur peut nous avoir créé la conversation à la volée.
            if (data.conversation_id) {
                const wasNew = !currentConversationId;
                currentConversationId = data.conversation_id;
                if (wasNew) {
                    // Recharger la sidebar pour voir la nouvelle conv (et la marquer active).
                    refreshConversations(currentConversationId);
                } else {
                    // Met à jour le nom et l'ordre.
                    refreshConversations(currentConversationId);
                }
            }

            const botTs = new Date();
            if (data.ok && data.pending_action) {
                // The assistant wants to WRITE something — show its intro
                // sentence (if any) then a Confirm/Cancel review card. Nothing
                // is written until the user confirms.
                const intro = (data.response || "").trim();
                if (intro) {
                    appendMessage("bot", intro, false, botTs, { stream: false });
                }
                appendPendingAction(data.pending_action, botTs);
            } else if (data.ok) {
                // The server now downgrades empty content to ok:false with
                // a diagnostic, so reaching here with an empty `response`
                // means a bug, not a normal flow. Still guard with a
                // meaningful safety-net message — never "(réponse vide)".
                const text = (data.response || "").trim();
                if (text) {
                    appendMessage("bot", text, false, botTs, {
                        stream: true,
                        activity: data.activity,
                        suggestions: data.suggestions,
                        modelLabel: data.model_label,
                    });
                    // First answer in a fresh thread → ask the server for a
                    // short smart title, then refresh the sidebar. Async,
                    // fail-open: never blocks or breaks the reply.
                    if (data.needs_title && currentConversationId) {
                        autotitleConversation(currentConversationId);
                    }
                } else {
                    appendMessage(
                        "bot",
                        "❌ " + _t("The server returned an answer with no content "
                        + "and no diagnostic. Check the Odoo log "
                        + "(Settings → Technical → Logs) and the state of your "
                        + "AI server, then try again."),
                        true,
                        botTs,
                    );
                }
            } else {
                appendMessage("bot", "❌ " + (data.error || _t("Unknown error.")), true, botTs);
            }

        } catch (err) {
            setLoading(false);
            const wasAbort = currentAbort === null || err.name === "AbortError";
            currentAbort = null;
            if (err.name === "AbortError") {
                // User pressed Stop — acknowledge quietly, no error styling.
                appendMessage("bot", "⏹️ " + _t("Generation interrupted."), false, new Date());
            } else if (!wasAbort) {
                appendMessage("bot", "❌ " + _t("Network error: %s", err.message), true, new Date());
            }
        }
    }

    /** Abort the in-flight turn (Stop button).
     *  Two things must happen:
     *   1. Abort the browser fetch — stops us WAITING for the answer.
     *   2. Tell the SERVER to stop, via POST /ai-solution/cancel. The
     *      server-side turn runs in its own worker and, without this,
     *      keeps the model generating to the end even though the browser
     *      hung up. The cancel note makes the running turn hang up on
     *      the server, which actually stops the model. */
    function stopGeneration() {
        // 2. Server-side cancel FIRST — fire it before the abort so the note
        //    is on its way even if the abort's catch runs immediately. Best
        //    effort: a failure here still leaves the browser abort in place.
        if (currentConversationId) {
            rpc("/ai-solution/cancel", {
                conversation_id: currentConversationId,
            }).catch(function () { /* best effort */ });
        }
        // 1. Abort the browser request.
        if (currentAbort) {
            try { currentAbort.abort(); } catch (_) {}
        }
    }

    /**
     * Ask the server for a short smart title for a freshly-started thread,
     * then refresh the sidebar so the new name appears. Fully fail-open —
     * a failure just leaves the existing (first-message) name in place.
     */
    async function autotitleConversation(convId) {
        try {
            const data = await rpc(
                `/ai-solution/conversations/${convId}/autotitle`, {});
            if (data && data.ok) {
                refreshConversations(currentConversationId);
            }
        } catch (_) {
            // Silent: title stays as the truncated first message.
        }
    }

    // ── Gestion auto-resize du textarea ──────────────────────────────────────
    inputEl.addEventListener("input", function () {
        this.style.height = "auto";
        this.style.height = Math.min(this.scrollHeight, 160) + "px";
    });

    // ── Entrée = envoyer, Maj+Entrée = saut de ligne ─────────────────────────
    inputEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ── Bouton envoyer ────────────────────────────────────────────────────────
    sendBtn.addEventListener("click", sendMessage);

    // ── Bouton effacer ────────────────────────────────────────────────────────
    // Démarre une nouvelle conversation côté serveur — l'historique courant
    // reste dans la sidebar, l'utilisateur peut y revenir.
    clearBtn.addEventListener("click", function () {
        inputEl.value = "";
        inputEl.style.height = "auto";
        clearFileChip();
        newConversation();
    });

    // ── Pièce jointe : ouvrir le sélecteur ───────────────────────────────────
    if (attachBtn && fileInputEl) {
        attachBtn.addEventListener("click", function () {
            fileInputEl.click();
        });

        fileInputEl.addEventListener("change", function () {
            const file = this.files && this.files[0];
            if (!file) return;

            if (!isAllowed(file.name)) {
                appendMessage(
                    "bot",
                    "❌ " + _t("Unsupported file type. Accepted formats: %s",
                                    ALLOWED_EXT.join(", ")),
                    true
                );
                this.value = "";
                return;
            }
            if (file.size > MAX_FILE_BYTES) {
                appendMessage(
                    "bot",
                    "❌ " + _t("File too large (%(size)s). Maximum: %(max)s.", { size: formatBytes(file.size), max: formatBytes(MAX_FILE_BYTES) }),
                    true
                );
                this.value = "";
                return;
            }

            attachedFile = file;
            showFileChip(file);
            inputEl.focus();
        });
    }

    // ── Pièce jointe : retirer ───────────────────────────────────────────────
    if (fileRemoveEl) {
        fileRemoveEl.addEventListener("click", function () {
            clearFileChip();
            inputEl.focus();
        });
    }

    // ── Drag & drop sur la zone de messages ──────────────────────────────────
    ["dragover", "dragenter"].forEach(ev =>
        messagesEl.addEventListener(ev, e => {
            e.preventDefault();
            messagesEl.classList.add("dai-chat-dragover");
        })
    );
    ["dragleave", "dragend", "drop"].forEach(ev =>
        messagesEl.addEventListener(ev, e => {
            e.preventDefault();
            messagesEl.classList.remove("dai-chat-dragover");
        })
    );
    messagesEl.addEventListener("drop", function (e) {
        const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (!file) return;
        if (!isAllowed(file.name)) {
            appendMessage("bot",
                "❌ " + _t("Unsupported file type. Accepted formats: %s", ALLOWED_EXT.join(", ")),
                true);
            return;
        }
        if (file.size > MAX_FILE_BYTES) {
            appendMessage("bot",
                "❌ " + _t("File too large (%(size)s). Maximum: %(max)s.", { size: formatBytes(file.size), max: formatBytes(MAX_FILE_BYTES) }),
                true);
            return;
        }
        attachedFile = file;
        showFileChip(file);
        inputEl.focus();
    });

    // ── Sidebar conversations ────────────────────────────────────────────────

    function clearMessagesArea() {
        // Vide la zone de messages, sans bulle de bienvenue.
        messagesEl.innerHTML = "";
    }

    function renderConvList() {
        if (!convListEl) return;
        convListEl.innerHTML = "";
        if (!conversations.length) {
            const li = document.createElement("li");
            li.className = "dai-chat-conv-item text-muted";
            li.textContent = _t("No conversation yet");
            convListEl.appendChild(li);
            return;
        }
        conversations.forEach(c => {
            const li = document.createElement("li");
            li.className = "dai-chat-conv-item";
            if (c.id === currentConversationId) li.classList.add("dai-chat-conv-active");
            li.dataset.id = c.id;

            const name = document.createElement("span");
            name.className = "dai-chat-conv-name";
            name.textContent = c.name || `Conversation #${c.id}`;
            li.appendChild(name);

            const actions = document.createElement("span");
            actions.className = "dai-chat-conv-actions";

            const renameBtn = document.createElement("button");
            renameBtn.type = "button";
            renameBtn.className = "btn btn-link p-0";
            renameBtn.title = _t("Rename");
            renameBtn.innerHTML = '<i class="fa fa-pencil"></i>';
            renameBtn.addEventListener("click", e => {
                e.stopPropagation();
                renameConversation(c);
            });
            actions.appendChild(renameBtn);

            const delBtn = document.createElement("button");
            delBtn.type = "button";
            delBtn.className = "btn btn-link p-0";
            delBtn.title = _t("Delete");
            delBtn.innerHTML = '<i class="fa fa-trash"></i>';
            delBtn.addEventListener("click", e => {
                e.stopPropagation();
                deleteConversation(c);
            });
            actions.appendChild(delBtn);

            li.appendChild(actions);
            li.addEventListener("click", () => loadConversation(c.id));
            convListEl.appendChild(li);
        });
    }

    async function refreshConversations(activateId) {
        try {
            const data = await rpc("/ai-solution/conversations", {});
            if (data.ok) {
                conversations = data.conversations || [];
                if (activateId) currentConversationId = activateId;
                renderConvList();
            }
        } catch (err) {
            if (err.message === "AUTH_REQUIRED") {
                appendMessage("bot",
                    "❌ " + _t("You must be logged in.") + " [" + _t("Log in") + "](/web/login)",
                    true);
            }
        }
    }

    async function loadConversation(id) {
        try {
            const data = await rpc(`/ai-solution/conversations/${id}/messages`, {});
            if (!data.ok) {
                appendMessage("bot", "❌ " + (data.error || _t("Error")), true);
                return;
            }
            currentConversationId = id;
            clearMessagesArea();
            (data.messages || []).forEach(m => {
                if (m.role === "user") {
                    const loadOpts = {};
                    if (m.attachment_id || m.attachment_filename) {
                        loadOpts.attachment = {
                            name: m.attachment_filename || _t("file"),
                            mimetype: m.attachment_mimetype || "",
                            size: m.attachment_size || 0,
                            url: m.attachment_url || null,
                            download_url: m.attachment_download_url || null,
                        };
                    }
                    appendMessage("user", m.content || "",
                                  false, m.create_date, loadOpts);
                } else if (m.role === "assistant") {
                    // Replay the persisted activity receipt (m.activity) so
                    // the "what I did" chip survives a page reload. No
                    // suggestions on reload — they're only actionable on the
                    // latest turn.
                    appendMessage("bot", m.content || "", false, m.create_date,
                                  { activity: m.activity,
                                    modelLabel: m.model_label });
                }
                // tool_call / tool_result : non rendus dans l'UI (gardés en DB pour traçabilité)
            });
            renderConvList(); // refresh which entry is highlighted
            inputEl.focus();
        } catch (err) {
            if (err.message === "AUTH_REQUIRED") {
                appendMessage("bot",
                    "❌ " + _t("You must be logged in.") + " [" + _t("Log in") + "](/web/login)",
                    true);
            }
        }
    }

    async function newConversation() {
        try {
            const data = await rpc("/ai-solution/conversations/new", {});
            if (!data.ok) return;
            currentConversationId = data.conversation.id;
            clearMessagesArea();
            appendMessage("bot",
                _t("New conversation. Ask anything about Odoo — DIGI-ERP AI "
                   + "builds the query and Odoo answers within your own "
                   + "access rights."));
            await refreshConversations(currentConversationId);
            inputEl.focus();
        } catch (err) {
            if (err.message === "AUTH_REQUIRED") {
                appendMessage("bot",
                    "❌ " + _t("You must be logged in.") + " [" + _t("Log in") + "](/web/login)",
                    true);
            }
        }
    }

    async function renameConversation(conv) {
        const newName = window.prompt(_t("Rename conversation:"), conv.name || "");
        if (!newName || newName.trim() === conv.name) return;
        const data = await rpc(`/ai-solution/conversations/${conv.id}/rename`,
                               { name: newName.trim() });
        if (data.ok) refreshConversations(currentConversationId);
    }

    async function deleteConversation(conv) {
        if (!window.confirm(_t("Delete the conversation “%s”?", conv.name))) return;
        const data = await rpc(`/ai-solution/conversations/${conv.id}/delete`, {});
        if (!data.ok) return;
        if (currentConversationId === conv.id) {
            currentConversationId = null;
            clearMessagesArea();
        }
        refreshConversations(currentConversationId);
    }

    if (newConvBtn) {
        newConvBtn.addEventListener("click", newConversation);
    }

    // ── Custom LLM model picker (chat topbar) ───────────────────────────────
    const PROVIDER_LABELS = {
        local: "Self-hosted",
        openai: "OpenAI",
        anthropic: "Anthropic · Claude",
        gemini: "Google Gemini",
        xai: "xAI · Grok",
        openai_compat: "OpenAI-compatible",
    };
    const PROVIDER_ICONS = {
        local: "fa-bolt",
        openai: "fa-cube",
        anthropic: "fa-feather",
        gemini: "fa-gem",
        xai: "fa-rocket",
        openai_compat: "fa-plug",
    };

    let providersCache = [];

    function setPickerOpen(open) {
        if (!pickerEl || !pickerMenu) return;
        if (open) {
            pickerMenu.removeAttribute("hidden");
            pickerEl.dataset.open = "1";
            if (pickerTrigger) pickerTrigger.setAttribute("aria-expanded", "true");
        } else {
            pickerMenu.setAttribute("hidden", "hidden");
            delete pickerEl.dataset.open;
            if (pickerTrigger) pickerTrigger.setAttribute("aria-expanded", "false");
        }
    }

    // True if the user has at least one provider with auto_route_tier set.
    // Used to gate the Auto-mode entry in the picker — showing it when
    // there's nothing to route to would be confusing.
    function hasAnyAutoTierProvider() {
        return providersCache.some(p => p.auto_route_tier);
    }

    function renderPickerTrigger() {
        if (!pickerText || !pickerEl) return;
        // Empty list → show a "no models" placeholder; chat still works
        // (backend uses Settings → DIGI-ERP AI defaults).
        if (!providersCache.length) {
            pickerText.textContent = _t("No model configured");
            pickerEl.setAttribute("data-current", "local");
            return;
        }
        // Auto mode is a sentinel string, not a record id.
        if (currentProviderId === "auto") {
            pickerText.textContent = _t("Auto");
            pickerEl.setAttribute("data-current", "auto");
            return;
        }
        const cur = providersCache.find(p => String(p.id) === String(currentProviderId))
                  || providersCache[0];
        if (!cur) return;
        pickerText.textContent = cur.name;
        // For colour theming: local providers use the 'local' palette,
        // remote ones use the API-specific colour.
        const themeKey = cur.is_local ? "local" : (cur.provider_type || "local");
        pickerEl.setAttribute("data-current", themeKey);
    }

    function renderPickerMenu() {
        if (!pickerMenu) return;
        const list = pickerMenu.querySelector(".dai-chat-model-list");
        if (!list) return;
        list.innerHTML = "";

        if (!providersCache.length) {
            const empty = document.createElement("div");
            empty.className = "dai-chat-model-item";
            empty.style.cursor = "default";
            empty.innerHTML = `
                <span class="dai-chat-model-item-body">
                    <span class="dai-chat-model-item-name">${esc(_t("No models yet"))}</span>
                    <span class="dai-chat-model-item-meta">
                        ${esc(_t("Add one in Odoo: DIGI-ERP AI → Configuration → AI Models"))}
                    </span>
                </span>
            `;
            list.appendChild(empty);
            return;
        }

        // Auto entry — only shown when at least one provider has a
        // tier configured. Picking it sends "auto" as provider_id;
        // the backend classifies each prompt and routes it.
        if (hasAnyAutoTierProvider()) {
            const autoItem = document.createElement("div");
            autoItem.className = "dai-chat-model-item";
            autoItem.setAttribute("role", "option");
            autoItem.setAttribute("data-id", "auto");
            autoItem.setAttribute("data-type", "auto");
            if (currentProviderId === "auto") {
                autoItem.setAttribute("aria-selected", "true");
            }
            autoItem.innerHTML = `
                <span class="dai-chat-model-item-body">
                    <span class="dai-chat-model-item-name">
                        <i class="fa fa-magic me-1"></i> ${esc(_t("Auto"))}
                    </span>
                    <span class="dai-chat-model-item-meta">
                        ${esc(_t("Picks the right model for each question"))}
                    </span>
                </span>
                <span class="dai-chat-model-item-check"><i class="fa fa-check"></i></span>
            `;
            autoItem.addEventListener("click", () => {
                currentProviderId = "auto";
                try { localStorage.setItem("daiProvider", "auto"); } catch (_) {}
                renderPickerTrigger();
                renderPickerMenu();
                setPickerOpen(false);
            });
            list.appendChild(autoItem);
        }

        providersCache.forEach((p) => {
            const themeKey = p.is_local ? "local" : (p.provider_type || "local");
            const item = document.createElement("div");
            item.className = "dai-chat-model-item";
            item.setAttribute("role", "option");
            item.setAttribute("data-id", String(p.id));
            item.setAttribute("data-type", themeKey);
            if (String(p.id) === String(currentProviderId)) {
                item.setAttribute("aria-selected", "true");
            }
            const typeLabel = p.is_local
                ? _t("Self-hosted")
                : (PROVIDER_LABELS[p.provider_type] || p.provider_type);
            const meta = p.model_name
                ? `${esc(typeLabel)} · ${esc(p.model_name)}`
                : esc(typeLabel);
            item.innerHTML = `
                <span class="dai-chat-model-item-body">
                    <span class="dai-chat-model-item-name">${esc(p.name)}</span>
                    <span class="dai-chat-model-item-meta">${meta}</span>
                </span>
                <span class="dai-chat-model-item-check"><i class="fa fa-check"></i></span>
            `;
            item.addEventListener("click", () => {
                currentProviderId = String(p.id);
                try { localStorage.setItem("daiProvider", currentProviderId); } catch (_) {}
                renderPickerTrigger();
                renderPickerMenu();
                setPickerOpen(false);
            });
            list.appendChild(item);
        });
    }

    async function loadProviders() {
        if (!pickerEl) return;
        try {
            const data = await rpc("/ai-solution/providers", {});
            if (!data.ok || !Array.isArray(data.providers)) return;
            providersCache = data.providers;

            // Pick (in order): saved choice (if still valid) → default
            // provider → first provider → empty (= use global settings).
            // Pick (in order): saved choice if still valid → first provider
            // in the list → empty (= use global settings).
            let chosen = localStorage.getItem("daiProvider") || "";
            // "auto" is a valid sentinel — only kill it if no tier'd
            // providers exist any more (e.g. all tiers were cleared).
            if (chosen === "auto" && !hasAnyAutoTierProvider()) {
                chosen = "";
            }
            if (chosen && chosen !== "auto"
                && !providersCache.some(p => String(p.id) === chosen)) {
                chosen = "";
            }
            if (!chosen) {
                chosen = hasAnyAutoTierProvider()
                    ? "auto"
                    : (providersCache.length ? String(providersCache[0].id) : "");
            }
            currentProviderId = chosen;

            renderPickerTrigger();
            renderPickerMenu();
        } catch (err) {
            // Auth or network error — leave currentProviderId empty so the
            // backend falls back to global Settings.
        }
    }

    if (pickerTrigger) {
        pickerTrigger.addEventListener("click", (ev) => {
            ev.stopPropagation();
            const isOpen = !!(pickerEl && pickerEl.dataset.open);
            setPickerOpen(!isOpen);
        });
        document.addEventListener("click", (ev) => {
            if (pickerEl && !pickerEl.contains(ev.target)) setPickerOpen(false);
        });
        document.addEventListener("keydown", (ev) => {
            if (ev.key === "Escape") setPickerOpen(false);
        });
    }

    // ── Company picker (multi-company scope) ────────────────────────────────
    // Lets the user choose which of their allowed companies the assistant's
    // data tools should span. Selection is sent as `company_ids` with every
    // ask; the backend validates it against the user's real allowed set and
    // defaults to ALL of them when nothing is selected.
    const companyPickerEl = document.getElementById("dai-chat-company-picker");
    const companyTrigger  = companyPickerEl && companyPickerEl.querySelector(".dai-chat-company-trigger");
    const companyTextEl   = companyPickerEl && companyPickerEl.querySelector(".dai-chat-company-trigger-text");
    const companyMenuEl   = companyPickerEl && companyPickerEl.querySelector(".dai-chat-company-menu");
    const companyListEl   = companyPickerEl && companyPickerEl.querySelector(".dai-chat-company-list");
    const companyAllBtn   = companyPickerEl && companyPickerEl.querySelector(".dai-chat-company-all");

    function setCompanyMenuOpen(open) {
        if (!companyPickerEl || !companyMenuEl) return;
        if (open) {
            companyMenuEl.removeAttribute("hidden");
            companyPickerEl.dataset.open = "1";
            companyTrigger && companyTrigger.setAttribute("aria-expanded", "true");
        } else {
            companyMenuEl.setAttribute("hidden", "hidden");
            delete companyPickerEl.dataset.open;
            companyTrigger && companyTrigger.setAttribute("aria-expanded", "false");
        }
    }

    function renderCompanyTrigger() {
        if (!companyTextEl) return;
        const total = companiesCache.length;
        const n = selectedCompanyIds.length;
        if (n === 0 || n === total) {
            companyTextEl.textContent = _t("All companies");
        } else if (n === 1) {
            const c = companiesCache.find(c => c.id === selectedCompanyIds[0]);
            companyTextEl.textContent = c ? c.name : _t("1 company");
        } else {
            companyTextEl.textContent = _t("%s companies", n);
        }
    }

    function persistCompanySelection() {
        try {
            localStorage.setItem("aiCompanyIds", JSON.stringify(selectedCompanyIds));
        } catch (_) {}
    }

    function renderCompanyList() {
        if (!companyListEl) return;
        companyListEl.innerHTML = "";
        companiesCache.forEach(c => {
            const item = document.createElement("button");
            item.type = "button";
            item.className = "dai-chat-company-item";
            item.setAttribute("role", "option");
            item.dataset.id = String(c.id);
            item.innerHTML =
                '<span class="dai-chat-company-check"></span>'
                + `<span class="dai-chat-company-name">${esc(c.name)}</span>`;
            item.addEventListener("click", (ev) => {
                // Keep the menu open: toggle in place, never re-render the list.
                ev.stopPropagation();
                const i = selectedCompanyIds.indexOf(c.id);
                if (i >= 0) {
                    // Don't allow deselecting the last one — keep ≥1 active.
                    if (selectedCompanyIds.length > 1) selectedCompanyIds.splice(i, 1);
                } else {
                    selectedCompanyIds.push(c.id);
                }
                persistCompanySelection();
                syncCompanyRows();
                renderCompanyTrigger();
            });
            companyListEl.appendChild(item);
        });
        syncCompanyRows();
    }

    /** Reflect `selectedCompanyIds` onto the existing rows + footer WITHOUT
     *  rebuilding the DOM, so a toggle never closes the open menu. */
    function syncCompanyRows() {
        if (!companyListEl) return;
        companyListEl.querySelectorAll(".dai-chat-company-item").forEach(row => {
            const on = selectedCompanyIds.includes(parseInt(row.dataset.id, 10));
            row.classList.toggle("is-on", on);
            row.setAttribute("aria-selected", String(on));
        });
        if (companyAllBtn) {
            const allOn = selectedCompanyIds.length >= companiesCache.length;
            companyAllBtn.textContent = allOn
                ? "Deselect all" : "Select all";
            companyAllBtn.dataset.mode = allOn ? "clear" : "all";
        }
    }

    async function loadCompanies() {
        if (!companyPickerEl) return;
        try {
            const data = await rpc("/ai-solution/companies", {});
            if (!data.ok || !Array.isArray(data.companies)) return;
            companiesCache = data.companies;

            // Single-company user → the picker adds nothing; keep it hidden and
            // send nothing (backend defaults to their one company anyway).
            if (!data.multi_company) {
                companyPickerEl.classList.add("d-none");
                selectedCompanyIds = [];
                return;
            }
            companyPickerEl.classList.remove("d-none");

            // Restore a saved selection (kept only if still valid), else start
            // with ALL allowed companies selected — the assistant spans them all.
            let saved = [];
            try { saved = JSON.parse(localStorage.getItem("aiCompanyIds") || "[]"); }
            catch (_) { saved = []; }
            const validIds = companiesCache.map(c => c.id);
            saved = Array.isArray(saved) ? saved.filter(id => validIds.includes(id)) : [];
            selectedCompanyIds = saved.length ? saved : validIds.slice();

            renderCompanyList();
            renderCompanyTrigger();
        } catch (_) {
            // Auth/network — leave hidden; backend defaults to all allowed.
        }
    }

    if (companyTrigger) {
        companyTrigger.addEventListener("click", (ev) => {
            ev.stopPropagation();
            setCompanyMenuOpen(!(companyPickerEl && companyPickerEl.dataset.open));
        });
        document.addEventListener("click", (ev) => {
            if (companyPickerEl && !companyPickerEl.contains(ev.target)) setCompanyMenuOpen(false);
        });
        document.addEventListener("keydown", (ev) => {
            if (ev.key === "Escape") setCompanyMenuOpen(false);
        });
    }
    if (companyAllBtn) {
        companyAllBtn.addEventListener("click", (ev) => {
            ev.stopPropagation();          // keep the menu open
            if (companyAllBtn.dataset.mode === "clear") {
                // Can't have zero active — reduce to just the first company.
                selectedCompanyIds = companiesCache.length
                    ? [companiesCache[0].id] : [];
            } else {
                selectedCompanyIds = companiesCache.map(c => c.id);
            }
            persistCompanySelection();
            syncCompanyRows();
            renderCompanyTrigger();
        });
    }

    // ── Bootstrap : charger les conversations, ouvrir la plus récente ───────
    (async function init() {
        // Provider list + companies run in parallel with the conversation load.
        loadProviders();
        loadCompanies();
        try {
            const data = await rpc("/ai-solution/conversations", {});
            if (!data.ok) return;
            conversations = data.conversations || [];
            if (conversations.length) {
                await loadConversation(conversations[0].id);
            } else {
                renderConvList();
            }
        } catch (err) {
            if (err.message === "AUTH_REQUIRED") {
                appendMessage("bot",
                    "❌ " + _t("You must be logged in to use the chat.") + " "
                    + "[Se connecter](/web/login)",
                    true);
            }
        }
    })();

    // Focus initial
    inputEl.focus();

})();
