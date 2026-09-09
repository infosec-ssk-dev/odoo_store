/**
 * DIGI-ERP AI — inline canvas charts for the chat.
 *
 * The model emits a fenced ```chart block containing JSON:
 *   {
 *     "type":   "bar" | "line" | "pie" | "doughnut",
 *     "title":  "Ventes 2025 par mois",        // optional
 *     "labels": ["Jan","Fév","Mar", ...],
 *     "series": [{"name":"CA","data":[14300, 11100, ...]}],  // 1..n series
 *     "unit":   "€"                            // optional suffix on values
 *   }
 *
 * The markdown renderers turn that block into a
 *   <div class="dai-chart" data-chart="<base64 json>"></div>
 * placeholder (a string — no canvas drawing happens during innerHTML).
 * After the final HTML is in the DOM, call `hydrateCharts(container)` to
 * find those placeholders and draw a real, themed <canvas> into each.
 *
 * Plain IIFE (NOT an @odoo-module) so the SAME file loads in both the
 * frontend bundle (chat.js, a classic IIFE) and the backend bundle
 * (embedded_chat.js, an ES module). It publishes two functions on
 * `window.DaiCharts`; both chat surfaces read them from there. No external
 * library — a compact hand-rolled 2D renderer keeps the design in our hands.
 */
