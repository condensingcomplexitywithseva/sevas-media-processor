# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import secrets
from flask import Flask, render_template, request, jsonify
from routes.settings_api import api_blueprint
from routes.execution_api import execution_bp
from routes.export_api import exports_bp
from routes.about_api import about_bp
from config_loader import (Settings, ROOT_DIR, get_env_file_path, get_masked_env_tokens,
                           get_app_data_dir, get_settings_path)
from config_validator import ProviderConfig
from version import APP_VERSION, APP_LINKS

SESSION_TOKEN = secrets.token_urlsafe(32)

OPEN_PATHS = ("/",)
OPEN_PREFIXES = ("/static/",)


def is_open_path(path: str) -> bool:
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)

def _field_kind(props: dict) -> str:
    schema_type = props.get('type')
    if schema_type in ('boolean', 'integer', 'number'):
        return schema_type
    if schema_type == 'array':
        items = props.get('items') or {}
        prefix = props.get('prefixItems') or []
        if items.get('type') == 'integer' or (prefix and all(p.get('type') == 'integer' for p in prefix)):
            return 'csv_ints'
        return 'string_list'
    if schema_type == 'object':
        return 'object'
    return 'string'


def build_field_meta():
    field_meta = {}
    for field, props in Settings.model_json_schema().get('properties', {}).items():
        field_meta[field] = {'tab': props.get('tab'), 'kind': _field_kind(props)}
    provider_field_kinds = {
        field: _field_kind(props)
        for field, props in ProviderConfig.model_json_schema().get('properties', {}).items()
    }
    return field_meta, provider_field_kinds


def get_translations():
    locales_dir = ROOT_DIR / "src" / "locales"
    translations = {}
    if locales_dir.exists():
        for filepath in locales_dir.glob("*.json"):
            lang = filepath.stem
            try:
                with open(filepath, encoding="utf-8") as f:
                    translations[lang] = json.load(f)
            except Exception:
                translations[lang] = {}
    return translations

def get_available_locales(translations):
    locales = {}
    for code, data in translations.items():
        locales[code] = data.get("locale_name", code.capitalize())
    return locales

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.register_blueprint(api_blueprint, url_prefix='/api')
    app.register_blueprint(execution_bp, url_prefix='/api/process')
    app.register_blueprint(exports_bp, url_prefix='/api/export')
    app.register_blueprint(about_bp, url_prefix='/api/about')

    @app.before_request
    def _enforce_local_api_auth():
        host_name = (request.host or "").split(":")[0].lower()
        if host_name not in ("127.0.0.1", "localhost"):
            return jsonify({"error": "Forbidden"}), 403

        if not is_open_path(request.path):
            supplied = request.headers.get("X-App-Token") or request.args.get("token") or ""
            if not secrets.compare_digest(supplied, SESSION_TOKEN):
                return jsonify({"error": "Forbidden"}), 403



    @app.route('/')
    def index():
        field_meta, provider_field_kinds = build_field_meta()

        from config_loader import load_for_ui
        merged_settings, initial_errors = load_for_ui()
        env_path_str = str(get_env_file_path())
        log_path_str = str(get_app_data_dir() / "logs")

        current_settings = dict(merged_settings)
        current_settings['ENV_TOKENS'] = get_masked_env_tokens()
        translations = get_translations()

        return render_template(
            'main.html',
            settings=merged_settings,
            current_settings=current_settings,
            default_settings=Settings().model_dump(mode='json'),
            active_tab='general',
            translations=translations,
            available_locales=get_available_locales(translations),
            env_tokens=get_masked_env_tokens(),
            env_path=env_path_str,
            log_path=log_path_str,
            settings_path=str(get_settings_path()),
            initial_errors=initial_errors,
            field_meta=field_meta,
            provider_field_kinds=provider_field_kinds,
            app_version=APP_VERSION,
            app_links=APP_LINKS,
        )

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=False, port=5000, use_reloader=False)
