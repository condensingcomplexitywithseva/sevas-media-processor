/* Copyright 2026 Vsevolod Belonogov */
/* SPDX-License-Identifier: Apache-2.0 */

        let savedLang = window.normalizeLang(window.SERVER_LANGUAGE || localStorage.getItem('app_lang'));
        localStorage.setItem('app_lang', savedLang);
        window.currentTranslations = window.ALL_TRANSLATIONS[savedLang] || window.ALL_TRANSLATIONS['en'];

        window.applyErrors = null;

        Object.defineProperty(window, 'draftErrors', {
            get() {
                return (window.applyErrors && window.hasUnsavedEdits())
                    ? window.applyErrors : window.diskErrors;
            },
            set() { throw new Error('draftErrors is derived; assign diskErrors or applyErrors instead'); }
        });

        window.isBrokenFileError = function(errors) {
            const general = errors && errors['general'];
            return !!general && general.value === 'err_broken_json';
        };

        function flattenObject(ob) {
            var toReturn = {};
            for (var i in ob) {
                if (!ob.hasOwnProperty(i)) continue;
                if ((typeof ob[i]) == 'object' && ob[i] !== null && !Array.isArray(ob[i])) {
                    var flatObject = flattenObject(ob[i]);
                    for (var x in flatObject) {
                        if (!flatObject.hasOwnProperty(x)) continue;
                        toReturn[i + '.' + x] = flatObject[x];
                    }
                } else {
                    toReturn[i] = ob[i];
                }
            }
            return toReturn;
        }

        window.originalState = flattenObject(window.rawOriginalState);
        window.draftState = Object.assign({}, window.originalState);

        function metaKind(name) {
            const meta = window.FIELD_META[name];
            if (meta) return meta.kind;
            if (name.startsWith('ENV_TOKENS.')) return 'string';
            const providerField = name.match(/^LLM_PROVIDERS\.[^.]+\.(.+)$/);
            if (providerField) return window.PROVIDER_FIELD_KINDS[providerField[1]] || 'string';
            return 'string';
        }

        function fieldTab(name) {
            const meta = window.FIELD_META[name.split('.')[0]];
            return meta ? meta.tab : undefined;
        }

        function canon(value, kind) {
            if (value === null || value === undefined) value = '';
            switch (kind) {
                case 'boolean':
                    return value === true || value === 'true';
                case 'integer':
                case 'number': {
                    const s = String(value).trim();
                    return (s !== '' && isFinite(Number(s))) ? Number(s) : s;
                }
                case 'csv_ints': {
                    const parts = Array.isArray(value) ? value : String(value).split(',');
                    return parts.map(p => String(p).trim()).join(',');
                }
                case 'string_list': {
                    const arr = Array.isArray(value)
                        ? value.map(v => (v && typeof v === 'object' && 'value' in v) ? String(v.value) : String(v))
                        : String(value).split(',').filter(x => x.trim() !== '');
                    return arr.slice().sort().join(',');
                }
                default:
                    return String(value).trim();
            }
        }

        function isDirty(name) {
            const kind = metaKind(name);
            return canon(window.draftState[name], kind) !== canon(window.originalState[name], kind);
        }

        window.hasUnsavedEdits = function() {
            for (let key in window.draftState) {
                if (isDirty(key)) return true;
            }
            return false;
        };

        function readInput(input) {
            if (input.name === 'NO_RETRY_STATUSES') {
                return Array.from(document.querySelectorAll('input[name="NO_RETRY_STATUSES"]:checked')).map(cb => cb.value);
            }
            if (input.type === 'checkbox') return input.checked;
            if (metaKind(input.name) === 'boolean') return input.value === 'true';
            return input.value;
        }

        function writeFieldToDOM(name) {
            const value = window.draftState[name];
            if (name === 'NO_RETRY_STATUSES') {
                const list = canon(value, 'string_list').split(',');
                document.querySelectorAll('input[name="NO_RETRY_STATUSES"]').forEach(cb => {
                    cb.checked = list.includes(cb.value);
                });
                return;
            }
            document.getElementsByName(name).forEach(el => {
                if (el.type === 'hidden') return;
                if (el.type === 'checkbox') {
                    el.checked = canon(value, 'boolean') === true;
                } else {
                    el.value = (value === null || value === undefined) ? ''
                        : (Array.isArray(value) ? value.join(',') : value);
                }
            });
        }

        window.runActive = false;

        window.setButtonState = function(button, enabled, title = '') {
            if (!button) return;
            button.disabled = !enabled;
            button.title = title;
            button.style.removeProperty('opacity');
            button.style.removeProperty('cursor');
        };

        window.updateGlobalControls = function() {
            document.querySelectorAll('input[name], select[name], textarea[name]').forEach(el => {
                if (!el.name || el.type === 'hidden' || el.disabled) return;
                let dirty;
                if (el.name === 'NO_RETRY_STATUSES') {
                    const diskList = canon(window.originalState[el.name], 'string_list').split(',');
                    dirty = el.checked !== diskList.includes(el.value);
                } else {
                    dirty = isDirty(el.name);
                }
                el.classList.toggle('dirty-field', dirty);
            });

            const hasChanges = window.hasUnsavedEdits();

            const applyBtn = document.getElementById('btn-apply');
            const discardBtn = document.getElementById('btn-discard');
            const startBtn = document.getElementById('btn-start');
            const unsavedLabel = document.getElementById('unsaved-warning-label');
            const savedLabel = document.getElementById('saved-status-label');

            if (hasChanges) {
                if (unsavedLabel) unsavedLabel.style.display = 'flex';
                if (savedLabel) savedLabel.style.display = 'none';

                window.setButtonState(applyBtn, true);
                window.setButtonState(discardBtn, true);
                window.setButtonState(startBtn, false, window.runActive
                    ? window.getT('warn_run_in_progress', 'A run is already in progress.')
                    : window.getT('warn_pending_changes', 'Pending changes must be applied or discarded before processing.'));
            } else {
                if (unsavedLabel) unsavedLabel.style.display = 'none';
                if (savedLabel) savedLabel.style.display = 'flex';

                window.setButtonState(applyBtn, false,
                    window.getT('warn_nothing_to_apply', 'No unsaved changes.'));
                window.setButtonState(discardBtn, false,
                    window.getT('warn_nothing_to_apply', 'No unsaved changes.'));

                const hasErrors = Object.keys(window.draftErrors).length > 0;
                if (window.runActive) {
                    window.setButtonState(startBtn, false,
                        window.getT('warn_run_in_progress', 'A run is already in progress.'));
                } else if (hasErrors) {
                    window.setButtonState(startBtn, false,
                        window.getT('btn_fix', 'Fix Errors to Start'));
                } else {
                    window.setButtonState(startBtn, true);
                }
            }

            if (window.draftErrors !== window._renderedErrors && typeof window.renderErrors === 'function') {
                window.renderErrors();
            }

            if (window.updateAIDisabledWarning) window.updateAIDisabledWarning();
        };

        window.updateAIDisabledWarning = function() {
            const aiDisabledWarning = document.getElementById('ai-disabled-warning');
            const aiSettingsWrapper = document.getElementById('ai-settings-wrapper');
            if (!aiDisabledWarning || !aiSettingsWrapper) return;

            let isEnabled = window.draftState['ENABLE_LLM_INFERENCE'];
            if (isEnabled === undefined) isEnabled = window.originalState['ENABLE_LLM_INFERENCE'];

            if (isEnabled === 'true' || isEnabled === true) {
                aiDisabledWarning.style.display = 'none';
                aiSettingsWrapper.style.opacity = '1';
                aiSettingsWrapper.style.pointerEvents = 'auto';
                aiSettingsWrapper.style.filter = 'none';
            } else {
                aiDisabledWarning.style.display = 'flex';
                aiSettingsWrapper.style.opacity = '0.6';
                aiSettingsWrapper.style.pointerEvents = 'none';
                aiSettingsWrapper.style.filter = 'grayscale(100%)';
            }
        };

        window.showToast = function(message, isError=false) {
            let toast = document.getElementById('generic-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'generic-toast';
                document.body.appendChild(toast);
                toast.style.position = 'fixed';
                toast.style.top = '20px';
                toast.style.left = '50%';
                toast.style.transform = 'translateX(-50%)';
                toast.style.color = 'white';
                toast.style.padding = '10px 20px';
                toast.style.borderRadius = '4px';
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.3s';
                toast.style.pointerEvents = 'none';
                toast.style.zIndex = '9999';
                toast.style.fontWeight = 'bold';
            }
            toast.style.background = isError ? 'var(--error-color)' : '#4CAF50';
            toast.innerHTML = (isError ? '✘ ' : '✓ ') + message;

            void toast.offsetWidth;
            toast.style.opacity = '1';

            if (toast.timeoutId) clearTimeout(toast.timeoutId);
            toast.timeoutId = setTimeout(() => {
                toast.style.opacity = '0';
            }, 3000);
        };

        window.appConfirm = function(message, options) {
            options = options || {};
            return new Promise(resolve => {
                const overlay = document.getElementById('modal-overlay');
                const dialog = document.getElementById('modal-dialog');
                const msgEl = document.getElementById('modal-message');
                const pathEl = document.getElementById('modal-path');
                const okBtn = document.getElementById('modal-ok');
                const cancelBtn = document.getElementById('modal-cancel');

                msgEl.textContent = message;

                pathEl.textContent = options.path || '';
                dialog.classList.toggle('with-path', !!options.path);
                okBtn.textContent = window.getT('btn_ok', 'OK');
                cancelBtn.textContent = window.getT('btn_cancel', 'Cancel');
                cancelBtn.style.display = options.hideCancel ? 'none' : 'inline-block';
                overlay.style.display = 'flex';

                function cleanup(result) {
                    overlay.style.display = 'none';
                    okBtn.removeEventListener('click', onOk);
                    cancelBtn.removeEventListener('click', onCancel);
                    overlay.removeEventListener('mousedown', onOverlay);
                    document.removeEventListener('keydown', onKey);
                    resolve(result);
                }
                function onOk() { cleanup(true); }
                function onCancel() { cleanup(false); }
                function onOverlay(e) { if (e.target === overlay) cleanup(false); }
                function onKey(e) {
                    if (e.key === 'Escape') cleanup(false);
                    else if (e.key === 'Enter') cleanup(true);
                }
                okBtn.addEventListener('click', onOk);
                cancelBtn.addEventListener('click', onCancel);
                overlay.addEventListener('mousedown', onOverlay);
                document.addEventListener('keydown', onKey);
                okBtn.focus();
            });
        };

        window.appAlert = function(message, options) {
            return window.appConfirm(message,
                Object.assign({}, options, { hideCancel: true }));
        };

        window.showAboutDialog = function() {
            const overlay = document.getElementById('about-overlay');
            const okBtn = document.getElementById('about-ok');
            okBtn.textContent = window.getT('btn_ok', 'OK');
            overlay.style.display = 'flex';
            function cleanup() {
                overlay.style.display = 'none';
                okBtn.removeEventListener('click', cleanup);
                overlay.removeEventListener('mousedown', onOverlay);
                document.removeEventListener('keydown', onKey);
            }
            function onOverlay(e) { if (e.target === overlay) cleanup(); }
            function onKey(e) { if (e.key === 'Escape' || e.key === 'Enter') cleanup(); }
            okBtn.addEventListener('click', cleanup);
            overlay.addEventListener('mousedown', onOverlay);
            document.addEventListener('keydown', onKey);
            okBtn.focus();
        };

        window.openExternalLink = function(key) {
            fetch('/api/about/open_link', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target: key })
            })
            .then(res => res.json().then(data => ({ ok: res.ok, data: data })))
            .then(({ ok, data }) => {
                if (!ok) {
                    appAlert(window.getT('err_open_link_failed',
                        'Could not open your browser. Copy the link and open it yourself.'),
                        data.url ? { path: data.url } : undefined);
                }
            })
            .catch(() => appAlert(window.getT('err_open_link_failed',
                'Could not open your browser. Copy the link and open it yourself.')));
        };

        window.openSettingsFile = function(target) {
            fetch('/api/settings/open_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target: target })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') return;
                const template = data.message_key
                    ? window.getT(data.message_key, data.message)
                    : data.message;
                const sentence = template.replace('{path}', '').trim();
                window.appAlert(sentence, { path: data.path || '' });
            })
            .catch(err => window.appAlert(
                window.getT('alert_error', 'An error occurred') + ': ' + err));
        };

        window.resetSettings = function() {
            fetch('/api/settings/reset', { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    window.diskErrors = { 'general': { type: 'i18n', value: 'error_settings_reset' } };
                    window.applyErrors = null;

                    const bpDisplay = document.getElementById('backup-path-display');
                    if (bpDisplay && data.backup_path) {
                        bpDisplay.innerText = data.backup_path;
                    }

                    window.renderErrors();
                    const fatalCorrupted = document.getElementById('fatal-corrupted-instructions');
                    if (fatalCorrupted) fatalCorrupted.style.display = 'none';
                    if (typeof applyTranslations === 'function') applyTranslations();
                } else {
                    appAlert("Failed to reset: " + data.message);
                }
            });
        };

        function escapeHTML(str) {
            return String(str).replace(/[&<>'"]/g, 
                tag => ({
                    '&': '&amp;',
                    '<': '&lt;',
                    '>': '&gt;',
                    "'": '&#39;',
                    '"': '&quot;'
                }[tag])
            );
        }

        function handleFieldChange(e) {
            const input = e.target;
            if (!input.name || input.type === 'hidden' || input.disabled) return;

            window.draftState[input.name] = readInput(input);

            window.updateGlobalControls();
        }

        window.switchTab = function(tabName, skipLog = false) {
            if (!skipLog) ui_logger.log(`Switching to tab: ${tabName}`, "UI", "DEBUG");
            document.querySelectorAll('.sidebar-tab').forEach(tab => {
                tab.classList.remove('active');
                if (tab.getAttribute('data-tab') === tabName) tab.classList.add('active');
            });
            document.querySelectorAll('.tab-pane').forEach(pane => pane.style.display = 'none');
            const activePane = document.getElementById('tab-content-' + tabName);
            if (activePane) activePane.style.display = 'block';
            window.activeTab = tabName;
            const headerSpan = document.getElementById('tab-header-text');
            if (headerSpan) {
                headerSpan.setAttribute('data-i18n', 'hdr_' + tabName);
                headerSpan.textContent = window.getT('hdr_' + tabName, headerSpan.textContent);
            }
            if (tabName === 'ai' && window.togglePromptModes) {
                window.togglePromptModes('user', false);
                window.togglePromptModes('sys', false);
            }
            const scroller = document.getElementById('main-scroll');
            if (scroller) scroller.scrollTop = 0;
        };

        window.commitGlobalDraft = function() {
            ui_logger.log("Applying Changes...", "UI", "INFO");
            const payload = Object.assign({}, window.draftState);

            fetch('/api/settings/commit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
            .then(res => res.json())
            .then(data => {
                document.querySelectorAll('.error-text').forEach(el => el.innerHTML = '');
                document.querySelectorAll('.error-field').forEach(el => el.classList.remove('error-field'));

                if (data.status === 'success') {
                    ui_logger.log("Settings Synced Successfully.", "UI", "INFO");
                    if (data.settings) {
                        window.rawOriginalState = data.settings;
                        if (data.env_tokens) {
                            window.rawOriginalState.ENV_TOKENS = data.env_tokens;
                        }
                        window.originalState = flattenObject(window.rawOriginalState);
                        window.draftState = Object.assign({}, window.originalState);
                    }

                    window.diskErrors = data.errors || {};
                    window.applyErrors = null;

                    normalizeTokenFields(data.env_tokens);

                    renderErrors();
                    window.updateGlobalControls();

                    const toast = document.getElementById('save-toast');
                    if (toast) {
                        toast.style.opacity = '1';
                        setTimeout(() => toast.style.opacity = '0', 2000);
                        if (typeof applyTranslations === 'function') applyTranslations();
                    }
                } else if (data.status === 'error') {
                    if (window.isBrokenFileError(data.errors)) {
                        ui_logger.log("Save REFUSED: settings.json on disk cannot be read, so nothing was written. Repair the file or reset it first.", "CONFIG", "WARNING");
                    } else {
                        const errCount = Object.keys(data.errors || {}).length;
                        ui_logger.log(`Settings synchronization failed. ${errCount} validation errors detected in Pydantic/Business logic.`, "CONFIG", "WARNING");
                    }
                    window.applyErrors = data.errors || {};
                    renderErrors();
                    window.updateGlobalControls();

                    const errToast = document.getElementById('error-toast');
                    if (errToast) {
                        const errToastSpan = errToast.querySelector('[data-i18n]');
                        if (errToastSpan) {
                            errToastSpan.setAttribute('data-i18n',
                                window.isBrokenFileError(data.errors) ? 'toast_save_refused' : 'toast_not_saved');
                        }
                        errToast.style.opacity = '1';
                        setTimeout(() => errToast.style.opacity = '0', 3000);
                        if (typeof applyTranslations === 'function') applyTranslations();
                    }
                } else if (data.status === 'fatal') {
                    ui_logger.log(`Fatal backend error during save: ${data.message}`, "CONFIG", "ERROR");
                    appAlert(window.getT('alert_error', 'An error occurred') + ': ' + data.message);
                }
            }).catch(err => {
                ui_logger.log(`Network failure during settings commit: ${err}`, "CONFIG", "ERROR");
                console.error("Network error: ", err);
                appAlert(window.getT('alert_error', 'An error occurred') + ': ' + err);
            });
        };

        window.discardGlobalDraft = function() {
            ui_logger.log("Reverting all local changes to match disk state.", "CONFIG", "WARNING");
            window.draftState = Object.assign({}, window.originalState);
            window.applyErrors = null;

            for (const name in window.draftState) {
                if (name.startsWith('ENV_TOKENS.')) continue;
                writeFieldToDOM(name);
            }
            if (window.draftState['GUI_LANGUAGE'] !== undefined) {
                changeLanguage(window.draftState['GUI_LANGUAGE']);
            }
            normalizeTokenFields((window.rawOriginalState && window.rawOriginalState.ENV_TOKENS) || {});

            renderErrors();
            window.updateGlobalControls();
            if (window.togglePromptModes) {
                window.togglePromptModes('user', false);
                window.togglePromptModes('sys', false);
            }
        };

        window.renderErrors = function() {
            if (!window.draftErrors) return;
            window._renderedErrors = window.draftErrors;

            const tabs = ['general', 'images', 'docs', 'animations', 'videos', 'ai', 'output', 'exports'];
            tabs.forEach(tab => {
                const warnIcon = document.getElementById('warn-tab-' + tab);
                if (warnIcon) warnIcon.style.display = 'none';
            });

            document.querySelectorAll('.error-text').forEach(el => el.innerHTML = '');
            document.querySelectorAll('.error-field').forEach(el => el.classList.remove('error-field'));

            const errorTabsFound = new Set();

            const fatalBanner = document.getElementById('general-fatal-error');
            const fatalText = document.getElementById('general-fatal-text');
            const fatalReset = document.getElementById('fatal-reset-instructions');
            const fatalCorrupted = document.getElementById('fatal-corrupted-instructions');

            if (window.draftErrors['general']) {
                if (fatalBanner && fatalText) {
                    const err = window.draftErrors['general'];

                    if (err.type === 'i18n' && err.value === 'error_settings_reset') {
                        fatalText.innerHTML = '';
                        fatalText.parentElement.style.display = 'none';
                        fatalBanner.style.display = 'flex';
                        if (fatalReset) fatalReset.style.display = 'flex';
                        if (fatalCorrupted) fatalCorrupted.style.display = 'none';
                    } else if (window.isBrokenFileError(window.draftErrors)) {
                        fatalText.innerHTML = '';
                        fatalText.parentElement.style.display = 'none';
                        fatalBanner.style.display = 'flex';
                        if (fatalReset) fatalReset.style.display = 'none';
                        if (fatalCorrupted) {
                            fatalCorrupted.style.display = 'flex';
                            const rawErr = document.getElementById('raw-error-container');
                            if (rawErr) rawErr.innerText = err.detail || '';
                        }
                    } else {
                        fatalText.parentElement.style.display = 'flex';
                        let t = err.value;
                        if (err.type === 'i18n') t = window.getT(err.value, err.value);
                        fatalText.innerHTML = escapeHTML(t);
                        fatalBanner.style.display = 'flex';
                        if (fatalReset) fatalReset.style.display = 'none';
                        if (fatalCorrupted) fatalCorrupted.style.display = 'none';
                    }
                }
            } else {
                if (fatalBanner) fatalBanner.style.display = 'none';
                if (fatalReset) fatalReset.style.display = 'none';
                if (fatalCorrupted) fatalCorrupted.style.display = 'none';
            }

            for (const [key, err] of Object.entries(window.draftErrors)) {
                if (key === 'general') continue;

                const tabName = fieldTab(key);

                if (tabName) {
                    errorTabsFound.add(tabName);
                    const warnIcon = document.getElementById('warn-tab-' + tabName);
                    if (warnIcon) warnIcon.style.display = 'inline';
                }

                const errEl = document.getElementById('err-' + key);
                const inputEls = document.getElementsByName(key);
                inputEls.forEach(el => {
                    if(el.type !== 'hidden') el.classList.add('error-field');
                });

                if (errEl) {
                    let text = err.value;
                    if (err.type === 'i18n') text = window.getT(err.value, err.value);
                    else if (err.type === 'min_bound') text = window.getT('warn_min', 'Min:') + ' ' + err.value;
                    else if (err.type === 'max_bound') text = window.getT('warn_max', 'Max:') + ' ' + err.value;
                    errEl.innerHTML = '⚠️ <span>' + escapeHTML(text) + '</span>';
                }
            }

            const globalBanner = document.getElementById('global-error-banner');
            const globalBannerText = document.getElementById('global-error-text');
            const aiHintRow = document.getElementById('global-error-ai-hint');
            const wrapper = document.getElementById('main-wrapper');

            if (errorTabsFound.size > 0) {
                const prefix = !window.hasUnsavedEdits()
                    ? window.getT('msg_disk_errors', 'Errors in the settings on tabs:')
                    : window.getT('msg_resolve_errors', 'Not saved. Errors on tabs:');

                if (globalBanner && globalBannerText) {
                    globalBannerText.innerHTML = '';
                    const errorTabs = Array.from(errorTabsFound);

                    const jumpBtn = document.createElement('button');
                    jumpBtn.type = 'button';
                    jumpBtn.className = 'banner-jump-btn';
                    jumpBtn.textContent = window.getT('btn_show_error_field', 'Go to error');
                    jumpBtn.onclick = () => window.goToFirstError(errorTabs[0]);
                    globalBannerText.appendChild(jumpBtn);

                    globalBannerText.appendChild(document.createTextNode(prefix + ' '));
                    errorTabs.forEach((tab, index) => {
                        if (index > 0) globalBannerText.appendChild(document.createTextNode(', '));
                        const link = document.createElement('a');
                        link.href = 'javascript:void(0)';
                        link.className = 'banner-tab-link';
                        link.textContent = window.getT('tab_' + tab, tab);
                        link.onclick = () => window.goToFirstError(tab);
                        globalBannerText.appendChild(link);
                    });
                    globalBannerText.appendChild(document.createTextNode('.'));

                    globalBanner.style.display = 'flex';
                }
                if (wrapper) wrapper.classList.add('has-banner');

                if (aiHintRow) {
                    let aiModeOn = window.draftState['ENABLE_LLM_INFERENCE'];
                    if (aiModeOn === undefined) aiModeOn = window.originalState['ENABLE_LLM_INFERENCE'];
                    const showHint = errorTabsFound.has('ai') && (aiModeOn === 'true' || aiModeOn === true);
                    aiHintRow.style.display = showHint ? 'flex' : 'none';
                }
            } else {
                if (globalBanner) globalBanner.style.display = 'none';
                if (wrapper) wrapper.classList.remove('has-banner');
                if (aiHintRow) aiHintRow.style.display = 'none';
            }
        }

        function navigateAndPulse(tabName, resolveTarget) {
            window.switchTab(tabName);
            setTimeout(() => {
                const found = resolveTarget();
                if (!found) return;
                const target = found.closest('.form-group') || found;
                target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                target.classList.remove('error-flash');
                void target.offsetWidth;
                target.classList.add('error-flash');
                setTimeout(() => target.classList.remove('error-flash'), 1700);
            }, 50);
        }

        window.goToFirstError = function(tabName) {
            navigateAndPulse(tabName, () => {
                const pane = document.getElementById('tab-content-' + tabName);
                if (!pane) return null;
                const visible = el => el.offsetParent !== null;
                return Array.from(pane.querySelectorAll('.error-field')).find(visible)
                    || Array.from(pane.querySelectorAll('.error-text'))
                        .find(el => el.textContent.trim() !== '' && visible(el))
                    || null;
            });
        };

        window.goToField = function(tabName, fieldName) {
            navigateAndPulse(tabName, () =>
                document.getElementById(fieldName) || document.getElementsByName(fieldName)[0]);
        };

        function normalizeTokenFields(maskedTokens) {
            document.querySelectorAll('input[name^="ENV_TOKENS."]').forEach(el => {
                const provider = el.name.slice('ENV_TOKENS.'.length);
                el.value = '';
                if (maskedTokens && maskedTokens[provider]) {
                    el.placeholder = '********';
                    el.removeAttribute('data-i18n-placeholder');
                } else {
                    el.placeholder = window.getT('placeholder_token', 'Enter token here...');
                    el.dataset.i18nPlaceholder = 'placeholder_token';
                }
                el.classList.remove('dirty-field');
            });
        }

        let eventSource = null;
        let isResizing = false;

        const resizer = document.getElementById('drag-resizer');
        const consoleContainer = document.getElementById('console-container');

        if (resizer) {
            resizer.addEventListener('mousedown', function(e) {
                isResizing = true;
                resizer.classList.add('active');
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';
            });

            document.addEventListener('mousemove', function(e) {
                if (!isResizing) return;
                let newWidth = document.body.clientWidth - e.clientX - 15;
                if (newWidth < 250) newWidth = 250; 
                if (newWidth > document.body.clientWidth - 400) newWidth = document.body.clientWidth - 400;
                consoleContainer.style.width = newWidth + 'px';
            });

            document.addEventListener('mouseup', function(e) {
                isResizing = false;
                resizer.classList.remove('active');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            });
        }

        function resetDefaultByName(name, defaultValue) {
            const els = document.getElementsByName(name);
            let firstValidEl = null;

            els.forEach(el => {
                if (el.type === 'hidden') return;
                if (els.length > 1 && el.disabled) return;
                if (!firstValidEl) firstValidEl = el;

                if (el.type === 'checkbox') {
                    if (Array.isArray(defaultValue)) { el.checked = defaultValue.includes(el.value); } 
                    else { el.checked = (defaultValue === true || defaultValue === 'true' || defaultValue === 'True'); }
                } else if (el.tagName === 'SELECT') {
                    if (el.multiple) {
                        const vals = Array.isArray(defaultValue) ? defaultValue : (typeof defaultValue === 'string' ? defaultValue.split(',') : [defaultValue]);
                        Array.from(el.options).forEach(opt => { opt.selected = vals.includes(opt.value); });
                    } else { el.value = defaultValue; }
                } else if (Array.isArray(defaultValue) && !el.multiple) {
                    el.value = defaultValue.join(',');
                } else { el.value = defaultValue === null ? '' : defaultValue; }
            });

            if (firstValidEl) {
                firstValidEl.dispatchEvent(new Event('input', { bubbles: true }));
                firstValidEl.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            fetch('/api/process/status')
                .then(res => res.json())
                .then(data => {
                    if (data.is_running) {
                        window.runActive = true;
                        window.setButtonState(document.getElementById('btn-start'), false,
                            window.getT('warn_run_in_progress', 'A run is already in progress.'));

                        const stopBtn = document.getElementById('btn-stop');
                        if (data.is_stopping) {
                            stopBtn.dataset.stopping = "true";
                            window.setButtonState(stopBtn, false,
                                window.getT('warn_already_stopping', 'Stopping - waiting for the current file to finish.'));
                        } else {
                            stopBtn.dataset.stopping = "false";
                            window.setButtonState(stopBtn, true);
                        }
                    }
                    attachSSEStream();
                })
                .catch(err => console.error("Failed to fetch process status:", err));

            renderErrors();
            window.updateGlobalControls();

            if (window.activeTab) {
                window.switchTab(window.activeTab, true);
            } else {
                window.switchTab('general', true);
            }
        });

        window.UI_CLIENT_ID = (window.crypto && crypto.randomUUID)
            ? crypto.randomUUID()
            : String(Date.now()) + '-' + Math.random();

        const ui_logger = {
            log: function(content, category = "UI", level = "INFO") {
                const now = new Date();
                const timestamp = now.getHours().toString().padStart(2, '0') + ':' +
                                  now.getMinutes().toString().padStart(2, '0') + ':' +
                                  now.getSeconds().toString().padStart(2, '0');
                this.render({
                    type: 'log',
                    category: category,
                    level: level,
                    timestamp: timestamp,
                    content: content
                });
                fetch('/api/log', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: content, category: category, level: level, client_id: window.UI_CLIENT_ID })
                }).catch(() => {});
            },
            render: function(msg) {
                const consoleDiv = document.getElementById('console-output');
                if (!consoleDiv) return;

                const line = document.createElement('div');
                line.className = 'log-line';

                const ts = document.createElement('span');
                ts.className = 'log-timestamp';
                ts.textContent = msg.timestamp || '';

                const cat = document.createElement('span');
                cat.className = 'log-category log-' + (msg.category || 'system').toLowerCase();
                cat.textContent = '[' + (msg.category || 'SYSTEM') + ']';

                const content = document.createElement('span');
                content.className = 'log-content';
                if (msg.level === 'ERROR' || msg.level === 'CRITICAL') content.classList.add('log-level-error');
                if (msg.level === 'WARNING') content.classList.add('log-level-warning');
                content.textContent = msg.content;

                line.appendChild(ts);
                line.appendChild(cat);
                line.appendChild(content);

                consoleDiv.appendChild(line);
                consoleDiv.scrollTop = consoleDiv.scrollHeight;

                while (consoleDiv.childNodes.length > 1000) {
                    consoleDiv.removeChild(consoleDiv.firstChild);
                }
            }
        };
        window.ui_logger = ui_logger;

        function finishRun(outcome) {
            window.runActive = false;
            window.updateGlobalControls();
            document.getElementById('btn-stop-icon').innerText = '🛑';
            document.getElementById('btn-stop').dataset.stopping = "false";
            window.setButtonState(document.getElementById('btn-stop'), false,
                window.getT('warn_no_run_to_stop', 'Nothing is running.'));

            const bar = document.getElementById('progress-bar');
            const status = document.getElementById('run-status');
            status.className = 'status-' + outcome;
            status.style.display = 'flex';

            if (outcome === 'done') {
                bar.classList.add('progress-done');
                status.textContent = '✔ ' + window.getT('run_status_done', 'Run completed successfully');
                window.showToast(window.getT('run_status_done', 'Run completed successfully'));
                ui_logger.log(window.getT('console_done', 'Processing Completed'), "CORE", "INFO");
            } else if (outcome === 'failed') {
                bar.classList.add('progress-failed');
                status.textContent = '✘ ' + window.getT('run_status_failed', 'Run failed — the process did not complete');
                window.showToast(window.getT('run_status_failed', 'Run failed — the process did not complete'), true);
                ui_logger.render({
                    type: 'log', category: 'CORE', level: 'ERROR',
                    timestamp: new Date().toTimeString().slice(0, 8),
                    content: window.getT('console_run_failed', 'Run FAILED — the process did not complete. See the errors above.')
                });
            } else if (outcome === 'aborted') {
                status.textContent = '■ ' + window.getT('run_status_aborted', 'Run stopped by user');
            }
            if (typeof applyTranslations === 'function') applyTranslations();
        }

        function attachSSEStream() {
            if (eventSource) return;
            eventSource = new EventSource('/api/process/stream?token=' + encodeURIComponent(window.API_TOKEN));

            eventSource.onmessage = function(e) {
                const msg = JSON.parse(e.data);

                if (msg.type === 'log') {
                    if (msg.ui_client_id && msg.ui_client_id === window.UI_CLIENT_ID) return;
                    ui_logger.render(msg);
                } else if (msg.type === 'progress') {
                    document.getElementById('progress-bar').value = msg.value;
                } else if (msg.type === 'done' || msg.type === 'failed' || msg.type === 'aborted') {
                    finishRun(msg.type);
                }
            };
        }

        function translateServerMessage(message) {
            if (typeof message === 'string' && message.startsWith('i18n:')) {
                const body = message.slice(5);
                const sep = body.indexOf('|');
                const key = sep === -1 ? body : body.slice(0, sep);
                const detail = sep === -1 ? '' : body.slice(sep + 1);
                let text = window.getT(key, key);
                if (detail) text += '\n\n(' + detail + ')';
                return text;
            }
            return message;
        }

        function startProcessing() {
            const startBtn = document.getElementById('btn-start');
            if (startBtn.disabled) return;

            window.runActive = true;
            window.setButtonState(startBtn, false,
                window.getT('warn_run_in_progress', 'A run is already in progress.'));
            document.getElementById('progress-bar').classList.remove('progress-failed', 'progress-done');
            const runStatus = document.getElementById('run-status');
            runStatus.style.display = 'none';
            runStatus.textContent = '';

            fetch('/api/process/start', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        window.setButtonState(document.getElementById('btn-stop'), true);
                        document.getElementById('btn-stop-icon').innerText = '🛑';
                        document.getElementById('btn-stop').dataset.stopping = "false";
                        document.getElementById('progress-bar').value = 0;
                        if (typeof applyTranslations === 'function') applyTranslations();
                    } else {
                        window.runActive = false;
                        window.setButtonState(startBtn, true);
                        appAlert(window.getT('alert_config_err', 'Configuration Error') + '\n\n'
                            + (translateServerMessage(data.message) || JSON.stringify(data.details)));

                        if (data.errors) {
                            window.diskErrors = data.errors;
                            window.applyErrors = null;
                            renderErrors();
                            window.updateGlobalControls();
                        }
                    }
                }).catch(err => {
                    window.runActive = false;
                    window.setButtonState(startBtn, true);
                    appAlert(window.getT('alert_start_fail', 'Failed to start processing') + ': ' + err);
                });
        }

        function stopProcessing() {
            fetch('/api/process/stop', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.status !== 'success') {
                        ui_logger.log(`Stop request rejected: ${data.message}`, "CORE", "WARNING");
                        return;
                    }
                    document.getElementById('btn-stop-icon').innerText = '⏳';
                    document.getElementById('btn-stop').dataset.stopping = "true";
                    window.setButtonState(document.getElementById('btn-stop'), false,
                        window.getT('warn_already_stopping', 'Stopping - waiting for the current file to finish.'));
                    if (typeof applyTranslations === 'function') applyTranslations();
                }).catch(err => appAlert(window.getT('alert_stop_fail', 'Failed to stop processing') + ': ' + err));
        }

        window.copyTextToClipboard = function(text, btn) {
            const flashCopied = () => {
                if (!btn) return;
                const original = btn.innerHTML;
                btn.innerHTML = '✅ <span>' + window.getT('btn_copied', 'Copied!') + '</span>';
                setTimeout(() => { btn.innerHTML = original; if (typeof applyTranslations === 'function') applyTranslations(); }, 1500);
            };
            function fallbackCopy() {
                const helper = document.createElement('textarea');
                helper.value = text;
                helper.style.position = 'fixed';
                helper.style.opacity = '0';
                document.body.appendChild(helper);
                helper.select();
                try { document.execCommand('copy'); flashCopied(); } catch (e) {  }
                document.body.removeChild(helper);
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(flashCopied).catch(fallbackCopy);
            } else {
                fallbackCopy();
            }
        };

        window.copyConsoleToClipboard = function() {
            const consoleDiv = document.getElementById('console-output');
            if (!consoleDiv) return;
            const text = Array.from(consoleDiv.querySelectorAll('.log-line'))
                .map(line => line.innerText)
                .join('\n');
            window.copyTextToClipboard(text, document.getElementById('btn-copy-console'));
        };

        function browseOS(dialogType, targetInputId, callback = null) {
            if (!window.pywebview || !window.pywebview.api) {
                console.error("PyWebView API is not ready yet.");
                return;
            }

            let promise = dialogType === 'folder' 
                ? window.pywebview.api.browse_folder() 
                : window.pywebview.api.browse_file();

            promise.then(path => {
                if (path) {
                    const input = document.getElementById(targetInputId);
                    input.value = path;
                    input.dispatchEvent(new Event('input', { bubbles: true })); 
                    input.dispatchEvent(new Event('change', { bubbles: true })); 
                    if (callback) callback(targetInputId);
                }
            }).catch(err => console.error("OS Dialog Error:", err));
        }

        document.body.addEventListener('input', handleFieldChange);
        document.body.addEventListener('change', handleFieldChange);