(function () {
"use strict";

// Brand palette — navy-led, matches the PDF stylesheet (#2F5496).
const DAI_PALETTE = [
    "#2F5496", "#4472C4", "#5B9BD5", "#70AD47", "#FFC000",
    "#ED7D31", "#C00000", "#7030A0", "#00B0F0", "#264478",
];

function _themeColors() {
    // Read the effective text colour from the document so charts adapt to
    // Odoo light/dark without hard-coding. Fallbacks are the light theme.
    let axis = "#5b6b7c";
    let grid = "rgba(120,130,145,0.18)";
    let ink = "#1c2733";
    try {
        const probe = getComputedStyle(document.documentElement);
        const c = probe.getPropertyValue("--dai-chart-ink").trim();
        if (c) {
            ink = c;
        }
    } catch (_e) { /* non-DOM context — keep defaults */ }
    return { axis, grid, ink };
}

// ── Placeholder builder (string) ─────────────────────────────────────────────
// Called from renderMarkdown. Validates the JSON minimally and returns a safe
// HTML string; on malformed JSON returns null so the caller can fall back to a
// normal code block.
function chartPlaceholder(rawJson) {
    let spec;
    try {
        spec = JSON.parse(rawJson);
    } catch (_e) {
        return null;
    }
    if (!spec || !Array.isArray(spec.labels) || !spec.labels.length) {
        return null;
    }
    // Normalise a single "data" array into the series shape.
    if (!Array.isArray(spec.series)) {
        if (Array.isArray(spec.data)) {
            spec.series = [{ name: spec.name || "", data: spec.data }];
        } else {
            return null;
        }
    }
    const ok = spec.series.every((s) => Array.isArray(s.data) && s.data.length);
    if (!ok) {
        return null;
    }
    // base64 so the JSON survives inside an HTML attribute untouched.
    let b64;
    try {
        b64 = btoa(unescape(encodeURIComponent(JSON.stringify(spec))));
    } catch (_e) {
        return null;
    }
    return `<div class="dai-chart" data-chart="${b64}" role="img"></div>`;
}

// ── Hydration ────────────────────────────────────────────────────────────────
function hydrateCharts(container) {
    if (!container) {
        return;
    }
    const nodes = container.querySelectorAll(".dai-chart[data-chart]");
    nodes.forEach((node) => {
        if (node.dataset.daiDrawn === "1") {
            return;
        }
        let spec;
        try {
            spec = JSON.parse(decodeURIComponent(escape(atob(node.dataset.chart))));
        } catch (_e) {
            return;
        }
        node.dataset.daiDrawn = "1";
        _buildChart(node, spec);
    });
}

function _fmt(v, unit) {
    if (typeof v !== "number" || !isFinite(v)) {
        return String(v);
    }
    const s = Math.abs(v) >= 1000
        ? v.toLocaleString("fr-FR", { maximumFractionDigits: 0 })
        : v.toLocaleString("fr-FR", { maximumFractionDigits: 2 });
    return unit ? `${s} ${unit}` : s;
}

function _buildChart(node, spec) {
    const type = (spec.type || "bar").toLowerCase();
    const unit = spec.unit || "";

    // Title.
    if (spec.title) {
        const h = document.createElement("div");
        h.className = "dai-chart-title";
        h.textContent = spec.title;
        node.appendChild(h);
    }

    // Legend (multi-series or pie).
    const isCircular = type === "pie" || type === "doughnut";
    if (spec.series.length > 1 || isCircular) {
        const legend = document.createElement("div");
        legend.className = "dai-chart-legend";
        const items = isCircular ? spec.labels : spec.series.map((s) => s.name || "");
        items.forEach((label, i) => {
            const chip = document.createElement("span");
            chip.className = "dai-chart-legend-item";
            const sw = document.createElement("span");
            sw.className = "dai-chart-swatch";
            sw.style.background = DAI_PALETTE[i % DAI_PALETTE.length];
            chip.appendChild(sw);
            chip.appendChild(document.createTextNode(label));
            legend.appendChild(chip);
        });
        node.appendChild(legend);
    }

    // Canvas — HiDPI aware.
    const canvas = document.createElement("canvas");
    canvas.className = "dai-chart-canvas";
    node.appendChild(canvas);

    const draw = () => {
        const cssW = node.clientWidth || 520;
        const cssH = isCircular ? 240 : Math.min(320, Math.max(180, cssW * 0.5));
        const dpr = window.devicePixelRatio || 1;
        canvas.style.width = cssW + "px";
        canvas.style.height = cssH + "px";
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        const ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, cssW, cssH);
        const theme = _themeColors();
        if (isCircular) {
            _drawPie(ctx, cssW, cssH, spec, theme, unit, type === "doughnut");
        } else if (type === "line") {
            _drawLine(ctx, cssW, cssH, spec, theme, unit);
        } else {
            _drawBars(ctx, cssW, cssH, spec, theme, unit);
        }
    };

    draw();
    // Redraw on resize (debounced) so the chart stays crisp when the panel
    // width changes (expand/collapse the embedded sidebar).
    let raf = null;
    const onResize = () => {
        if (raf) {
            cancelAnimationFrame(raf);
        }
        raf = requestAnimationFrame(draw);
    };
    if (window.ResizeObserver) {
        const ro = new ResizeObserver(onResize);
        ro.observe(node);
    } else {
        window.addEventListener("resize", onResize);
    }
}

// ── axis helpers ─────────────────────────────────────────────────────────────
function _niceMax(v) {
    if (v <= 0) {
        return 1;
    }
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * pow;
}

function _drawGrid(ctx, x0, y0, w, h, max, theme, unit) {
    const ticks = 4;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= ticks; i++) {
        const val = (max / ticks) * i;
        const y = y0 + h - (h * i) / ticks;
        ctx.strokeStyle = theme.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(x0 + w, y);
        ctx.stroke();
        ctx.fillStyle = theme.axis;
        ctx.textAlign = "right";
        ctx.fillText(_fmt(val, unit), x0 - 8, y);
    }
}

function _drawBars(ctx, W, H, spec, theme, unit) {
    const padL = 64, padR = 12, padT = 12, padB = 34;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const labels = spec.labels;
    const series = spec.series;
    let max = 0;
    series.forEach((s) => s.data.forEach((v) => { max = Math.max(max, v || 0); }));
    max = _niceMax(max);

    _drawGrid(ctx, padL, padT, plotW, plotH, max, theme, unit);

    const groups = labels.length;
    const groupW = plotW / groups;
    const nSeries = series.length;
    const barGap = groupW * 0.18;
    const barW = (groupW - barGap) / nSeries;

    labels.forEach((label, gi) => {
        series.forEach((s, si) => {
            const v = s.data[gi] || 0;
            const bh = (v / max) * plotH;
            const x = padL + gi * groupW + barGap / 2 + si * barW;
            const y = padT + plotH - bh;
            const color = DAI_PALETTE[si % DAI_PALETTE.length];
            const grad = ctx.createLinearGradient(0, y, 0, y + bh);
            grad.addColorStop(0, color);
            grad.addColorStop(1, color + "cc");
            ctx.fillStyle = grad;
            _roundRect(ctx, x + 1, y, barW - 2, Math.max(bh, 1), 3);
            ctx.fill();
        });
        // x label
        ctx.fillStyle = theme.axis;
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(_ellipsize(label, 10), padL + gi * groupW + groupW / 2,
            padT + plotH + 8);
    });
}

