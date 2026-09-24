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
        let repairFields = new Set(window.FORM_REPAIR_FIELDS || []);
        const editedFields = new Set();
        const fieldRevisions = {};
        let submission = null;
        let settingsRevision = window.SETTINGS_REVISION || 0;
        let acceptedSnapshot = null;
        let resetOperation = null;
        window.settingsResetUnconfirmed = false;
        let settingsRestartRequired = false;
        let editRevision = 0;
        window.settingsSavePending = false;
        window.settingsSaveUnconfirmed = false;

        function recordFieldEdit(name, explicit = true) {
            fieldRevisions[name] = ++editRevision;
            if (explicit) {
                editedFields.add(name);
                if (submission) submission.discarded.delete(name);
            }
        }

        window.revertDraftField = function(name) {
            const submitted = submission && Object.hasOwn(submission.edits, name)
                && !submission.cancelled.has(name);
            if (submitted) window.draftState[name] = submission.edits[name];
            else if (Object.hasOwn(window.originalState, name)) window.draftState[name] = window.originalState[name];
            else delete window.draftState[name];
            editedFields.delete(name);
            recordFieldEdit(name, false);
            if (submission) submission.discarded.add(name);
            writeFieldToDOM(name);
        };

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
                case 'boolean': {
                    if (value === true || value === false) return value;
                    const s = String(value).toLowerCase();
                    if (['true', '1', 'on', 'yes', 'y', 't'].includes(s)) return true;
                    if (['false', '0', 'off', 'no', 'n', 'f'].includes(s)) return false;
                    return 'invalid:' + s;
                }
                case 'integer': {
                    const s = String(value).trim();
                    if (/^[+-]?\d+$/.test(s)) return BigInt(s).toString();
                    return s;
                }
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
            return (window.settingsSaveUnconfirmed && submission && Object.hasOwn(submission.edits, name) && !submission.cancelled.has(name))
                || (repairFields.has(name) && editedFields.has(name))
                || canon(window.draftState[name], kind) !== canon(window.originalState[name], kind);
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
                if (document.getElementsByName(name).length > 1 && el.disabled) return;
                if (el.tagName === 'SELECT') {
                    const text = value == null ? '' : String(value);
                    el.querySelectorAll('[data-unrepresented-value]').forEach(option => {
                        if (option.value !== text) option.remove();
                    });
                    if (!Array.from(el.options).some(option => option.value === text)) {
                        const option = document.createElement('option');
                        option.value = text; option.textContent = text;
                        option.dataset.unrepresentedValue = 'true';
                        el.appendChild(option);
                    }
                }
                if (el.type === 'checkbox') {
                    el.checked = canon(value, 'boolean') === true;
                } else {
                    el.value = (value === null || value === undefined) ? ''
                        : (Array.isArray(value) ? value.join(',') : value);
                }
            });
        }

        function syncDraftControls(preserveTokens = new Set()) {
            for (const name of Object.keys(window.draftState)) {
                if (name.startsWith('ENV_TOKENS.') || ['LLM_USER_PROMPT', 'LLM_SYSTEM_PROMPT'].includes(name)) continue;
                writeFieldToDOM(name);
            }
            if (window.syncProviderSelection) window.syncProviderSelection();
            for (const type of ['user', 'sys']) {
                if (window.togglePromptModes) window.togglePromptModes(type, false, false);
                writeFieldToDOM(type === 'user' ? 'LLM_USER_PROMPT' : 'LLM_SYSTEM_PROMPT');
                const mode = document.getElementById(type + '_prompt_mode');
                if (mode && mode.value === 'FILE' && window.loadPreview) window.loadPreview(type + '_file_input');
            }
            normalizeTokenFields(window.rawOriginalState.ENV_TOKENS || {}, preserveTokens);
        }

        window.settingsOperation = function() {
            return {id: crypto.randomUUID(), base: settingsRevision,
                revisions: Object.assign({}, fieldRevisions)};
        };

        window.settingsOperationHeaders = function(operation) {
            return {'Content-Type': 'application/json', 'X-Settings-Operation': operation.id,
                'X-Settings-Epoch': window.SETTINGS_EPOCH, 'X-Settings-Revision': String(operation.base)};
        };

        function acceptSettingsSnapshot(data) {
            const snapshot = data.snapshot;
            if (!snapshot || snapshot.epoch !== window.SETTINGS_EPOCH
                    || !Number.isSafeInteger(snapshot.revision) || !snapshot.settings
                    || !snapshot.errors || !snapshot.env_tokens || !Array.isArray(snapshot.repair_fields)) {
                throw new Error('Missing authoritative settings snapshot');
            }
            if (snapshot.revision <= settingsRevision) return false;
            settingsRevision = snapshot.revision;
            acceptedSnapshot = snapshot;
            return true;
        }

        function reconcileSettings(preserve) {
            const draft = window.draftState;
            if (acceptedSnapshot) {
                window.rawOriginalState = Object.assign({}, acceptedSnapshot.settings,
                    {ENV_TOKENS: acceptedSnapshot.env_tokens});
                repairFields = new Set(acceptedSnapshot.repair_fields);
                window.diskErrors = acceptedSnapshot.backup_path
                    ? {general: {type: 'i18n', value: 'error_settings_reset'}} : acceptedSnapshot.errors;
            }
            window.originalState = flattenObject(window.rawOriginalState);
            window.draftState = Object.assign({}, window.originalState);
            for (const name of preserve) {
                if (Object.hasOwn(draft, name)) window.draftState[name] = draft[name];
                else delete window.draftState[name];
            }
            for (const name of editedFields) if (!preserve.has(name)) editedFields.delete(name);
        }

        function acknowledgeSettings(data, submittedRevisions) {
            const draft = window.draftState;
            const late = new Set(Object.keys(draft).filter(name =>
                fieldRevisions[name] !== submittedRevisions[name] && !submission.discarded.has(name)));
            acceptSettingsSnapshot(data);
            reconcileSettings(late);
            submission = null;
            window.settingsSaveUnconfirmed = false;
            syncDraftControls(late);
        }

        window.acknowledgeTokenWipe = function(data, operation, provider) {
            if (!acceptSettingsSnapshot(data)) return false;
            const name = 'ENV_TOKENS.' + provider;
            const preserve = new Set(Object.keys(window.draftState).filter(isDirty));
            if (fieldRevisions[name] === operation.revisions[name]) {
                preserve.delete(name);
                editedFields.delete(name);
                recordFieldEdit(name, false);
            }
            if (submission) submission.cancelled.add(name);
            reconcileSettings(preserve);
            window.applyErrors = window.applyErrors && window.hasUnsavedEdits()
                ? Object.assign({}, window.applyErrors, window.diskErrors) : null;
            syncDraftControls(preserve);
            window.renderErrors();
            window.updateGlobalControls();
            return true;
        };

        window.settingsOperationFailure = function(data, id, key) {
            if (data.snapshot) {
                const preserve = new Set(Object.keys(window.draftState).filter(isDirty));
                if (!acceptSettingsSnapshot(data)) return;
                reconcileSettings(preserve);
                syncDraftControls(preserve);
                window.renderErrors();
                window.updateGlobalControls();
            }
            window.notice(Object.assign(window.serverNotice(data), {id, key}));
        };

        window.runState = {run_id: '', revision: -1, phase: 'idle', progress: 0};
        Object.defineProperty(window, 'runActive', {
            get: () => ['starting', 'unknown', 'running', 'stopping'].includes(window.runState.phase)
        });

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

            if (window.settingsSavePending || window.settingsSaveUnconfirmed) {
                if (unsavedLabel) unsavedLabel.style.display = 'flex';
                if (savedLabel) savedLabel.style.display = 'none';
                const reason = window.getT(window.settingsSavePending ? 'settings_saving' : 'settings_save_unconfirmed');
                window.setButtonState(applyBtn, !window.settingsSavePending, reason);
                window.setButtonState(discardBtn, false, reason);
                window.setButtonState(startBtn, false, reason);
            }
            if (settingsRestartRequired) {
                window.setButtonState(applyBtn, false, window.getT('err_settings_operation_unknown'));
            }
            if (window.settingsResetUnconfirmed) {
                window.setButtonState(startBtn, false, window.getT('err_settings_reset_unconfirmed'));
                window.setButtonState(discardBtn, false, window.getT('err_settings_reset_unconfirmed'));
            }
            if (unsavedLabel) {
                const label = unsavedLabel.querySelector('[data-i18n]');
                const key = window.settingsSavePending ? 'settings_saving'
                    : window.settingsSaveUnconfirmed ? 'settings_save_unconfirmed' : 'lbl_unsaved';
                label.dataset.i18n = key;
                label.textContent = window.getT(key);
            }

            if (window.draftErrors !== window._renderedErrors && typeof window.renderErrors === 'function') {
                window.renderErrors();
            }

            renderRunControls();
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
                aiSettingsWrapper.classList.remove('ai-dormant');
            } else {
                aiDisabledWarning.style.display = window.activeTab === 'ai' ? 'flex' : 'none';
                aiSettingsWrapper.classList.add('ai-dormant');
            }
        };

        function rememberFocus(element = document.activeElement) {
            if (!element || element === document.body) return () => null;
            const row = element.closest('[data-notice-id]');
            const owner = row && row.dataset.noticeId;
            const setting = element.dataset.setting;
            const action = element.dataset.noticeAction;
            const id = element.id;
            const container = element.parentElement && element.parentElement.closest('[id]');
            const containerId = container && container.id;
            const settingScope = row || container;
            const occurrence = setting && settingScope ? Array.from(settingScope.querySelectorAll('[data-setting]'))
                .filter(el => el.dataset.setting === setting).indexOf(element) : 0;
            const selector = 'button, a[href], input, select, textarea, [tabindex]';
            const index = container ? Array.from(container.querySelectorAll(selector)).indexOf(element) : -1;
            return () => {
                if (element.isConnected) return element;
                const scope = owner ? Array.from(document.querySelectorAll('[data-notice-id]'))
                    .find(el => el.dataset.noticeId === owner) : document.getElementById(containerId);
                if (scope && setting) return Array.from(scope.querySelectorAll('[data-setting]'))
                    .filter(el => el.dataset.setting === setting)[occurrence];
                if (scope && action) return Array.from(scope.querySelectorAll('[data-notice-action]'))
                    .find(el => el.dataset.noticeAction === action);
                return (id && document.getElementById(id)) ||
                    (scope && index >= 0 ? scope.querySelectorAll(selector)[index] : null);
            };
        }
        function retainFocus(root) {
            const active = document.activeElement;
            if (!root || !root.contains(active)) return () => {};
            const resolve = rememberFocus(active);
            return () => {
                const target = resolve();
                if (target && !target.disabled && target.getClientRects().length) target.focus({preventScroll: true});
            };
        }

        window.notices = new Map();
        window.settingRef = function(key) {
            const candidates = Array.from(document.getElementsByName(key));
            const byId = document.getElementById(key);
            if (byId && !candidates.includes(byId)) candidates.unshift(byId);
            const element = candidates.find(el => !el.disabled && el.type !== 'hidden') || candidates[0];
            if (!element) return null;
            const pane = element.closest('.tab-pane');
            const group = element.closest('.form-group');
            const label = element.labels && element.labels[0] || group && group.querySelector('label');
            return {field: key, element, tab: pane && pane.id.replace('tab-content-', ''),
                label: label ? label.textContent.trim().replace(/:$/, '') : ''};
        };
        window.settingLink = function(key, beforeJump) {
            const ref = window.settingRef(key);
            if (!ref || !ref.tab || !ref.label) return null;
            const link = document.createElement('a');
            link.href = '#';
            link.className = 'banner-tab-link';
            link.dataset.setting = key;
            link.textContent = ref.label;
            link.onclick = event => {
                event.preventDefault();
                if (beforeJump) beforeJump();
                window.goToField(key);
            };
            return link;
        };
        window.serverNotice = function(data) {
            return {key: data.message_key || '', text: data.message || '',
                detail: data.detail || '', detailKey: data.detail_key || '',
                path: data.path || '', field: data.field || '',
                args: data.args || {}, refs: data.refs || {}};
        };
        window.renderMessage = function(element, message, beforeJump) {
            const spec = typeof message === 'string' ? {text: message} : message;
            const text = (spec.prefix || '') + (spec.key ? window.getT(spec.key, spec.text || undefined)
                : spec.textForLocale ? spec.textForLocale() : spec.text || '');
            const refs = Object.assign({}, window.MESSAGE_REFERENCES[spec.key] || {}, spec.refs);
            if (spec.field) refs.setting = spec.field;
            const restoreFocus = retainFocus(element);
            element.replaceChildren();
            function appendText(target, value) {
                for (const part of value.split(/(\{\w+\})/g)) {
                    const name = part.startsWith('{') ? part.slice(1, -1) : '';
                    const link = refs[name] ? window.settingLink(refs[name], beforeJump) : null;
                    if (link) target.appendChild(link);
                    else target.appendChild(document.createTextNode(
                        refs[name] ? window.getT('msg_setting_unavailable') :
                        Object.prototype.hasOwnProperty.call(spec.args || {}, name) ? String(spec.args[name]) : part));
                }
            }
            if (!text.includes('\n')) appendText(element, text);
            else for (const line of text.split('\n')) {
                const block = document.createElement('span');
                block.className = 'i18n-line';
                const marker = line.match(/^(?:•|\d+[.)])\s+/);
                if (marker) {
                    block.classList.add('i18n-hang');
                    const prefix = document.createElement('span');
                    prefix.className = 'i18n-marker'; prefix.textContent = marker[0];
                    const body = document.createElement('span'); body.className = 'i18n-text';
                    appendText(body, line.slice(marker[0].length));
                    block.append(prefix, body);
                } else appendText(block, line);
                element.appendChild(block);
            }
            restoreFocus();
        };
        window.messageText = function(spec) {
            const node = document.createElement('span');
            window.renderMessage(node, spec);
            return node.textContent;
        };
        let toastTimer;
        let toastNotice = null;
        window.clearNotice = function(id) {
            window.notices.delete(id);
            if (window.renderErrors) window.renderErrors();
        };
        window.notice = function(spec) {
            if (spec.surface === 'dialog') return window.appAlert(spec);
            if (spec.surface === 'console') {
                ui_logger.log(window.messageText(spec), 'UI', (spec.level || 'info').toUpperCase());
                return;
            }
            if (spec.surface === 'inline') {
                if (!spec.target) throw new Error('Inline messages need a target');
                spec.target._notice = spec;
                spec.target.dataset.noticeComponent = 'inline';
                window.renderMessage(spec.target, spec);
                return;
            }
            if (spec.surface === 'toast') {
                if (spec.level && spec.level !== 'success') throw new Error('Only success messages may fade');
                toastNotice = spec;
                const toast = document.getElementById('generic-toast');
                window.renderMessage(toast, spec);
                toast.classList.add('toast-visible');
                clearTimeout(toastTimer);
                toastTimer = setTimeout(() => { toast.classList.remove('toast-visible'); toastNotice = null; }, 3000);
                return;
            }
            if (!spec.id) throw new Error('Persistent messages need an owner ID');
            const fields = spec.clearOnFields || [];
            const saved = Object.fromEntries(fields.map(field => [field, canon(window.originalState[field], metaKind(field))]));
            window.notices.set(spec.id, Object.assign({level: 'error'}, spec,
                {saved}));
            if (window.renderErrors) window.renderErrors();
        };
        window.reportFailure = function(id, spec) {
            const message = Object.assign({summaryKey: 'notice_action_failed'}, spec, {id});
            window.notice(message);
            return window.appAlert(message);
        };
        window.clearChangedSettingNotices = function() {
            for (const [id, spec] of window.notices) {
                if ((spec.clearOnFields || []).some(field =>
                    spec.saved[field] !== canon(window.originalState[field], metaKind(field)))) {
                    window.notices.delete(id);
                }
            }
        };
        window.renderNotices = function() {
            const host = document.getElementById('persistent-notices');
            const restoreFocus = retainFocus(host);
            host.replaceChildren();
            const entries = Array.from(window.notices);
            const general = window.draftErrors && window.draftErrors.general;
            if (general && !window.isBrokenFileError(window.draftErrors) && general.value !== 'error_settings_reset') {
                entries.push(['configuration', {key: general.type === 'i18n' ? general.value : '',
                    text: general.value, detail: general.detail, summaryKey: 'notice_action_failed', level: 'error'}]);
            }
            for (const [id, spec] of entries) {
                if (id === 'provider-inspection' && window.activeTab !== 'ai') continue;
                const row = document.createElement('div');
                row.className = 'banner-row notice-' + spec.level;
                row.dataset.noticeId = id;
                row.dataset.noticeComponent = 'banner';
                const text = document.createElement('span');
                window.renderMessage(text, spec.summaryKey ? Object.assign({}, spec, {key: spec.summaryKey}) : spec);
                row.appendChild(text);
                if (id === 'start') {
                    const link = text.querySelector('a');
                    if (link) link.id = 'refusal-jump-link';
                }
                const actions = spec.actions || [{key: 'btn_details', run: () => window.appAlert(spec)}];
                for (const action of actions) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.dataset.noticeAction = action.key;
                    button.className = 'banner-jump-btn';
                    button.textContent = window.getT(action.key);
                    button.onclick = action.run;
                    row.appendChild(button);
                }
                host.appendChild(row);
            }
            const banner = document.getElementById('global-error-banner');
            const rows = Array.from(banner.querySelectorAll('.banner-row'))
                .filter(row => getComputedStyle(row).display !== 'none');
            const visible = rows.length > 0;
            banner.classList.toggle('warning', visible && rows.every(row => row.classList.contains('notice-warning')));
            banner.style.display = visible ? 'flex' : 'none';
            document.getElementById('main-wrapper').classList.toggle('has-banner', visible);
            if (toastNotice) window.renderMessage(document.getElementById('generic-toast'), toastNotice);
            document.querySelectorAll('[data-notice-component="inline"]').forEach(el => {
                if (el._notice) window.renderMessage(el, el._notice);
            });
            restoreFocus();
        };
        window.modalSession = function(overlay, initialFocus, onClose) {
            const previousFocus = rememberFocus();
            const siblings = Array.from(document.body.children).filter(el => el !== overlay && el.tagName !== 'SCRIPT');
            const inert = siblings.map(el => el.inert);
            siblings.forEach(el => { el.inert = true; });
            overlay.style.display = 'flex';
            initialFocus.focus();
            function keydown(event) {
                if (event.key === 'Escape') { event.preventDefault(); onClose(false); }
                if (event.key !== 'Tab') return;
                const focusable = Array.from(overlay.querySelectorAll('button, a[href], input, select, textarea, [tabindex]'))
                    .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
                const index = focusable.indexOf(document.activeElement);
                event.preventDefault();
                if (focusable.length) focusable[(index + (event.shiftKey ? -1 : 1) + focusable.length) % focusable.length].focus();
            }
            document.addEventListener('keydown', keydown);
            return () => {
                document.removeEventListener('keydown', keydown);
                overlay.style.display = 'none';
                siblings.forEach((el, i) => { el.inert = inert[i]; });
                const invoker = previousFocus();
                if (invoker && !invoker.disabled) invoker.focus({preventScroll: true});
            };
        };

        let closeCurrentDialog = null;
        let modalMessage = null;
        function renderModalMessage() {
            if (!modalMessage) return;
            const restoreFocus = retainFocus(document.getElementById('modal-dialog'));
            const {spec, close} = modalMessage;
            const message = document.getElementById('modal-message');
            window.renderMessage(message, spec, () => close(true));
            const detailElement = document.getElementById('modal-detail');
            if (spec.detailKey) {
                window.renderMessage(detailElement, {key: spec.detailKey,
                    text: spec.detail === spec.detailKey ? '' : spec.detail,
                    args: spec.args, refs: spec.refs, field: spec.field}, () => close(true));
            } else detailElement.textContent = spec.detail || '';
            detailElement.hidden = !detailElement.textContent;
            document.getElementById('modal-detail-copy').hidden = !detailElement.textContent;
            const references = [...message.querySelectorAll('a[data-setting]'),
                ...detailElement.querySelectorAll('a[data-setting]')];
            const firstLink = references.find(link => link.dataset.setting === spec.field) || references[0];
            if (firstLink) firstLink.id = 'modal-jump-link';
            const jump = document.getElementById('modal-jump');
            jump.replaceChildren();
            if (spec.field && !firstLink) {
                const link = window.settingLink(spec.field, () => close(true));
                if (link) { link.id = 'modal-jump-link'; jump.appendChild(link); }
            }
            jump.style.display = jump.childElementCount ? 'block' : 'none';
            document.getElementById('modal-path').textContent = spec.path || '';
            document.getElementById('modal-dialog').classList.toggle('with-path', !!spec.path);
            document.getElementById('modal-ok').textContent = window.getT('btn_ok', 'OK');
            document.getElementById('modal-cancel').textContent = window.getT('btn_cancel', 'Cancel');
            restoreFocus();
        }
        window.appConfirm = function(message, options = {}) {
            if (closeCurrentDialog) closeCurrentDialog(false);
            const spec = Object.assign({}, typeof message === 'string' ? {text: message} : message, options);
            if (options.jumpField) spec.field = options.jumpField;
            if (spec.path && !/^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(spec.path)) {
                throw new Error('A filesystem location must be a path, not explanation text');
            }
            return new Promise(resolve => {
                const overlay = document.getElementById('modal-overlay');
                const ok = document.getElementById('modal-ok');
                const cancel = document.getElementById('modal-cancel');
                let endSession;
                function close(result) {
                    if (endSession) endSession();
                    ok.removeEventListener('click', accept);
                    cancel.removeEventListener('click', reject);
                    overlay.removeEventListener('mousedown', backdrop);
                    modalMessage = null;
                    closeCurrentDialog = null;
                    resolve(result);
                }
                function accept() { close(true); }
                function reject() { close(false); }
                function backdrop(event) { if (event.target === overlay) close(false); }
                closeCurrentDialog = close;
                modalMessage = {spec, close};
                renderModalMessage();
                cancel.style.display = spec.hideCancel ? 'none' : 'inline-block';
                ok.addEventListener('click', accept);
                cancel.addEventListener('click', reject);
                overlay.addEventListener('mousedown', backdrop);
                endSession = window.modalSession(overlay, ok, close);
            });
        };
        window.appAlert = function(message, options = {}) {
            return window.appConfirm(message, Object.assign({}, options, {hideCancel: true}));
        };
        window.showAboutDialog = function() {
            if (closeCurrentDialog) closeCurrentDialog(false);
            const overlay = document.getElementById('about-overlay');
            const ok = document.getElementById('about-ok');
            document.getElementById('about-dialog').setAttribute('aria-label', window.getT('btn_about'));
            ok.textContent = window.getT('btn_ok', 'OK');
            function close() {
                endSession();
                ok.removeEventListener('click', close);
                overlay.removeEventListener('mousedown', backdrop);
                closeCurrentDialog = null;
            }
            function backdrop(event) { if (event.target === overlay) close(); }
            ok.addEventListener('click', close);
            overlay.addEventListener('mousedown', backdrop);
            closeCurrentDialog = close;
            const endSession = window.modalSession(overlay, ok, close);
        };

        window.openExternalLink = function(key) {
            fetch('/api/about/open_link', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({target: key})
            }).then(response => response.json()).then(data => {
                if (data.status === 'success') window.clearNotice('browser-link');
                else window.reportFailure('browser-link', {key: 'err_open_link_failed', detail: data.url || data.message});
            }).catch(error => window.reportFailure('browser-link',
                {key: 'err_open_link_failed', detail: String(error)}));
        };

        window.openSettingsFile = function(target) {
            fetch('/api/settings/open_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target: target })
            })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') { window.clearNotice('settings-file'); return; }
                const spec = window.serverNotice(data);
                spec.args.path = '';
                window.notice(Object.assign({}, spec, {id: 'settings-file', summaryKey: 'notice_action_failed'}));
                window.appAlert(spec);
            })
            .catch(err => window.reportFailure('settings-file', {key: 'err_settings_open_unconfirmed', detail: String(err)}));
        };

        window.resetSettings = function() {
            const operation = window.settingsOperation();
            resetOperation = operation;
            fetch('/api/settings/reset', { method: 'POST', headers: window.settingsOperationHeaders(operation) })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    if (!acceptSettingsSnapshot(data)) return;
                    const preserve = new Set(Object.keys(window.draftState).filter(name =>
                        fieldRevisions[name] !== operation.revisions[name]));
                    reconcileSettings(preserve);
                    syncDraftControls(preserve);
                    window.diskErrors = { 'general': { type: 'i18n', value: 'error_settings_reset' } };
                    window.applyErrors = null;

                    const bpDisplay = document.getElementById('backup-path-display');
                    if (bpDisplay && data.backup_path) {
                        bpDisplay.innerText = data.backup_path;
                    }

                    window.renderErrors();
                    const fatalCorrupted = document.getElementById('fatal-corrupted-instructions');
                    if (fatalCorrupted) fatalCorrupted.style.display = 'none';
                    window.clearNotice('save');
                    window.clearNotice('reset');
                    if (typeof applyTranslations === 'function') applyTranslations();
                } else {
                    window.settingsOperationFailure(data, 'reset', 'err_reset_failed');
                }
            })
            .catch(err => {
                window.settingsResetUnconfirmed = true;
                window.reportFailure('reset', {key: 'err_settings_reset_unconfirmed', detail: String(err)});
                window.updateGlobalControls();
            });
        };

        function handleFieldChange(e) {
            const input = e.target;
            if (!input.name || input.type === 'hidden' || input.disabled) return;
            if (input.closest('[data-provider-inspection]')) { writeFieldToDOM(input.name); return; }

            window.draftState[input.name] = readInput(input);
            recordFieldEdit(input.name);
            if (input.name.startsWith('LLM_PROVIDERS.') || input.name.startsWith('ENV_TOKENS.')) {
                recordFieldEdit('LLM_PROVIDER', false);
            }
            for (const prefix of ['LLM_USER_PROMPT', 'LLM_SYSTEM_PROMPT']) {
                if (input.name === prefix) recordFieldEdit(prefix + '_MODE', false);
                if (input.name === prefix + '_MODE') recordFieldEdit(prefix, false);
            }

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
            window.updateAIDisabledWarning();
            window.renderErrors();
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

        function newSettingsSubmission() {
            const edits = {};
            for (const name in window.draftState) {
                if (isDirty(name)) edits[name] = structuredClone(window.draftState[name]);
            }
            return Object.assign(window.settingsOperation(), {edits,
                discarded: new Set(), cancelled: new Set()});
        }

        window.commitGlobalDraft = async function() {
            if (window.settingsSavePending || settingsRestartRequired) return;
            if (toastNotice && toastNotice.key === 'toast_saved') {
                clearTimeout(toastTimer);
                toastNotice = null;
                document.getElementById('generic-toast').classList.remove('toast-visible');
            }
            ui_logger.log("Applying Changes...", "UI", "INFO");
            const recovering = !!submission;
            if (!submission) submission = newSettingsSubmission();
            window.settingsSavePending = true;
            window.updateGlobalControls();
            try {
                for (let attempt = 0; attempt < (recovering ? 2 : 1); attempt++) {
                    const current = submission;
                    const response = await fetch('/api/settings/commit', {
                        method: 'POST', headers: window.settingsOperationHeaders(current),
                        body: JSON.stringify({edits: current.edits})
                    });
                    const data = await response.json();
                    if (data.status === 'success') {
                        if (!data.settings) throw new Error('Missing saved settings');
                        acknowledgeSettings(data, current.revisions);
                        ui_logger.log("Settings Synced Successfully.", "UI", "INFO");
                        window.applyErrors = null;
                        window.clearChangedSettingNotices();
                    } else if (data.status === 'error' || data.status === 'superseded') {
                        const preserve = new Set(Object.keys(window.draftState).filter(isDirty));
                        acceptSettingsSnapshot(data);
                        if (data.status === 'superseded') {
                            for (const name of Object.keys(current.edits)) {
                                const wipe = name.startsWith('ENV_TOKENS.')
                                    && acceptedSnapshot.token_wipes[name.slice('ENV_TOKENS.'.length)] > current.base;
                                const reset = acceptedSnapshot.reset_revision > current.base
                                    && (!resetOperation || (current.revisions[name] || 0) <= (resetOperation.revisions[name] || 0));
                                if ((wipe || reset) && fieldRevisions[name] === current.revisions[name]) preserve.delete(name);
                            }
                        }
                        reconcileSettings(preserve);
                        submission = null;
                        window.settingsSaveUnconfirmed = false;
                        window.applyErrors = data.operation_revision >= settingsRevision ? (data.errors || {}) : null;
                        syncDraftControls(preserve);
                        window.clearNotice('save');
                        if (data.status === 'superseded') {
                            window.notice({id: 'save', key: 'err_settings_changed_retry'});
                        } else if (window.isBrokenFileError(data.errors)) {
                            window.notice({id: 'save', key: 'toast_save_refused'});
                            ui_logger.log("Save REFUSED: settings.json on disk cannot be read, so nothing was written. Repair the file or reset it first.", "CONFIG", "WARNING");
                        } else {
                            const errCount = Object.keys(data.errors || {}).length;
                            ui_logger.log(`Settings synchronization failed. ${errCount} validation errors detected in Pydantic/Business logic.`, "CONFIG", "WARNING");
                        }
                        if (!recovering || attempt !== 0 || !window.hasUnsavedEdits()) break;
                    } else {
                        if (data.message_key === 'err_settings_operation_unknown') settingsRestartRequired = true;
                        throw new Error(data.message_key ? window.getT(data.message_key) : (data.message || 'Unrecognized settings response'));
                    }
                    if (recovering && attempt === 0 && window.hasUnsavedEdits()) {
                        submission = newSettingsSubmission();
                        continue;
                    }
                    window.clearNotice('save');
                    window.notice({surface: 'toast', key: 'toast_saved'});
                    break;
                }
            } catch (err) {
                ui_logger.log(`Settings outcome unconfirmed: ${err}`, "CONFIG", "ERROR");
                window.settingsSaveUnconfirmed = true;
                window.reportFailure('save', {key: settingsRestartRequired ? 'err_settings_operation_unknown' : 'err_settings_save_unconfirmed',
                    detail: settingsRestartRequired ? '' : String(err)});
            } finally {
                window.settingsSavePending = false;
                renderErrors();
                window.updateGlobalControls();
            }
        };

        window.discardGlobalDraft = function() {
            if (window.settingsSavePending || window.settingsSaveUnconfirmed || window.settingsResetUnconfirmed) return;
            ui_logger.log("Reverting all local changes to match disk state.", "CONFIG", "WARNING");
            window.draftState = Object.assign({}, window.originalState);
            editedFields.clear();
            window.applyErrors = null;
            syncDraftControls();
            if (window.draftState['GUI_LANGUAGE'] !== undefined) changeLanguage(window.draftState['GUI_LANGUAGE']);
            renderErrors();
            window.updateGlobalControls();
        };

        window.renderErrors = function() {
            if (!window.draftErrors) return;
            window._renderedErrors = window.draftErrors;

            const tabs = ['general', 'images', 'docs', 'animations', 'videos', 'ai', 'output', 'exports'];
            tabs.forEach(tab => {
                const warnIcon = document.getElementById('warn-tab-' + tab);
                if (warnIcon) warnIcon.style.display = 'none';
            });

            document.querySelectorAll('[data-field-error]').forEach(el => { el._notice = null; el.replaceChildren(); });
            document.querySelectorAll('form [name].error-field').forEach(el => el.classList.remove('error-field'));

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
                        fatalBanner.style.display = 'none';
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
                    if (err.type === 'i18n') text = window.getT(err.value);
                    else if (err.type === 'min_bound') text = window.getT('warn_min', 'Min:') + ' ' + err.value;
                    else if (err.type === 'max_bound') text = window.getT('warn_max', 'Max:') + ' ' + err.value;
                    window.notice({surface: 'inline', target: errEl, prefix: '⚠️ ',
                        key: err.type === 'i18n' ? err.value : '', text});
                }
            }

            const globalBanner = document.getElementById('global-error-banner');
            const globalBannerText = document.getElementById('global-error-text');
            const aiHintRow = document.getElementById('global-error-ai-hint');
            const wrapper = document.getElementById('main-wrapper');

            document.getElementById('field-error-summary').style.display = errorTabsFound.size ? 'flex' : 'none';
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
            window.renderNotices();
            renderModalMessage();
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
                    || Array.from(pane.querySelectorAll('[data-field-error]'))
                        .find(el => el.textContent.trim() !== '' && visible(el))
                    || null;
            });
        };

        window.goToField = function(fieldName) {
            const ref = window.settingRef(fieldName);
            if (ref && ref.tab) navigateAndPulse(ref.tab, () => {
                if (window.inspectProviderField) window.inspectProviderField(ref.element);
                ref.element.focus({preventScroll: true});
                return ref.element;
            });
        };

        function normalizeTokenFields(maskedTokens, preserve = new Set()) {
            document.querySelectorAll('input[name^="ENV_TOKENS."]').forEach(el => {
                const provider = el.name.slice('ENV_TOKENS.'.length);
                el.value = preserve.has(el.name) ? (window.draftState[el.name] || '') : '';
                if (maskedTokens && maskedTokens[provider]) {
                    el.placeholder = '********';
                    el.removeAttribute('data-i18n-placeholder');
                } else {
                    el.placeholder = window.getT('placeholder_token', 'Enter token here...');
                    el.dataset.i18nPlaceholder = 'placeholder_token';
                }
                el.classList.toggle('dirty-field', isDirty(el.name));
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
            window.__apiTokenReady.then(() => {
                attachSSEStream();
                reconcileRun();
            });

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

        function finishRun(outcome, message = {}) {
            const bar = document.getElementById('progress-bar');
            const status = document.getElementById('run-status');
            status.className = 'status-' + outcome;
            status.style.display = 'flex';

            if (outcome === 'done') {
                bar.classList.add('progress-done');
                const reportIssue = window.exportOutcome && window.exportOutcome.status === 'error';
                status.textContent = reportIssue
                    ? window.getT('run_status_export_incomplete', 'Processing finished; report saving needs attention')
                    : '✔ ' + window.getT('run_status_done', 'Run completed successfully');
                if (!window.exportOutcome || window.exportOutcome.status === 'success') {
                window.notice({surface: 'toast', key: 'run_status_done'});
                }
                ui_logger.log(window.getT('console_done', 'Processing Completed'), "CORE", "INFO");
            } else if (outcome === 'failed') {
                bar.classList.add('progress-failed');
                status.textContent = '✘ ' + window.getT('run_status_failed', 'Run failed — the process did not complete');
                const failure = message.message_key ? window.serverNotice(message) : {key: 'run_status_failed'};
                window.notice(Object.assign(failure, {id: 'run', summaryKey: 'notice_run_failed'}));
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

        let startRequestSequence = 0;
        let pendingRunId = null;
        let observedRevision = -1;
        let reconcileTimer = null;
        let startNoticeBeforeRun = null;

        function renderRunControls() {
            const phase = window.runState.phase;
            const stop = document.getElementById('btn-stop');
            if (!stop) return;
            const stopping = phase === 'stopping';
            stop.dataset.stopping = String(stopping);
            document.getElementById('btn-stop-icon').innerText = stopping ? '⏳' : '🛑';
            window.setButtonState(stop, phase === 'running', stopping
                ? window.getT('warn_already_stopping', 'Stopping - waiting for the current file to finish.')
                : (phase === 'running' ? '' : window.getT('warn_no_run_to_stop', 'Nothing is running.')));
            const label = document.getElementById('btn-stop-text');
            label.dataset.i18n = stopping ? 'btn_stopping' : 'btn_stop';
            label.textContent = window.getT(label.dataset.i18n);
        }

        function acceptRunState(snapshot) {
            if (!snapshot || !Number.isSafeInteger(snapshot.revision) || snapshot.revision <= observedRevision) return;
            if (pendingRunId && snapshot.run_id !== pendingRunId && !snapshot.is_running) return;
            const previous = window.runState;
            observedRevision = snapshot.revision;
            window.runState = snapshot;
            if (pendingRunId === snapshot.run_id || previous.run_id !== snapshot.run_id) {
                pendingRunId = null;
                window.finishAutomaticExportRun(snapshot.export_barrier);
                window.clearNotice('start');
                window.clearNotice('stop');
                if (window.notices.get('run') === startNoticeBeforeRun) window.clearNotice('run');
            }
            if (snapshot.export_result && window.receiveAutomaticExport) {
                window.receiveAutomaticExport(snapshot.export_result);
            }
            document.getElementById('progress-bar').value = snapshot.progress;
            renderRunControls();
            window.updateGlobalControls();
            if (modalMessage && modalMessage.spec.key === 'err_run_start_unconfirmed'
                    && modalMessage.spec.runId === snapshot.run_id) modalMessage.close(false);
            if (['done', 'failed', 'aborted'].includes(snapshot.phase)
                    && (previous.run_id !== snapshot.run_id || previous.phase !== snapshot.phase)) {
                finishRun(snapshot.phase, snapshot.terminal || {});
            }
        }

        async function reconcileRun() {
            clearTimeout(reconcileTimer);
            try {
                const response = await fetch('/api/process/status');
                if (!response.ok) throw new Error('Run status unavailable');
                acceptRunState(await response.json());
            } catch (error) {
                console.error('Failed to reconcile run status:', error);
            }
            if (pendingRunId || window.runState.phase === 'unknown') {
                reconcileTimer = setTimeout(reconcileRun, 1000);
            }
        }

        function attachSSEStream() {
            if (eventSource) return;
            eventSource = new EventSource('/api/process/stream?token=' + encodeURIComponent(window.API_TOKEN));
            eventSource.onopen = () => reconcileRun();
            eventSource.onmessage = function(e) {
                const msg = JSON.parse(e.data);
                if (msg.type === 'log') {
                    if (msg.ui_client_id && msg.ui_client_id === window.UI_CLIENT_ID) return;
                    ui_logger.render(msg);
                    return;
                }
                if (msg.run_state) acceptRunState(msg.run_state);
                if (msg.type === 'export_result' && window.receiveAutomaticExport) window.receiveAutomaticExport(msg);
            };
        }

        function startProcessing() {
            const startBtn = document.getElementById('btn-start');
            if (startBtn.disabled) return;
            const sequence = ++startRequestSequence;
            const runId = window.UI_CLIENT_ID + '-' + sequence;
            const previousState = window.runState;
            pendingRunId = runId;
            startNoticeBeforeRun = window.notices.get('run');
            window.runState = {...previousState, run_id: runId, phase: 'starting', progress: 0};
            renderRunControls();
            window.updateGlobalControls();
            document.getElementById('progress-bar').classList.remove('progress-failed', 'progress-done');
            document.getElementById('progress-bar').value = 0;
            const runStatus = document.getElementById('run-status');
            runStatus.style.display = 'none';
            runStatus.textContent = '';
            window.beginAutomaticExportRun();
            fetch('/api/process/start', {method: 'POST', headers: {'X-Run-Id': runId}})
                .then(response => response.json())
                .then(data => {
                    if (sequence !== startRequestSequence) return;
                    if (data.status === 'success') {
                        acceptRunState(data.run_state);
                        window.finishAutomaticExportRun();
                        return;
                    }
                    pendingRunId = null;
                    if (window.runState.run_id === runId && window.runState.phase === 'starting') {
                        window.runState = previousState;
                    }
                    acceptRunState(data.run_state);
                    renderRunControls();
                    window.updateGlobalControls();
                    window.finishAutomaticExportRun();
                    const refusal = window.serverNotice(data);
                    if (refusal.key === 'err_resume_old_database') refusal.path = '';
                    const spec = Object.assign(refusal, {id: 'start',
                        summaryKey: refusal.field ? 'notice_start_setting' : 'notice_start_failed',
                        clearOnFields: refusal.field ? [refusal.field] : []});
                    window.notice(spec);
                    window.appAlert(spec);
                    if (data.errors) {
                        window.diskErrors = data.errors;
                        window.applyErrors = null;
                        renderErrors();
                        window.updateGlobalControls();
                    }
                }).catch(async error => {
                    if (sequence !== startRequestSequence) return;
                    if (window.runState.run_id === runId && window.runState.phase === 'starting') {
                        window.runState = {...window.runState, phase: 'unknown'};
                        renderRunControls();
                        window.updateGlobalControls();
                    }
                    await reconcileRun();
                    if (pendingRunId === runId) {
                        console.error('Start acknowledgement unavailable:', error);
                        window.reportFailure('start', {key: 'err_run_start_unconfirmed',
                            summaryKey: 'notice_start_unconfirmed', runId});
                    }
                });
        }

        function stopProcessing() {
            const runId = window.runState.run_id;
            fetch('/api/process/stop', {method: 'POST', headers: {'X-Run-Id': runId}})
                .then(response => response.json())
                .then(data => {
                    if (runId !== window.runState.run_id || !window.runActive) return;
                    acceptRunState(data.run_state);
                    if (data.status === 'success') window.clearNotice('stop');
                    else if (window.runActive) window.reportFailure('stop', window.serverNotice(data));
                }).catch(async error => {
                    if (runId !== window.runState.run_id || !window.runActive) return;
                    await reconcileRun();
                    if (runId === window.runState.run_id && window.runState.phase === 'running') {
                        console.error('Stop acknowledgement unavailable:', error);
                        window.reportFailure('stop', {key: 'alert_stop_fail'});
                    }
                });
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
                helper.className = 'clipboard-helper';
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

