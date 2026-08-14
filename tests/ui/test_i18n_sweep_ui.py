# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from pathlib import Path

LANGS = tuple(sorted(
    p.stem for p in (Path(__file__).resolve().parents[2] / "src" / "locales").glob("*.json")
))
TABS = ("general", "images", "docs", "animations", "videos", "output", "ai", "exports")
APP_WIDTH = 1264
APP_HEIGHT = 800

VALID_AI = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"}

SWEEP = """
(ctx) => {
    const out = [];
    const report = (key, kind, detail) =>
        out.push({tab: ctx.tab, lang: ctx.lang, key, kind, detail});

    // Rendered line count for elements WITHOUT <br>: height divided by the
    // height with wrapping forced off.
    const countLines = (el) => {
        const h = el.getBoundingClientRect().height;
        const prev = el.style.whiteSpace;
        el.style.whiteSpace = 'nowrap';
        const single = el.getBoundingClientRect().height;
        el.style.whiteSpace = prev;
        return single ? Math.round(h / single) : 1;
    };

    document.querySelectorAll('[data-i18n]').forEach(el => {
        const tag = el.tagName;
        if (tag === 'OPTION' || tag === 'INPUT' || tag === 'TEXTAREA') return;
        if (!el.getClientRects().length) return; // hidden (other tab/pane/mode)
        const key = el.getAttribute('data-i18n');
        const source = window.getT(key, '');
        if (!source.trim() || !el.textContent.trim()) return;

        const newlines = (source.match(/\\n/g) || []).length;
        const breaks = el.querySelectorAll('br').length;
        const lineBlocks = el.querySelectorAll('.i18n-line').length;
        if (newlines > 0) {
            if (lineBlocks !== newlines + 1 || breaks !== 0)
                report(key, 'break-mismatch',
                    `source has ${newlines} newline(s) but the element renders ` +
                    `${lineBlocks} .i18n-line block(s) and ${breaks} <br>`);
        } else if (breaks !== 0 || lineBlocks !== 0) {
            report(key, 'break-mismatch',
                `source has no newline but the element renders ${lineBlocks} ` +
                `.i18n-line block(s) and ${breaks} <br>`);
        }

        const ws = getComputedStyle(el).whiteSpace;
        const prose = ws === 'pre-wrap' || ws === 'pre-line';
        if (!prose && breaks === 0 && newlines === 0) {
            const lines = countLines(el);
            if (lines > 1)
                report(key, 'unintended-wrap', `renders ${lines} lines in a single-line slot`);
        }
        if (el.scrollWidth > el.clientWidth + 1)
            report(key, 'horizontal-overflow',
                `content ${el.scrollWidth}px wide in a ${el.clientWidth}px slot`);
    });

    // Options: the closed select box clips overlong text without wrapping,
    // so measure each option's text against the box's inner width.
    const g = document.createElement('canvas').getContext('2d');
    document.querySelectorAll('select').forEach(sel => {
        if (!sel.getClientRects().length) return;
        const cs = getComputedStyle(sel);
        g.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        const inner = sel.clientWidth - parseFloat(cs.paddingLeft)
            - parseFloat(cs.paddingRight) - 20; // native dropdown arrow
        Array.from(sel.options).forEach(o => {
            const text = o.textContent.trim();
            if (!text) return;
            const w = g.measureText(text).width;
            if (w > inner)
                report(o.getAttribute('data-i18n') || text.slice(0, 40), 'option-clipped',
                    `option text needs ${Math.round(w)}px, select box offers ${Math.round(inner)}px`);
        });
    });

    return out;
}
"""


def test_every_visible_string_fits_its_slot(open_page):
    page = open_page(VALID_AI,
                     tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)

    violations = {}
    swept = 0
    for lang in LANGS:
        page.evaluate(f"changeLanguage('{lang}')")
        page.wait_for_timeout(300)
        for tab in TABS:
            page.evaluate(f"window.switchTab('{tab}', true)")
            page.wait_for_timeout(250)
            found = page.evaluate(SWEEP, {"tab": tab, "lang": lang})
            swept += 1
            for v in found:
                violations.setdefault((v["lang"], v["key"], v["kind"]), v)

    assert swept == len(LANGS) * len(TABS)
    assert not violations, (
        f"{len(violations)} localized string(s) outgrow their UI slot:\n" + "\n".join(
            f"  [{v['lang']}] {v['key']} on tab '{v['tab']}': {v['kind']} — {v['detail']}"
            for v in violations.values()
        )
    )
