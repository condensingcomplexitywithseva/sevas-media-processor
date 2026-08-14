# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import os
from pathlib import Path
from typing import Tuple, List, Dict, Any, Literal
from pydantic import BaseModel, Field, TypeAdapter, create_model, field_validator
from pydantic.fields import FieldInfo
from pydantic.fields import PydanticUndefined
from schemas import Status
from range_parsers import PageRangeSelector, VideoSelector
from fs_utils import read_prompt, get_safe_path

def current_run_folder(output_folder_path) -> Path:
    return Path(str(output_folder_path)) / "current_run"


def tech_folder_path(output_folder_path) -> Path:
    return current_run_folder(output_folder_path) / "TECH"


class ProviderConfig(BaseModel):
    url: str
    model: str
    auth_header_key: str = "Authorization"
    auth_header_format: str = "Bearer {token}"
    extra_header_key: str = ""
    extra_header_value: str = ""
    require_max_tokens: bool = False
    max_tokens: int = Field(default=10000, ge=1)

    system_prompt_location: Literal["messages", "top_level"] = "messages"
    image_payload_style: Literal["data_uri", "base64_dict"] = "data_uri"
    reasoning_handling: Literal["preserve", "strip_xml", "parse_claude_blocks", "ignore_api_field"] = "preserve"
    response_extraction_path: str = "choices[0].message.content"