function _drawLine(ctx, W, H, spec, theme, unit) {
    const padL = 64, padR = 12, padT = 12, padB = 34;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const labels = spec.labels;
    const series = spec.series;
    let max = 0;
    series.forEach((s) => s.data.forEach((v) => { max = Math.max(max, v || 0); }));
    max = _niceMax(max);

    _drawGrid(ctx, padL, padT, plotW, plotH, max, theme, unit);

    const n = labels.length;
    const stepX = n > 1 ? plotW / (n - 1) : plotW;

    series.forEach((s, si) => {
        const color = DAI_PALETTE[si % DAI_PALETTE.length];
        const pts = s.data.map((v, i) => ({
            x: padL + (n > 1 ? i * stepX : plotW / 2),
            y: padT + plotH - ((v || 0) / max) * plotH,
        }));
        // area fill
        const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
        grad.addColorStop(0, color + "44");
        grad.addColorStop(1, color + "00");
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, padT + plotH);
        pts.forEach((p) => ctx.lineTo(p.x, p.y));
        ctx.lineTo(pts[pts.length - 1].x, padT + plotH);
        ctx.closePath();
        ctx.fill();
        // line
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5;
        ctx.lineJoin = "round";
        ctx.beginPath();
        pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
        ctx.stroke();
        // points
        ctx.fillStyle = color;
        pts.forEach((p) => {
            ctx.beginPath();
            ctx.arc(p.x, p.y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
    });

    ctx.fillStyle = theme.axis;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    labels.forEach((label, i) => {
        const x = padL + (n > 1 ? i * stepX : plotW / 2);
        ctx.fillText(_ellipsize(label, 10), x, padT + plotH + 8);
    });
}

function _drawPie(ctx, W, H, spec, theme, unit, doughnut) {
    const data = spec.series[0].data;
    const total = data.reduce((a, b) => a + (b || 0), 0) || 1;
    const cx = W / 2, cy = H / 2;
    const r = Math.min(W, H) / 2 - 16;
    const inner = doughnut ? r * 0.58 : 0;
    let a0 = -Math.PI / 2;
    data.forEach((v, i) => {
        const frac = (v || 0) / total;
        const a1 = a0 + frac * Math.PI * 2;
        ctx.fillStyle = DAI_PALETTE[i % DAI_PALETTE.length];
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, a0, a1);
        ctx.closePath();
        ctx.fill();
        // percentage label on slices ≥ 6%
        if (frac >= 0.06) {
            const mid = (a0 + a1) / 2;
            const lr = inner ? (inner + r) / 2 : r * 0.62;
            ctx.fillStyle = "#fff";
            ctx.font = "bold 12px system-ui, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(Math.round(frac * 100) + "%",
                cx + Math.cos(mid) * lr, cy + Math.sin(mid) * lr);
        }
        a0 = a1;
    });
    if (doughnut) {
        ctx.globalCompositeOperation = "destination-out";
        ctx.beginPath();
        ctx.arc(cx, cy, inner, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalCompositeOperation = "source-over";
        // center total
        ctx.fillStyle = theme.ink;
        ctx.font = "bold 15px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(_fmt(total, unit), cx, cy);
    }
}

function _roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}

function _ellipsize(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// ── Publish to both chat surfaces ────────────────────────────────────────────
window.DaiCharts = { chartPlaceholder: chartPlaceholder, hydrateCharts: hydrateCharts };

})();
