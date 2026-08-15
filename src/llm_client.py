# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import base64
import logging
import threading
import time
import requests
from pathlib import Path
from typing import Any
import os
import re

from config_loader import get_env_tokens
from fs_utils import get_safe_path, read_prompt
from schemas import Status, InferenceResult, ConfigurationError
import contextlib

network_client_logger = logging.getLogger("LLMNetworkClient")


class LLMRequestAbortedByUser(Exception):
    pass

class LLMClient:

    def __init__(self, settings, token: str | None = None):
        provider_config = settings.ACTIVE_PROVIDER_CONFIG

        self.target_endpoint_url = provider_config.url
        self.target_model_name = provider_config.model

        self.system_prompt_location = provider_config.system_prompt_location
        self.image_payload_style = provider_config.image_payload_style
        self.response_extraction_path = provider_config.response_extraction_path

        self.auth_header_key = provider_config.auth_header_key
        self.auth_header_format = provider_config.auth_header_format
        self.extra_header_key = provider_config.extra_header_key
        self.extra_header_value = provider_config.extra_header_value

        self.include_max_tokens = provider_config.require_max_tokens
        self.maximum_output_tokens = provider_config.max_tokens

        self.maximum_images_per_chunk = settings.MAX_JPEGS_PER_INFERENCE
        self.maximum_network_retries = settings.LLM_MAX_RETRIES
        self.network_timeout_duration_seconds = settings.LLM_TIMEOUT_SECONDS
        self.network_retry_sleep_seconds = settings.LLM_RETRY_SLEEP_SECONDS
        self.halt_batch_on_parse_error = settings.HALT_ON_LLM_PARSE_ERROR

        self.reasoning_handling = provider_config.reasoning_handling

        self.system_prompt_string = self._load_prompt_content(
            settings.LLM_SYSTEM_PROMPT,
            settings.LLM_SYSTEM_PROMPT_MODE
        )
        self.user_prompt_string = self._load_prompt_content(
            settings.LLM_USER_PROMPT,
            settings.LLM_USER_PROMPT_MODE
        )

        token_env_key = f"{settings.LLM_PROVIDER.upper().replace('-', '_')}_TOKEN"

        live_token = token or get_env_tokens().get(token_env_key)
        self.active_security_token = live_token or os.environ.get(token_env_key)

        if not self.active_security_token and settings.LLM_PROVIDER not in ["ollama", "lm-studio", "custom"]:
            network_client_logger.warning(
                f"No API key detected for {settings.LLM_PROVIDER}. "
                f"Client may encounter HTTP 401 Unauthorized errors.")


    def _load_prompt_content(self, input_val: str, mode: str) -> str:
        try:
            return read_prompt(input_val, mode)
        except Exception as e:
            network_client_logger.error(f"Failed to read prompt file ({input_val}): {e}")
            return ""

    def _abortable_sleep(self, seconds: float, abort_flag: Any | None) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if abort_flag is not None and abort_flag.is_set():
                return True
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        return abort_flag is not None and abort_flag.is_set()

    def _post_with_abort(self, headers: dict, payload: dict, abort_flag: Any | None) -> requests.Response:
        session = requests.Session()
        outcome = {}

        def _do_request():
            try:
                response = session.post(
                    self.target_endpoint_url,
                    headers=headers,
                    json=payload,
                    timeout=self.network_timeout_duration_seconds,
                    stream=True,
                )
                body_buffer = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if abort_flag is not None and abort_flag.is_set():
                        raise LLMRequestAbortedByUser()
                    if chunk:
                        body_buffer.extend(chunk)
                response._content = bytes(body_buffer)
                outcome["response"] = response
            except BaseException as e:
                outcome["error"] = e

        worker = threading.Thread(target=_do_request, daemon=True)
        worker.start()

        while worker.is_alive():
            worker.join(0.5)
            if abort_flag is not None and abort_flag.is_set():
                with contextlib.suppress(Exception):
                    session.close()
                raise LLMRequestAbortedByUser("LLM request aborted by user Stop command.")

        session.close()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["response"]

    def _encode_image_to_base64(self, image_path: Path) -> str:
        with open(get_safe_path(image_path), "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _encode_chunk_content(self, chunk_paths: list[Path], errors: list[str]) -> tuple[list[dict[str, Any]], int]:
        content: list[dict[str, Any]] = []
        if self.user_prompt_string:
            content.append({"type": "text", "text": self.user_prompt_string})

        encoded_count = 0
        for image_path in chunk_paths:
            try:
                encoded = self._encode_image_to_base64(image_path)

                if self.image_payload_style == "base64_dict":
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
                    })
                else:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    })
                encoded_count += 1
            except Exception as e:
                err_msg = f"Base64 Encoding Failure [{image_path.name}]: {e}"
                network_client_logger.error(err_msg)
                errors.append(err_msg)
                continue

        return content, encoded_count

    def _build_payload(self, content: list[dict[str, Any]]) -> dict:
        payload = {
            "model": self.target_model_name,
            "messages": [{"role": "user", "content": content}],
        }

        if self.system_prompt_string:
            if self.system_prompt_location == "top_level":
                payload["system"] = self.system_prompt_string
            else:
                payload["messages"].insert(0, {"role": "system", "content": self.system_prompt_string})

        if self.include_max_tokens:
            payload["max_tokens"] = self.maximum_output_tokens

        return payload

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}

        if self.active_security_token and self.auth_header_key:
            headers[self.auth_header_key] = self.auth_header_format.replace("{token}", self.active_security_token)

        if self.extra_header_key and self.extra_header_value:
            headers[self.extra_header_key] = self.extra_header_value

        return headers

    def _send_chunk_with_retries(self, chunk_num: int, headers: dict, payload: dict,
                                 abort_flag: Any | None, answers: list[str],
                                 errors: list[str]) -> tuple[bool, Any]:
        for attempt in range(1, self.maximum_network_retries + 1):
            try:
                try:
                    response = self._post_with_abort(headers, payload, abort_flag)
                except LLMRequestAbortedByUser:
                    msg = f"Chunk {chunk_num}: network call aborted by user."
                    network_client_logger.warning(msg)
                    errors.append(msg)
                    answers.append(f"--- Chunk {chunk_num} ---\n[ABORTED BY USER]\n")
                    return False, None

                if response.status_code in (401, 403):
                    msg = f"FATAL AUTHENTICATION ERROR: Server rejected credentials (HTTP {response.status_code})."
                    network_client_logger.critical(msg)
                    raise ConfigurationError(msg)

                if response.status_code == 400 and "API_KEY_INVALID" in response.text:
                    msg = "FATAL AUTHENTICATION ERROR: Server rejected credentials (HTTP 400, API_KEY_INVALID)."
                    network_client_logger.critical(msg)
                    raise ConfigurationError(msg)

                response.raise_for_status()
                return True, response.json()

            except requests.exceptions.RequestException as net_err:
                details = str(net_err)
                err_response = getattr(net_err, "response", None)

                if err_response is not None:
                    with contextlib.suppress(Exception):
                        details += f" | Server Response Body: {err_response.text}"

                network_client_logger.warning(f"Network attempt {attempt} failed for chunk {chunk_num}: {details}")

                if attempt < self.maximum_network_retries:
                    if self._abortable_sleep(self.network_retry_sleep_seconds, abort_flag):
                        msg = f"Chunk {chunk_num}: retry pause aborted by user."
                        network_client_logger.warning(msg)
                        errors.append(msg)
                        return False, None
                else:
                    msg = f"Chunk {chunk_num} Network Failure: {details}"
                    network_client_logger.error(msg)
                    answers.append(f"--- Chunk {chunk_num} ---\n[NETWORK FAILURE]\n")
                    errors.append(msg)
                    return False, None

        return False, None

    def _parse_response_text(self, chunk_num: int, parsed: Any, answers: list[str], errors: list[str]) -> str | None:
        try:
            finish_reason = None
            if "choices" in parsed:
                finish_reason = parsed["choices"][0].get("finish_reason")
            elif "stop_reason" in parsed:
                finish_reason = parsed.get("stop_reason")

            if finish_reason in ["length", "max_tokens"]:
                trunc_msg = "Token Limit Exceeded: AI response was abruptly cut off. Increase MAX_TOKENS."
                if self.halt_batch_on_parse_error:
                    raise ConfigurationError(trunc_msg)
                else:
                    network_client_logger.error(f"Chunk {chunk_num} Parse Error: {trunc_msg}")
                    errors.append(f"Chunk {chunk_num}: {trunc_msg}")
                    answers.append(f"--- Chunk {chunk_num} ---\n[TOKEN LIMIT EXCEEDED - SEE LOGS]\n")
                    return None
        except ConfigurationError:
            raise
        except Exception:
            pass

        try:
            if self.reasoning_handling == "parse_claude_blocks":
                content_array = parsed.get("content", [])
                text_blocks = []
                for block in content_array:
                    if block.get("type") == "text":
                        text_blocks.append(block.get("text", ""))
                text = "\n".join(text_blocks)

            else:
                path_parts = re.findall(r'[^.\[\]]+', self.response_extraction_path)
                current_node = parsed

                for part in path_parts:
                    if part.isdigit():
                        current_node = current_node[int(part)]
                    else:
                        current_node = current_node[part]

                text = current_node

            text = str(text or "")

            if self.reasoning_handling == "strip_xml":
                open_tags = text.count("<think>")
                close_tags = text.count("</think>")

                if open_tags == close_tags and open_tags > 0:
                    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                elif open_tags != close_tags:
                    msg = f"Chunk {chunk_num} LLM Formatting Error: Mismatched <think> tags."
                    network_client_logger.warning(msg)
                    errors.append(msg)

            if not text.strip():
                raise ValueError("The LLM returned an empty string or a null payload.")

        except (KeyError, IndexError, TypeError, ValueError) as schema_err:
            msg = f"FATAL PARSE ERROR: Unexpected JSON schema returned from server. Details: {schema_err}"
            if self.halt_batch_on_parse_error:
                network_client_logger.critical(msg)
                raise ConfigurationError(msg) from schema_err
            else:
                network_client_logger.error(f"Chunk {chunk_num} Parse Error: {msg}")
                errors.append(f"Chunk {chunk_num} Parse Error: {schema_err}")
                answers.append(f"--- Chunk {chunk_num} ---\n[PARSE ERROR - SEE LOGS]\n")
                return None

        return text

    def execute_network_inference(self, image_paths: list[Path], abort_flag: Any | None = None) -> InferenceResult:

        if not image_paths:
            network_client_logger.warning("Network error: No image paths provided.")
            return InferenceResult(status=Status.FAILURE.value,
                                   answer="No images provided for network inference.",
                                   error="No paths routed to LLM Client.")

        answers = []
        errors = []

        chunk_num = 0
        chunks_ok = 0

        headers = self._build_headers()

        for offset in range(0, len(image_paths), self.maximum_images_per_chunk):
            if abort_flag and abort_flag.is_set():
                network_client_logger.warning("LLM Inference sequence aborted by user flag.")
                errors.append("Inference sequence aborted by user.")
                break

            chunk_num += 1
            chunk_paths = image_paths[offset : offset + self.maximum_images_per_chunk]

            network_client_logger.info(f"LLM Network: Preparing chunk {chunk_num} ({len(chunk_paths)} frames)...")

            content, encoded_count = self._encode_chunk_content(chunk_paths, errors)

            if encoded_count == 0:
                err_msg = f"Chunk {chunk_num} aborted: Zero valid images encoded."
                network_client_logger.error(err_msg)
                errors.append(err_msg)
                answers.append(f"--- Chunk {chunk_num} ---\n[ENCODING FAILURE - ALL IMAGES DROPPED]\n")
                continue

            sent_ok, parsed = self._send_chunk_with_retries(
                chunk_num, headers, self._build_payload(content), abort_flag, answers, errors
            )
            if not sent_ok:
                continue

            text = self._parse_response_text(chunk_num, parsed, answers, errors)
            if text is None:
                continue

            answers.append(f"--- Chunk {chunk_num} ---\n{text.strip()}\n")
            chunks_ok += 1
            network_client_logger.debug(f"Chunk {chunk_num} completed successfully.")

        answer = "\n".join(answers)
        combined_error = " | ".join(errors)

        if chunks_ok == chunk_num and chunk_num > 0:
            status = Status.OK.value if not errors else Status.LLM_PARTIAL.value
            return InferenceResult(status=status, answer=answer, error=combined_error)
        elif chunks_ok > 0:
            return InferenceResult(status=Status.LLM_PARTIAL.value, answer=answer, error=combined_error)
        else:
            return InferenceResult(status=Status.LLM_FAILED.value,
                                   answer="[TOTAL LLM NETWORK FAILURE]", error=combined_error)
