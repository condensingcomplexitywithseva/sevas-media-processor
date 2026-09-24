# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import os
from pathlib import Path
from typing import Any, Literal
from pydantic import (
    BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter, create_model, field_validator, model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from schemas import OverallResult
from range_parsers import PageRangeSelector, VideoSelector
from fs_utils import read_prompt, get_safe_path
from user_data import DEFAULT_INPUT_FOLDER, DEFAULT_OUTPUT_FOLDER
import copy
import json

def output_columns_error(raw: Any) -> str | None:
    from data_exporter import LLM_ANSWERS_REPORT_COLUMNS

    keys = parse_output_columns(raw)
    if not keys or any(not key for key in keys):
        return "err_output_columns_invalid"
    if any(len(key) > 64 or not key.isprintable() or '"' in key or "\\" in key
           for key in keys):
        return "err_output_columns_invalid"
    folded = [key.casefold() for key in keys]
    if len(set(folded)) != len(folded):
        return "err_output_columns_duplicate"
    reserved = {"page"} | {
        column[len("llm_"):].casefold()
        for column in LLM_ANSWERS_REPORT_COLUMNS
        if column.startswith("llm_")
    }
    if any(key in reserved for key in folded):
        return "err_output_columns_reserved"
    return None


def parse_output_columns(raw: Any) -> list[str]:
    if not isinstance(raw, str):
        return []
    lines = [line for line in raw.splitlines() if line.strip()]
    return [item.strip() for item in ",".join(lines).split(",")]


def current_run_folder(output_folder_path) -> Path:
    return Path(str(output_folder_path)) / "current_run"


def tech_folder_path(output_folder_path) -> Path:
    return current_run_folder(output_folder_path) / "TECH"


RETIRED_SETTINGS: frozenset[str] = frozenset({"EXPORT_JOINED_SHEET"})
RETIRED_PROVIDER_FIELDS: frozenset[str] = frozenset()


def _without_retired(data: Any, retired: frozenset[str]) -> Any:
    if isinstance(data, dict) and any(name in data for name in retired):
        return {key: value for key, value in data.items() if key not in retired}
    return data


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def drop_retired_fields(cls, data: Any) -> Any:
        return _without_retired(data, RETIRED_PROVIDER_FIELDS)

    url: str
    model: str
    auth_header_key: str = "Authorization"
    auth_header_format: str = "Bearer {token}"
    extra_header_key: str = ""
    extra_header_value: str = ""
    require_max_tokens: bool = False
    max_tokens: int = Field(default=64000, ge=1)
    max_tokens_field: str = "max_tokens"

    system_prompt_location: Literal["messages", "top_level"] = "messages"
    image_payload_style: Literal["data_uri", "base64_dict"] = "data_uri"
    reasoning_handling: Literal["preserve", "strip_xml", "parse_claude_blocks", "ignore_api_field"] = "preserve"
    response_extraction_path: str = "choices[0].message.content"

    @field_validator("max_tokens_field")
    @classmethod
    def validate_max_tokens_field(cls, value: str) -> str:
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("err_max_tokens_field_invalid")
        return value

def _default_provider_configs() -> dict[str, ProviderConfig]:
    return {
        "openai": ProviderConfig(
            url="https://api.openai.com/v1/chat/completions",
            model="gpt-5.6",
            max_tokens_field="max_completion_tokens",
            reasoning_handling="preserve"
        ),
        "claude": ProviderConfig(
            url="https://api.anthropic.com/v1/messages",
            model="claude-sonnet-5",
            auth_header_key="x-api-key",
            auth_header_format="{token}",
            extra_header_key="anthropic-version",
            extra_header_value="2023-06-01",
            require_max_tokens=True,
            system_prompt_location="top_level",
            image_payload_style="base64_dict",
            response_extraction_path="content[0].text",
            reasoning_handling="parse_claude_blocks"
        ),
        "gemini": ProviderConfig(
            url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model="gemini-3.8-flash",
            reasoning_handling="preserve"
        ),
        "deepseek": ProviderConfig(
            url="https://api.deepseek.com/chat/completions",
            model="deepseek-v4-flash-vision-exp",
            reasoning_handling="ignore_api_field"
        ),
        "mistral": ProviderConfig(
            url="https://api.mistral.ai/v1/chat/completions",
            model="mistral-medium-latest",
            reasoning_handling="preserve"
        ),
        "ollama": ProviderConfig(
            url="http://localhost:11434/v1/chat/completions",
            model="qwen3.5:0.8b",
            auth_header_key="",
            auth_header_format="",
            reasoning_handling="strip_xml"
        ),
        "lm-studio": ProviderConfig(
            url="http://localhost:1234/v1/chat/completions",
            model="qwen/qwen3.5-2b",
            auth_header_key="",
            auth_header_format="",
            reasoning_handling="strip_xml"
        ),
        "custom": ProviderConfig(
            url="",
            model="",
            reasoning_handling="preserve"
        )
    }


def provider_entry(providers: Any, selected: str) -> Any:
    defaults = _default_provider_configs()
    preset = defaults.get(selected)
    base = preset.model_dump() if preset is not None else {}
    if not isinstance(providers, dict):
        return providers
    if selected not in providers:
        return base
    entry = providers[selected]
    if not isinstance(entry, dict):
        return copy.deepcopy(entry)
    return {**base, **copy.deepcopy(entry)}


class Settings(BaseModel):

    model_config = ConfigDict(validate_default=True, extra="allow")
    _unsupplied_ai_fields: set[str] = PrivateAttr(default_factory=set)

    @model_validator(mode="before")
    @classmethod
    def drop_retired_settings(cls, data: Any) -> Any:
        return _without_retired(data, RETIRED_SETTINGS)

    GUI_LANGUAGE: str = Field(default="en", json_schema_extra={'tab': 'general'})
    INPUT_FOLDER_PATH: Path = Field(default_factory=lambda: Path(DEFAULT_INPUT_FOLDER),
                                    json_schema_extra={'tab': 'general'})
    OUTPUT_FOLDER_PATH: Path = Field(default_factory=lambda: Path(DEFAULT_OUTPUT_FOLDER),
                                     json_schema_extra={'tab': 'general'})
    LOGGING_LEVEL: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
                               json_schema_extra={'tab': 'general'})
    START_OVER: bool = Field(default=True, json_schema_extra={'tab': 'general'})
    NO_RETRY_STATUSES: list[OverallResult] = Field(default=[OverallResult.OK], json_schema_extra={'tab': 'general'})

    ENABLE_LLM_INFERENCE: bool = Field(default=False, json_schema_extra={'tab': 'general'})
    LLM_PROVIDER: str = Field(default="gemini",
                              pattern="^(openai|claude|gemini|deepseek|mistral|ollama|lm-studio|custom)$",
                              json_schema_extra={'tab': 'ai'})

    LLM_PROVIDERS: dict[str, Any] = Field(
        default_factory=lambda: {k: v.model_dump() for k, v in _default_provider_configs().items()},
        json_schema_extra={'tab': 'ai'},
    )

    LLM_USER_PROMPT: str = Field(
        default="Describe succinctly what you can see in the image, video or document. Do not overthink.",
        min_length=1, json_schema_extra={'tab': 'ai'})
    LLM_USER_PROMPT_MODE: str = Field(default="TEXT", pattern="^(TEXT|FILE)$", json_schema_extra={'tab': 'ai'})
    LLM_SYSTEM_PROMPT: str = Field(
        default="You are a helpful AI assistant. Reply only with what is asked for.",
        min_length=1, json_schema_extra={'tab': 'ai'})
    LLM_SYSTEM_PROMPT_MODE: str = Field(default="TEXT", pattern="^(TEXT|FILE)$", json_schema_extra={'tab': 'ai'})

    LLM_OUTPUT_MODE: Literal["table_per_file", "table_per_page"] = Field(
        default="table_per_file", json_schema_extra={'tab': 'ai'})
    LLM_OUTPUT_COLUMNS: str = Field(default="answer", json_schema_extra={'tab': 'ai'})
    LLM_OUTPUT_COLUMN_INSTRUCTIONS: str = Field(default="", json_schema_extra={'tab': 'ai'})
    LLM_JSON_MAX_ATTEMPTS: int = Field(default=3, ge=1, json_schema_extra={'tab': 'ai'})
    LLM_ABORT_ON_MALFORMED_JSON: bool = Field(default=False, json_schema_extra={'tab': 'ai'})

    MAX_JPEGS_PER_INFERENCE: int = Field(default=150, ge=1, json_schema_extra={'tab': 'ai'})
    MAX_CONSECUTIVE_LLM_FAILURES: int = Field(default=5, ge=1, json_schema_extra={'tab': 'ai'})
    HALT_ON_LLM_PARSE_ERROR: bool = Field(default=True, json_schema_extra={'tab': 'ai'})
    LLM_MAX_RETRIES: int = Field(default=3, ge=1, json_schema_extra={'tab': 'ai'})
    LLM_TIMEOUT_SECONDS: int = Field(default=1200, ge=1, json_schema_extra={'tab': 'ai'})
    LLM_RETRY_SLEEP_SECONDS: int = Field(default=3, ge=0, json_schema_extra={'tab': 'ai'})
    ENV_TOKENS: dict[str, str] = Field(default_factory=dict, exclude=True, json_schema_extra={'tab': 'ai'})

    MAX_DIMENSION: int = Field(default=1920, ge=1, json_schema_extra={'tab': 'output'})
    JPEG_QUALITY: int = Field(default=90, ge=1, le=100, json_schema_extra={'tab': 'output'})
    PDF_SCALE: int = Field(default=2, ge=1, json_schema_extra={'tab': 'docs'})
    MAX_FILE_SIZE_KB: int = Field(default=250, ge=1, json_schema_extra={'tab': 'output'})
    LOWEST_QUALITY: int = Field(default=20, ge=1, le=100, json_schema_extra={'tab': 'output'})
    PILLOW_MAX_PIXELS: int = Field(default=89478485, ge=1, json_schema_extra={'tab': 'output'})

    WHITE_BACKGROUND: tuple[int, int, int] = Field(default=(255, 255, 255), json_schema_extra={'tab': 'output'})

    OUTPUT_FILENAME_PREFIX_LENGTH: int = Field(default=20, ge=0, le=64, json_schema_extra={'tab': 'output'})
    OUTPUT_FILENAME_TIMESTAMPS: bool = Field(default=True, json_schema_extra={'tab': 'output'})

    VIDEO_MODE: str = Field(default="SUMMARY", pattern="^(SUMMARY|SAMPLING)$", json_schema_extra={'tab': 'videos'})
    VIDEO_RANGE: str = Field(default="", json_schema_extra={'tab': 'videos'})
    VIDEO_SUMMARY_TARGET_TOTAL_FRAMES: int = Field(default=150, ge=1, json_schema_extra={'tab': 'videos'})
    VIDEO_SUMMARY_SCENE_SENSITIVITY: float = Field(default=5.0, ge=0.0, json_schema_extra={'tab': 'videos'})
    VIDEO_SAMPLING_CAPTURE_RATE_FPS: float = Field(default=1.0, gt=0.0, json_schema_extra={'tab': 'videos'})
    VIDEO_SAMPLING_MAX_FRAMES_BUDGET: int = Field(default=100, ge=1, json_schema_extra={'tab': 'videos'})
    VIDEO_SAMPLING_SCENE_SENSITIVITY: float = Field(default=0.0, ge=0.0, json_schema_extra={'tab': 'videos'})

    ANIMATION_RANGE: str = Field(default="", json_schema_extra={'tab': 'animations'})
    ANIMATION_TARGET_TOTAL_FRAMES: int = Field(default=10, ge=1, json_schema_extra={'tab': 'animations'})
    ANIMATION_SCENE_SENSITIVITY: float = Field(default=5.0, ge=0.0, json_schema_extra={'tab': 'animations'})

    IMAGE_RANGE: str = Field(default="", json_schema_extra={'tab': 'images'})
    DOCUMENT_RANGE: str = Field(default="", json_schema_extra={'tab': 'docs'})
    DOCUMENT_MAX_PAGES: int = Field(default=1000, ge=1, json_schema_extra={'tab': 'docs'})

    @property
    def CURRENT_RUN_FOLDER(self) -> Path:
        return current_run_folder(self.OUTPUT_FOLDER_PATH)

    @property
    def TECH_FOLDER_PATH(self) -> Path:
        return tech_folder_path(self.OUTPUT_FOLDER_PATH)

    @property
    def ACTIVE_PROVIDER_CONFIG(self) -> ProviderConfig:
        raw = provider_entry(self.LLM_PROVIDERS, self.LLM_PROVIDER)
        return ProviderConfig(**raw) if isinstance(raw, dict) else ProviderConfig(url="", model="")

    @field_validator("LLM_PROVIDERS", mode="before")
    @classmethod
    def preserve_provider_data(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        return {key: copy.deepcopy(_without_retired(entry, RETIRED_PROVIDER_FIELDS))
                for key, entry in value.items()}

    @field_validator("NO_RETRY_STATUSES", mode="before")
    @classmethod
    def parse_no_retry_statuses(cls, value: Any) -> list[OverallResult]:
        if isinstance(value, str):
            return [OverallResult(v.strip()) for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return [OverallResult(v) if isinstance(v, str) else v for v in value]
        return value

    @field_validator("WHITE_BACKGROUND", mode="before")
    @classmethod
    def parse_white_background(cls, value: Any) -> tuple[int, int, int]:
        if isinstance(value, str):
            try:
                parts = tuple(map(int, value.split(",")))
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    return parts
            except Exception:
                pass
            raise ValueError("err_white_bg_format")
        if isinstance(value, (tuple, list)) and len(value) == 3:
            if all(isinstance(p, int) and 0 <= p <= 255 for p in value):
                return tuple(value)
            raise ValueError("err_rgb_range")
        raise ValueError("err_white_bg_format")

    @field_validator("LOWEST_QUALITY")
    @classmethod
    def validate_quality_floor(cls, value: int, info) -> int:
        jpeg_quality = info.data.get("JPEG_QUALITY")
        if isinstance(jpeg_quality, int) and value > jpeg_quality:
            raise ValueError("err_quality_floor_above_start")
        return value

    @field_validator("IMAGE_RANGE", "DOCUMENT_RANGE", "ANIMATION_RANGE")
    @classmethod
    def validate_page_ranges(cls, value: str) -> str:
        try:
            PageRangeSelector(value)
        except Exception as error:
            raise ValueError("err_invalid_range") from error
        return value

    @field_validator("VIDEO_RANGE")
    @classmethod
    def validate_video_range(cls, value: str) -> str:
        try:
            VideoSelector(value)
        except Exception as error:
            raise ValueError("err_invalid_time_range") from error
        return value

    def apply_library_limits(self):
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = self.PILLOW_MAX_PIXELS
        except ImportError:
            pass



AI_TAB_FIELDS = frozenset(
    name for name, f in Settings.model_fields.items()
    if isinstance(f.json_schema_extra, dict) and f.json_schema_extra.get("tab") == "ai"
)



IDENTITY_SETTINGS = frozenset({
    "INPUT_FOLDER_PATH", "ENABLE_LLM_INFERENCE",
    "LLM_PROVIDER", "LLM_PROVIDERS", "LLM_USER_PROMPT", "LLM_USER_PROMPT_MODE",
    "LLM_SYSTEM_PROMPT", "LLM_SYSTEM_PROMPT_MODE", "LLM_OUTPUT_MODE", "LLM_OUTPUT_COLUMNS",
    "LLM_OUTPUT_COLUMN_INSTRUCTIONS", "MAX_JPEGS_PER_INFERENCE",
    "MAX_DIMENSION", "JPEG_QUALITY", "PDF_SCALE", "MAX_FILE_SIZE_KB", "LOWEST_QUALITY",
    "WHITE_BACKGROUND", "OUTPUT_FILENAME_PREFIX_LENGTH", "OUTPUT_FILENAME_TIMESTAMPS",
    "VIDEO_MODE", "VIDEO_RANGE", "VIDEO_SUMMARY_TARGET_TOTAL_FRAMES",
    "VIDEO_SUMMARY_SCENE_SENSITIVITY", "VIDEO_SAMPLING_CAPTURE_RATE_FPS",
    "VIDEO_SAMPLING_MAX_FRAMES_BUDGET", "VIDEO_SAMPLING_SCENE_SENSITIVITY",
    "ANIMATION_RANGE", "ANIMATION_TARGET_TOTAL_FRAMES", "ANIMATION_SCENE_SENSITIVITY",
    "IMAGE_RANGE", "DOCUMENT_RANGE", "DOCUMENT_MAX_PAGES",
})
NON_IDENTITY_SETTINGS = frozenset({
    "GUI_LANGUAGE", "OUTPUT_FOLDER_PATH", "LOGGING_LEVEL", "START_OVER", "NO_RETRY_STATUSES",
    "ENV_TOKENS", "LLM_MAX_RETRIES", "LLM_TIMEOUT_SECONDS", "LLM_RETRY_SLEEP_SECONDS",
    "LLM_JSON_MAX_ATTEMPTS", "HALT_ON_LLM_PARSE_ERROR", "LLM_ABORT_ON_MALFORMED_JSON",
    "MAX_CONSECUTIVE_LLM_FAILURES",
    "PILLOW_MAX_PIXELS",
})
IDENTITY_PROVIDER_FIELDS = frozenset({
    "url", "model", "extra_header_key", "extra_header_value", "system_prompt_location",
    "image_payload_style", "reasoning_handling", "response_extraction_path",
})
NON_IDENTITY_PROVIDER_FIELDS = frozenset({
    "auth_header_key", "auth_header_format",
    "require_max_tokens", "max_tokens", "max_tokens_field",
})


def classification_errors(fields, identity: frozenset[str], other: frozenset[str]) -> list[str]:
    declared = set(fields)
    return sorted(
        [f"{name}: unclassified" for name in declared - identity - other]
        + [f"{name}: on both sides" for name in declared & identity & other]
        + [f"{name}: not a declared field" for name in (identity | other) - declared])


def _carrier_field(original: FieldInfo):
    kwargs = {"json_schema_extra": original.json_schema_extra, "exclude": original.exclude}
    if original.default_factory is not None:
        kwargs["default_factory"] = original.default_factory
    elif original.default is not PydanticUndefined:
        kwargs["default"] = original.default
    return Field(**kwargs)


_carrier_fields: dict[str, Any] = {
    name: (Any, _carrier_field(Settings.model_fields[name])) for name in AI_TAB_FIELDS
}
SettingsAIDormant = create_model(
    "SettingsAIDormant",
    __base__=Settings,
    **_carrier_fields,
)


def resolve_ai_enabled(raw_value: Any) -> bool:
    try:
        return TypeAdapter(bool).validate_python(raw_value)
    except Exception:
        return True


def validate_business_rules(
    settings: dict[str, Any], env_tokens: dict[str, str], ai_enabled: bool
) -> list[tuple[str, str]]:
    errors = []

    input_raw = str(settings.get("INPUT_FOLDER_PATH") or "")
    output_raw = str(settings.get("OUTPUT_FOLDER_PATH") or "")
    try:
        input_path = Path(input_raw).resolve()
        output_path = Path(output_raw).resolve()
    except Exception:
        input_path = Path(input_raw).absolute()
        output_path = Path(output_raw).absolute()

    input_path_check = Path(get_safe_path(input_path))
    if not input_path_check.exists():
        errors.append(("INPUT_FOLDER_PATH", "err_input_missing"))
    elif not input_path_check.is_dir():
        errors.append(("INPUT_FOLDER_PATH", "err_input_not_dir"))

    input_check = Path(os.path.normcase(str(input_path)))
    output_check = Path(os.path.normcase(str(output_path)))

    if (input_check == output_check or output_check.is_relative_to(input_check)
            or input_check.is_relative_to(output_check)):
        errors.append(("OUTPUT_FOLDER_PATH", "err_path_overlap"))
        errors.append(("INPUT_FOLDER_PATH", "err_path_overlap"))

    if ai_enabled:
        provider = str(settings.get("LLM_PROVIDER") or "")
        providers = settings.get("LLM_PROVIDERS")
        provider_cfg = providers.get(provider) if isinstance(providers, dict) else None

        if provider not in _default_provider_configs():
            errors.append(("LLM_PROVIDER", "err_unknown_provider"))
        elif not isinstance(provider_cfg, dict):
            errors.append(("LLM_PROVIDER", "err_fix_ai_settings"))
        else:
            if not str(provider_cfg.get("url") or "").strip():
                errors.append((f"LLM_PROVIDERS.{provider}.url", "err_missing_url"))
            if not str(provider_cfg.get("model") or "").strip():
                errors.append((f"LLM_PROVIDERS.{provider}.model", "err_missing_model"))

            if provider not in ["ollama", "lm-studio", "custom"]:
                token_env_key = f"{provider.upper().replace('-', '_')}_TOKEN"
                if not env_tokens.get(token_env_key):
                    errors.append((f"ENV_TOKENS.{provider}", "err_missing_token"))

        def check_prompt(val, mode, field):
            v = str(val or "").strip()
            if not v:
                errors.append((field, "err_missing_prompt"))
                return
            if mode == "FILE":
                prefix = "user" if "USER" in field else "sys"
                try:
                    exists = Path(get_safe_path(Path(v))).is_file()
                except Exception:
                    exists = False
                if not exists:
                    errors.append((field, f"err_{prefix}_file_missing"))
                    return
                try:
                    content = read_prompt(v, mode)
                except ValueError:
                    errors.append((field, f"err_{prefix}_file_unreadable"))
                    return
                except Exception:
                    errors.append((field, f"err_{prefix}_file_missing"))
                    return
                if not content.strip():
                    errors.append((field, f"err_{prefix}_file_empty"))

        check_prompt(settings.get("LLM_USER_PROMPT"), settings.get("LLM_USER_PROMPT_MODE"), "LLM_USER_PROMPT")
        check_prompt(settings.get("LLM_SYSTEM_PROMPT"), settings.get("LLM_SYSTEM_PROMPT_MODE"), "LLM_SYSTEM_PROMPT")

        columns_error = output_columns_error(settings.get("LLM_OUTPUT_COLUMNS"))
        if columns_error:
            errors.append(("LLM_OUTPUT_COLUMNS", columns_error))

    return errors


def settings_form_view(settings: dict[str, Any]) -> dict[str, Any]:
    def display(value: Any, boolean: bool = False) -> Any:
        if boolean:
            try:
                return TypeAdapter(bool).validate_python(value)
            except ValueError:
                pass
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True)
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
            return str(value)
        return value

    view = {name: (copy.deepcopy(value) if name in {"NO_RETRY_STATUSES", "WHITE_BACKGROUND"}
                   else display(value, Settings.model_fields[name].annotation is bool))
            for name, value in settings.items()
            if name in Settings.model_fields and name not in {"LLM_PROVIDERS", "ENV_TOKENS"}}
    providers = {}
    for name in _default_provider_configs():
        entry = provider_entry(settings.get("LLM_PROVIDERS"), name)
        if not isinstance(entry, dict):
            entry = {}
        providers[name] = {field: display(entry.get(field), spec.annotation is bool)
                           for field, spec in ProviderConfig.model_fields.items()}
    view["LLM_PROVIDERS"] = providers
    return view


def settings_form_repair_fields(settings: dict[str, Any]) -> list[str]:
    repairs = []
    for name, props in Settings.model_json_schema()['properties'].items():
        if props.get('type') == 'string' and not isinstance(settings.get(name), str):
            repairs.append(name)
    fields = ProviderConfig.model_json_schema()['properties']
    for provider in _default_provider_configs():
        entry = provider_entry(settings.get('LLM_PROVIDERS'), provider)
        for name, props in fields.items():
            if props.get('type') == 'string' and (not isinstance(entry, dict)
                                                or not isinstance(entry.get(name), str)):
                repairs.append(f'LLM_PROVIDERS.{provider}.{name}')
    return repairs
