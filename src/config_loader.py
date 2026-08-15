# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json
import logging
import re
import threading
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import ValidationError

from config_validator import (
    ProviderConfig,
    Settings,
    SettingsAIDormant,
    resolve_ai_enabled,
    validate_business_rules,
)
from schemas import ConfigurationError

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _atomic_write_text(target_path: Path, content: str) -> None:
    tmp_path = target_path.with_name(f"{target_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def real_token_updates(env_tokens: Any) -> dict[str, str]:
    if not isinstance(env_tokens, dict):
        return {}
    updates = {}
    for prov, token in env_tokens.items():
        val = str(token).strip() if token else ""
        if val and val != "********":
            updates[prov] = val
    return updates


BROKEN_FILE_KEY = "err_broken_json"


def is_broken_file(errors: Any) -> bool:
    if not isinstance(errors, dict):
        return False
    general = errors.get("general")
    return isinstance(general, dict) and general.get("value") == BROKEN_FILE_KEY


class TokenManager:

    def __init__(self, env_path: Path):
        self.env_path = env_path
        self._lock = threading.RLock()

    def get_tokens(self) -> dict:
        tokens = {}
        with self._lock:
            if self.env_path.exists():
                try:
                    with open(self.env_path, encoding="utf-8") as f:
                        for line in f:
                            if "=" in line:
                                k, v = line.strip().split("=", 1)
                                tokens[k.strip()] = v.strip()
                except Exception:
                    pass
        return tokens

    def get_masked_tokens(self) -> dict:
        tokens = self.get_tokens()
        masked = {}
        for k in tokens:
            if k.endswith("_TOKEN"):
                provider = k.replace("_TOKEN", "").lower().replace("_", "-")
                masked[provider] = "********"
        return masked

    def update_tokens(self, updates: dict) -> None:
        if not updates:
            return
        with self._lock:
            self.env_path.parent.mkdir(parents=True, exist_ok=True)
            existing_env = self.get_tokens()
            real = real_token_updates(updates)
            for prov, token in updates.items():
                key = f"{prov.upper().replace('-', '_')}_TOKEN"
                if prov in real:
                    existing_env[key] = real[prov]
                    os.environ[key] = real[prov]
                elif token == "":
                    existing_env.pop(key, None)
                    os.environ.pop(key, None)
            _atomic_write_text(
                self.env_path, "".join(f"{k}={v}\n" for k, v in existing_env.items())
            )

class ConfigManager:

    _lock = threading.RLock()

    def __init__(self, root_dir: Path):
        with self._lock:
            self.root_dir = root_dir
            self.settings_path = root_dir / "settings.json"
            self.app_data_dir = self._get_app_data_dir()
            self.env_path = self.app_data_dir / ".env"
            self.token_manager = TokenManager(self.env_path)

    def _get_app_data_dir(self) -> Path:
        return (
            Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / "SevasMediaProcessor"
        )

    def get_env_tokens(self) -> dict:
        return self.token_manager.get_tokens()

    def get_masked_env_tokens(self) -> dict:
        return self.token_manager.get_masked_tokens()

    def load_for_ui(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            raw_data = {}
            if self.settings_path.exists():
                try:
                    with open(self.settings_path, encoding="utf-8") as f:
                        raw_data = json.load(f)
                except Exception as e:
                    logger.critical(f"Failed to read settings.json: {e}")
                    if isinstance(e, json.JSONDecodeError):
                        detail = f"Broken JSON: {e}"
                    else:
                        detail = f"Could not read the file: {type(e).__name__}: {e}"
                    return (
                        Settings().model_dump(mode="json"),
                        {"general": {"type": "i18n", "value": BROKEN_FILE_KEY,
                                     "detail": detail}},
                    )

            _, errors, merged = self._validate(raw_data)
            return merged, errors

    def validate_draft(
        self, raw_data: dict[str, Any]
    ) -> tuple[Settings | None, dict[str, Any], dict[str, Any]]:
        return self._validate(raw_data)

    def _validate(
        self, raw_data: dict[str, Any]
    ) -> tuple[Settings | None, dict[str, Any], dict[str, Any]]:
        errors = {}

        merged = Settings().model_dump(mode="json")

        def deep_update(base, update):
            for key, value in update.items():
                if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                    deep_update(base[key], value)
                else:
                    base[key] = value

        deep_update(merged, raw_data)

        ai_enabled = resolve_ai_enabled(merged.get("ENABLE_LLM_INFERENCE"))

        settings_obj = None
        try:
            model = Settings if ai_enabled else SettingsAIDormant
            settings_obj = model(**raw_data)
        except ValidationError as e:
            errors.update(self._parse_pydantic_errors(e))


        current_tokens = self.get_env_tokens()
        for key, value in os.environ.items():
            if key.endswith("_TOKEN"):
                current_tokens.setdefault(key, value)
        for prov, token in real_token_updates(raw_data.get("ENV_TOKENS")).items():
            current_tokens[f"{prov.upper().replace('-', '_')}_TOKEN"] = token

        for field_path, err_key in validate_business_rules(merged, current_tokens, ai_enabled):
            errors[field_path] = {"type": "i18n", "value": err_key}

        if ai_enabled:
            selected = str(merged.get("LLM_PROVIDER") or "")
            providers = merged.get("LLM_PROVIDERS")
            if isinstance(providers, dict):
                key = selected if selected in providers else "custom"
                entry = providers.get(key)
                if isinstance(entry, dict):
                    try:
                        ProviderConfig(**entry)
                    except ValidationError as e:
                        for loc, err in self._parse_pydantic_errors(e).items():
                            errors[f"LLM_PROVIDERS.{key}.{loc}"] = err

        if errors:
            settings_obj = None
        return settings_obj, errors, merged

    _PYDANTIC_TYPE_KEYS: ClassVar[dict[str, str]] = {
        "int_parsing": "err_valid_integer",
        "int_type": "err_valid_integer",
        "float_parsing": "err_valid_number",
        "float_type": "err_valid_number",
        "string_too_short": "err_missing_prompt",
    }
    _ERROR_KEY_RE = re.compile(r"err_[a-z_]+")

    def _parse_pydantic_errors(self, e: ValidationError) -> dict[str, Any]:
        errors = {}
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "").replace("Value error, ", "")
            err_type = err.get("type", "")
            ctx = err.get("ctx", {})

            if self._ERROR_KEY_RE.fullmatch(msg):
                entry = {"type": "i18n", "value": msg}
            elif err_type in ("less_than_equal", "less_than"):
                entry = {"type": "max_bound", "value": str(ctx.get("le", ctx.get("lt", "")))}
            elif err_type in ("greater_than_equal", "greater_than"):
                entry = {"type": "min_bound", "value": str(ctx.get("ge", ctx.get("gt", "")))}
            elif err_type in self._PYDANTIC_TYPE_KEYS:
                entry = {"type": "i18n", "value": self._PYDANTIC_TYPE_KEYS[err_type]}
            else:
                entry = {"type": "i18n", "value": msg}

            errors[loc] = entry
        return errors

    def load_strict(self) -> Settings:
        with self._lock:
            raw_data = {}
            if self.settings_path.exists():
                try:
                    with open(self.settings_path, encoding="utf-8") as f:
                        raw_data = json.load(f)
                except json.JSONDecodeError as e:
                    raise ConfigurationError(f"Settings file is corrupted: {e}") from e
                except Exception as e:
                    raise ConfigurationError(
                        f"Settings file could not be read: {type(e).__name__}: {e}"
                    ) from e

            settings_obj, errors, _ = self._validate(raw_data)
            if errors:
                first_err_loc = next(iter(errors))
                first_err_msg = errors[first_err_loc].get("value", "Validation error")
                raise ConfigurationError(f"Configuration error at {first_err_loc}: {first_err_msg}")

            assert settings_obj is not None
            return settings_obj

    def save_settings(self, settings: Settings):
        with self._lock:
            try:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(self.settings_path, settings.model_dump_json(indent=4))
            except Exception as e:
                logger.error(f"Failed to save settings: {e}")
                raise


_manager = ConfigManager(ROOT_DIR)


def get_app_data_dir():
    return _manager.app_data_dir


def get_settings_path():
    return _manager.settings_path


def get_env_file_path():
    return _manager.env_path


def get_env_tokens():
    return _manager.get_env_tokens()


def get_masked_env_tokens():
    return _manager.get_masked_env_tokens()


def update_env_tokens(updates: dict):
    _manager.token_manager.update_tokens(updates)


def load_strict():
    return _manager.load_strict()


def load_for_ui():
    return _manager.load_for_ui()


def validate_draft(raw_data: dict):
    return _manager.validate_draft(raw_data)


def save_settings(settings_obj: Settings):
    return _manager.save_settings(settings_obj)


def log_settings_errors(errors: dict):
    for loc, err_data in errors.items():
        val = err_data.get("value", "Unknown error")
        t = err_data.get("type", "raw")
        if t == "min_bound":
            logger.warning(f"Config: '{loc}' must be at least {val}.")
        elif t == "max_bound":
            logger.warning(f"Config: '{loc}' must be at most {val}.")
        else:
            logger.warning(f"Config: '{loc}': {val}")
