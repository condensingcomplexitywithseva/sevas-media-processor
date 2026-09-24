/* Copyright 2026 Vsevolod Belonogov */
/* SPDX-License-Identifier: Apache-2.0 */

let currentTranslations = {};

window.getT = function(key, fallback) {
    const translated = window.currentTranslations && window.currentTranslations[key];
    if (typeof translated === 'string' && translated) return translated;
    if (typeof fallback === 'string') return fallback;
    console.error('Missing translation:', key);
    return window.currentTranslations && window.currentTranslations.msg_translation_missing || 'Message unavailable.';
};

window.normalizeLang = function(raw) {
    if (raw === 'English') return 'en';
    if (raw === 'Russian') return 'ru';
    return raw || 'en';
};

document.addEventListener('DOMContentLoaded', () => {
    let savedLang = window.normalizeLang(
        (window.rawOriginalState && window.rawOriginalState.GUI_LANGUAGE) ? window.rawOriginalState.GUI_LANGUAGE : localStorage.getItem('app_lang')
    );

    if (window.ALL_TRANSLATIONS && window.ALL_TRANSLATIONS[savedLang]) {
        currentTranslations = window.ALL_TRANSLATIONS[savedLang];
        window.currentTranslations = currentTranslations;
        applyTranslations();
        document.body.classList.add('lang-loaded');
    } else {
        let cached = localStorage.getItem('translations_' + savedLang);
        if (cached) {
            currentTranslations = JSON.parse(cached);
            window.currentTranslations = currentTranslations;
            applyTranslations();
            document.body.classList.add('lang-loaded');
        }
    }

    changeLanguage(savedLang);
});

async function changeLanguage(lang) {
    try {
        if (window.ALL_TRANSLATIONS && window.ALL_TRANSLATIONS[lang]) {
            window.currentTranslations = window.ALL_TRANSLATIONS[lang];
        } else {
            const response = await fetch(`/api/locales/${lang}.json`);
            if (!response.ok) throw new Error("Locale not found");
            window.currentTranslations = await response.json();
        }

        currentTranslations = window.currentTranslations;

        localStorage.setItem('app_lang', lang);
        localStorage.setItem('translations_' + lang, JSON.stringify(currentTranslations));

        applyTranslations();
        if (window.exportOutcome && typeof window.showExportOutcome === 'function') {
            window.showExportOutcome(window.exportOutcome);
        }
        if (typeof window.renderErrors === 'function') window.renderErrors();
        if (typeof window.updateGlobalControls === 'function') window.updateGlobalControls();

        const langSelect = document.getElementById('language');
        if (langSelect && langSelect.value !== lang) {
            langSelect.value = lang;
        }
    } catch (err) {
        console.error(err);
    } finally {
        document.body.classList.add('lang-loaded');
    }
}

function applyTranslations(root = document) {
    const btnStopSpan = root.querySelector('#btn-stop-text');
    if (btnStopSpan) {
        const btnStop = root.querySelector('#btn-stop');
        if (btnStop && btnStop.dataset.stopping === "true") {
            btnStopSpan.setAttribute('data-i18n', 'btn_stopping');
        } else {
            btnStopSpan.setAttribute('data-i18n', 'btn_stop');
        }
    }

    const references = [];
    root.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (window.currentTranslations && window.currentTranslations[key]) {
            let text = window.currentTranslations[key];
            const varsStr = el.getAttribute('data-i18n-args');
            if (varsStr) {
                try {
                    const args = JSON.parse(varsStr);
                    for (const [k, v] of Object.entries(args)) {
                        text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
                    }
                } catch (e) {}
            }
            if (window.renderMessage && (el.dataset.i18nRefs || window.MESSAGE_REFERENCES[key])) {
                references.push({el, key});
                return;
            }
            let escapedText = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            let formattedText;
            if (escapedText.includes('\n')) {
                formattedText = escapedText.split('\n').map(line => {
                    const marker = line.match(/^(?:•|\d+[.)])\s+/);
                    if (marker) {
                        return `<span class="i18n-line i18n-hang"><span class="i18n-marker">${marker[0]}</span><span class="i18n-text">${line.slice(marker[0].length)}</span></span>`;
                    }
                    return `<span class="i18n-line">${line}</span>`;
                }).join('');
            } else {
                formattedText = escapedText;
            }
            if (el.tagName === "SPAN" || el.tagName === "DIV" || el.tagName === "P" || el.tagName === "LABEL" || el.tagName === "BUTTON" || el.tagName === "A" || el.tagName === "H3") {
                el.innerHTML = formattedText; 
            } else {
                el.textContent = text;
            }
        }
    });

    references.forEach(({el, key}) => window.renderMessage(el,
        {key, refs: el.dataset.i18nRefs ? JSON.parse(el.dataset.i18nRefs) : {}}));

    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (window.currentTranslations && window.currentTranslations[key]) {
            el.placeholder = window.currentTranslations[key];
        }
    });

    root.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        if (window.currentTranslations && window.currentTranslations[key]) {
            el.title = window.currentTranslations[key];
        }
    });
}