def _default_provider_configs() -> Dict[str, ProviderConfig]:
    return {
        "openai": ProviderConfig(
            url="https://api.openai.com/v1/chat/completions",
            model="gpt-5.5",
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
            model="gemini-3.5-flash",
            reasoning_handling="preserve"
        ),
        "deepseek": ProviderConfig(
            url="https://api.deepseek.com/chat/completions",
            model="deepseek-v4-flash",
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
            model="qwen/qwen3.5:0.8b",
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


class Settings(BaseModel):

    GUI_LANGUAGE: str = Field(default="en", json_schema_extra={'tab': 'general'})
    INPUT_FOLDER_PATH: Path = Field(default="input", json_schema_extra={'tab': 'general'})
    OUTPUT_FOLDER_PATH: Path = Field(default="output", json_schema_extra={'tab': 'general'})
    LOGGING_LEVEL: str = Field(default="DEBUG", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$", json_schema_extra={'tab': 'general'})
    START_OVER: bool = Field(default=True, json_schema_extra={'tab': 'general'})
    NO_RETRY_STATUSES: List[Status] = Field(default=[Status.OK], json_schema_extra={'tab': 'general'})

    ENABLE_LLM_INFERENCE: bool = Field(default=False, json_schema_extra={'tab': 'general'})
    LLM_PROVIDER: str = Field(default="openai", pattern="^(openai|claude|gemini|deepseek|mistral|ollama|lm-studio|custom)$", json_schema_extra={'tab': 'ai'})

    LLM_PROVIDERS: Dict[str, Any] = Field(
        default_factory=lambda: {k: v.model_dump() for k, v in _default_provider_configs().items()},
        json_schema_extra={'tab': 'ai'},
    )

    LLM_USER_PROMPT: str = Field(default="Please accurately extract and transcribe all text from these images.", min_length=1, json_schema_extra={'tab': 'ai'})
    LLM_USER_PROMPT_MODE: str = Field(default="TEXT", pattern="^(TEXT|FILE)$", json_schema_extra={'tab': 'ai'})
    LLM_SYSTEM_PROMPT: str = Field(default="You are a helpful AI assistant. Output only the requested text.", min_length=1, json_schema_extra={'tab': 'ai'})
    LLM_SYSTEM_PROMPT_MODE: str = Field(default="TEXT", pattern="^(TEXT|FILE)$", json_schema_extra={'tab': 'ai'})

    MAX_JPEGS_PER_INFERENCE: int = Field(default=10, ge=1, json_schema_extra={'tab': 'ai'})
    MAX_CONSECUTIVE_LLM_FAILURES: int = Field(default=5, ge=1, json_schema_extra={'tab': 'ai'})
    HALT_ON_LLM_PARSE_ERROR: bool = Field(default=True, json_schema_extra={'tab': 'ai'})
    LLM_MAX_RETRIES: int = Field(default=3, ge=1, json_schema_extra={'tab': 'ai'})
    LLM_TIMEOUT_SECONDS: int = Field(default=1200, ge=1, json_schema_extra={'tab': 'ai'})
    LLM_RETRY_SLEEP_SECONDS: int = Field(default=3, ge=0, json_schema_extra={'tab': 'ai'})
    ENV_TOKENS: Dict[str, str] = Field(default_factory=dict, exclude=True, json_schema_extra={'tab': 'ai'})

    MAX_DIMENSION: int = Field(default=2560, ge=1, json_schema_extra={'tab': 'output'})
    JPEG_QUALITY: int = Field(default=90, ge=1, le=100, json_schema_extra={'tab': 'output'})
    PDF_SCALE: int = Field(default=2, ge=1, json_schema_extra={'tab': 'docs'})
    MAX_FILE_SIZE_KB: int = Field(default=500, ge=1, json_schema_extra={'tab': 'output'})
    LOWEST_QUALITY: int = Field(default=20, ge=1, le=100, json_schema_extra={'tab': 'output'})
    PILLOW_MAX_PIXELS: int = Field(default=89478485, ge=1, json_schema_extra={'tab': 'output'})

    WHITE_BACKGROUND: Tuple[int, int, int] = Field(default=(255, 255, 255), json_schema_extra={'tab': 'output'})

    OUTPUT_FILENAME_PREFIX_LENGTH: int = Field(default=20, ge=0, le=64, json_schema_extra={'tab': 'output'})
    OUTPUT_FILENAME_TIMESTAMPS: bool = Field(default=True, json_schema_extra={'tab': 'output'})

    VIDEO_MODE: str = Field(default="SUMMARY", pattern="^(SUMMARY|SAMPLING)$", json_schema_extra={'tab': 'videos'})
    VIDEO_RANGE: str = Field(default="", json_schema_extra={'tab': 'videos'})
    VIDEO_SUMMARY_TARGET_TOTAL_FRAMES: int = Field(default=10, ge=1, json_schema_extra={'tab': 'videos'})
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
        raw = self.LLM_PROVIDERS.get(self.LLM_PROVIDER)
        if not isinstance(raw, dict):
            raw = self.LLM_PROVIDERS.get("custom")
        return ProviderConfig(**raw) if isinstance(raw, dict) else ProviderConfig(url="", model="")

    @field_validator("LLM_PROVIDERS", mode="before")
    @classmethod
    def keep_unselected_providers_verbatim(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        complete = {k: v.model_dump() for k, v in _default_provider_configs().items()}
        for key, entry in value.items():
            base = complete.get(key)
            if isinstance(base, dict) and isinstance(entry, dict):
                base.update(entry)
            else:
                complete[key] = entry
        for key, entry in complete.items():
            if isinstance(entry, dict):
                try:
                    complete[key] = ProviderConfig(**entry).model_dump()
                except Exception:
                    pass
        return complete

    @field_validator("NO_RETRY_STATUSES", mode="before")
    @classmethod
    def parse_no_retry_statuses(cls, value: Any) -> List[Status]:
        if isinstance(value, str):
            return [Status(v.strip()) for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return [Status(v) if isinstance(v, str) else v for v in value]
        return value

    @field_validator("WHITE_BACKGROUND", mode="before")
    @classmethod
    def parse_white_background(cls, value: Any) -> Tuple[int, int, int]:
        if isinstance(value, str):
            try:
                parts = tuple(map(int, value.split(",")))
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    return parts
            except Exception: pass
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
        except Exception:
            raise ValueError("err_invalid_range")
        return value

    @field_validator("VIDEO_RANGE")
    @classmethod
    def validate_video_range(cls, value: str) -> str:
        try:
            VideoSelector(value)
        except Exception:
            raise ValueError("err_invalid_time_range")
        return value

    def apply_library_limits(self):
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = self.PILLOW_MAX_PIXELS
        except ImportError:
            pass



AI_TAB_FIELDS = frozenset(
    name for name, f in Settings.model_fields.items()
    if (f.json_schema_extra or {}).get("tab") == "ai"
)


def _carrier_field(original: FieldInfo):
    kwargs = {"json_schema_extra": original.json_schema_extra, "exclude": original.exclude}
    if original.default_factory is not None:
        kwargs["default_factory"] = original.default_factory
    elif original.default is not PydanticUndefined:
        kwargs["default"] = original.default
    return Field(**kwargs)


SettingsAIDormant = create_model(
    "SettingsAIDormant",
    __base__=Settings,
    **{name: (Any, _carrier_field(Settings.model_fields[name])) for name in AI_TAB_FIELDS},
)


def resolve_ai_enabled(raw_value: Any) -> bool:
    try:
        return TypeAdapter(bool).validate_python(raw_value)
    except Exception:
        return True


def validate_business_rules(
    settings: Dict[str, Any], env_tokens: Dict[str, str], ai_enabled: bool
) -> List[Tuple[str, str]]:
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

    if input_check == output_check or output_check.is_relative_to(input_check) or input_check.is_relative_to(output_check):
        errors.append(("OUTPUT_FOLDER_PATH", "err_path_overlap"))
        errors.append(("INPUT_FOLDER_PATH", "err_path_overlap"))

    if ai_enabled:
        provider = str(settings.get("LLM_PROVIDER") or "")
        providers = settings.get("LLM_PROVIDERS")
        provider_cfg = providers.get(provider) if isinstance(providers, dict) else None

        if not isinstance(provider_cfg, dict):
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

    return errors
