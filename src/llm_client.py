# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import base64
import json
import logging
import math
import threading
import time
import requests
from pathlib import Path
from typing import Any
from collections.abc import Callable
import os
import re

from config_loader import get_env_tokens
from config_validator import NON_IDENTITY_PROVIDER_FIELDS, ProviderConfig, parse_output_columns
from fs_utils import get_safe_path, read_prompt
from schemas import AnswerRow, RequestOutcome, RequestStatus, ConfigurationError
import contextlib

network_client_logger = logging.getLogger("LLMNetworkClient")

_TRUNCATION_MSG = "Token Limit Exceeded: AI response was abruptly cut off. Increase MAX_TOKENS."

_INVISIBLE_CODEPOINTS = str.maketrans("", "", "\ufeff\u200b\u200c\u200d\u2060")

_REPLAY_CAP_CHARS = 64 * 1024
_REPLAY_TRUNCATION_MARKER = "\n[reply cut here for the retry]"

_RETRIED_STATUSES = frozenset({408, 409, 429})

_JSON_OPENER = re.compile(r"[\[{]")

_MAX_OPENERS_TRIED = 256


def dumps_model(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def first_non_finite(value: Any) -> str | None:
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, float) and not math.isfinite(node):
            return repr(node)
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def plan_requests(frames: list[tuple[int, Path, str]],
                  max_per_request: int) -> list[tuple[int, list[tuple[int, Path, str]]]]:
    return [
        (request_number, frames[offset : offset + max_per_request])
        for request_number, offset in enumerate(
            range(0, len(frames), max_per_request), start=1)
    ]


def not_attempted_outcomes(request_plan: list[tuple[int, list[tuple[int, Path, str]]]],
                           attempted_count: int, reason: str) -> list[RequestOutcome]:
    return [
        RequestOutcome(request_number, tuple(page for page, _, _ in request_frames),
                       RequestStatus.NOT_ATTEMPTED.value, "", reason)
        for request_number, request_frames in request_plan[attempted_count:]
    ]


class LLMRequestAbortedByUser(Exception):
    pass

class LLMClient:

    def __init__(self, settings, token: str | None = None):
        provider_config = settings.ACTIVE_PROVIDER_CONFIG
        self.provider_config = provider_config

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
        self.max_tokens_field = provider_config.max_tokens_field

        self.maximum_images_per_request = settings.MAX_JPEGS_PER_INFERENCE
        self.maximum_network_retries = settings.LLM_MAX_RETRIES
        self.network_timeout_duration_seconds = settings.LLM_TIMEOUT_SECONDS
        self.network_retry_sleep_seconds = settings.LLM_RETRY_SLEEP_SECONDS
        self.halt_batch_on_parse_error = settings.HALT_ON_LLM_PARSE_ERROR

        self.output_mode = settings.LLM_OUTPUT_MODE
        self.declared_columns = parse_output_columns(settings.LLM_OUTPUT_COLUMNS)
        self.json_max_attempts = settings.LLM_JSON_MAX_ATTEMPTS
        self.abort_on_malformed_json = settings.LLM_ABORT_ON_MALFORMED_JSON
        self.json_contract_text = self._build_json_contract(
            settings.LLM_OUTPUT_COLUMN_INSTRUCTIONS)

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


    def processing_semantics(self) -> dict[str, Any]:
        provider = {name: getattr(self.provider_config, name) for name in ProviderConfig.model_fields
                    if name not in NON_IDENTITY_PROVIDER_FIELDS}
        header = (provider.pop("extra_header_key", "").lower(), provider.pop("extra_header_value", ""))
        if all(header):
            provider["extra_header"] = header
        names = ("maximum_images_per_request", "output_mode", "declared_columns",
                 "json_contract_text", "system_prompt_string", "user_prompt_string")
        return {"provider": provider, **{name: getattr(self, name) for name in names}}

    def _build_json_contract(self, column_instructions: str) -> str:
        keys_list = ", ".join(json.dumps(key, ensure_ascii=False)
                              for key in self.declared_columns)
        if self.output_mode == "table_per_page":
            contract = ("Return ONLY a JSON array with one object per image. "
                        "Each object must have exactly these keys: "
                        f'"page" (integer - copy from the image label), {keys_list}.')
        else:
            contract = ("Return ONLY a single JSON object. It must have "
                        f"exactly these keys: {keys_list}.")
        if column_instructions.strip():
            contract += f" Column meanings: {column_instructions}"
        contract += (" Do not wrap the JSON in markdown fences. "
                     "No commentary before or after the JSON.")
        return contract

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
        if abort_flag is not None and abort_flag.is_set():
            raise LLMRequestAbortedByUser("LLM request aborted by user Stop command.")

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

    def _encode_request_content(self, request_frames: list[tuple[int, Path, str]],
                              errors: list[str], first_ordinal: int,
                              total_images: int, abort_flag: Any | None = None,
                              ) -> tuple[list[dict[str, Any]], int, list[int]]:
        image_parts: list[dict[str, Any]] = []
        sent_ordinals: list[int] = []
        sent_pages: list[int] = []
        for offset, (page_number, image_path, timestamp) in enumerate(request_frames):
            if abort_flag is not None and abort_flag.is_set():
                raise LLMRequestAbortedByUser("Stopped while preparing images.")
            try:
                encoded = self._encode_image_to_base64(image_path)
            except Exception as e:
                err_msg = f"Base64 Encoding Failure [{image_path.name}]: {e}"
                network_client_logger.error(err_msg)
                errors.append(err_msg)
                continue

            label = (f"Image {first_ordinal + offset} of {total_images} - "
                     f"page {page_number}")
            if timestamp:
                label += f", extracted at {timestamp}"
            image_parts.append({"type": "text", "text": label + "."})

            if self.image_payload_style == "base64_dict":
                image_parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
                })
            else:
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                })
            sent_ordinals.append(first_ordinal + offset)
            sent_pages.append(page_number)

        content: list[dict[str, Any]] = []
        if self.user_prompt_string:
            content.append({"type": "text", "text": self.user_prompt_string})
        if not sent_ordinals:
            phrase = ""
        elif len(sent_ordinals) == 1:
            phrase = f"image {sent_ordinals[0]}"
        elif sent_ordinals == list(range(sent_ordinals[0], sent_ordinals[-1] + 1)):
            phrase = f"images {sent_ordinals[0]}-{sent_ordinals[-1]}"
        else:
            listed = ", ".join(str(o) for o in sent_ordinals[:-1])
            phrase = f"images {listed} and {sent_ordinals[-1]}"
        if phrase:
            content.append({"type": "text", "text":
                            f"This request contains {phrase} of "
                            f"{total_images} from one file."})
        content.extend(image_parts)
        content.append({"type": "text", "text": self.json_contract_text})
        return content, len(sent_ordinals), sent_pages

    def _build_payload(self, content: list[dict[str, Any]],
                       extra_messages: list[dict[str, Any]] | None = None) -> dict:
        payload = {
            "model": self.target_model_name,
            "messages": [{"role": "user", "content": content},
                         *(extra_messages or [])],
        }

        if self.system_prompt_string:
            if self.system_prompt_location == "top_level":
                payload["system"] = self.system_prompt_string
            else:
                payload["messages"].insert(0, {"role": "system", "content": self.system_prompt_string})

        if self.include_max_tokens:
            payload[self.max_tokens_field] = self.maximum_output_tokens

        return payload

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}

        if self.active_security_token and self.auth_header_key:
            headers[self.auth_header_key] = self.auth_header_format.replace("{token}", self.active_security_token)

        if self.extra_header_key and self.extra_header_value:
            headers[self.extra_header_key] = self.extra_header_value

        return headers

    def _send_request_with_retries(self, request_num: int, headers: dict, payload: dict,
                                 abort_flag: Any | None,
                                 notes: list[str]) -> tuple[str, Any, str]:
        for attempt in range(1, self.maximum_network_retries + 1):
            try:
                try:
                    response = self._post_with_abort(headers, payload, abort_flag)
                except LLMRequestAbortedByUser:
                    msg = f"Request {request_num}: network call aborted by user."
                    network_client_logger.warning(msg)
                    notes.append(msg)
                    return RequestStatus.ABORTED_BY_USER.value, None, ""

                if response.status_code in (401, 403):
                    msg = f"FATAL AUTHENTICATION ERROR: Server rejected credentials (HTTP {response.status_code})."
                    network_client_logger.critical(msg)
                    raise ConfigurationError(msg)

                if response.status_code == 400 and "API_KEY_INVALID" in response.text:
                    msg = "FATAL AUTHENTICATION ERROR: Server rejected credentials (HTTP 400, API_KEY_INVALID)."
                    network_client_logger.critical(msg)
                    raise ConfigurationError(msg)

                status_code = response.status_code
                if 400 <= status_code < 500 and status_code not in _RETRIED_STATUSES:
                    details = f"HTTP {status_code} rejected by the server"
                    with contextlib.suppress(Exception):
                        details += f" | Server Response Body: {response.text}"
                    msg = f"Request {request_num} Network Failure: {details}"
                    network_client_logger.error(msg)
                    notes.append(msg)
                    return RequestStatus.NETWORK_FAILURE.value, None, ""

                response.raise_for_status()
                try:
                    parsed_reply = response.json()
                except RecursionError as depth_error:
                    raise requests.exceptions.RequestException(
                        f"Unreadable reply body: {depth_error}") from depth_error
                return RequestStatus.OK.value, parsed_reply, response.text

            except requests.exceptions.RequestException as net_err:
                details = str(net_err)
                err_response = getattr(net_err, "response", None)

                if err_response is not None:
                    with contextlib.suppress(Exception):
                        details += f" | Server Response Body: {err_response.text}"

                network_client_logger.warning(f"Network attempt {attempt} failed for request {request_num}: {details}")

                if attempt < self.maximum_network_retries:
                    if self._abortable_sleep(self.network_retry_sleep_seconds, abort_flag):
                        msg = f"Request {request_num}: retry pause aborted by user."
                        network_client_logger.warning(msg)
                        notes.append(msg)
                        return RequestStatus.ABORTED_BY_USER.value, None, ""
                else:
                    msg = f"Request {request_num} Network Failure: {details}"
                    network_client_logger.error(msg)
                    notes.append(msg)
                    return RequestStatus.NETWORK_FAILURE.value, None, ""

        return RequestStatus.NETWORK_FAILURE.value, None, ""

    def _parse_response_text(self, request_num: int, parsed: Any, raw_body: str,
                             notes: list[str]) -> tuple[str, str]:
        truncated = False
        try:
            finish_reason = None
            if "choices" in parsed:
                finish_reason = parsed["choices"][0].get("finish_reason")
            elif "stop_reason" in parsed:
                finish_reason = parsed.get("stop_reason")

            if finish_reason in ["length", "max_tokens"]:
                network_client_logger.error(f"Request {request_num} Parse Error: {_TRUNCATION_MSG}")
                notes.append(f"Request {request_num}: {_TRUNCATION_MSG}")
                truncated = True
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
                    msg = f"Request {request_num} LLM Formatting Error: Mismatched <think> tags."
                    network_client_logger.warning(msg)
                    notes.append(msg)

            if not text.translate(_INVISIBLE_CODEPOINTS).strip():
                raise ValueError("The LLM returned an empty string or a null payload.")

        except (KeyError, IndexError, TypeError, ValueError) as schema_err:
            if truncated:
                return RequestStatus.TOKEN_LIMIT_EXCEEDED.value, raw_body
            msg = f"FATAL PARSE ERROR: Unexpected JSON schema returned from server. Details: {schema_err}"
            network_client_logger.error(f"Request {request_num} Parse Error: {msg}")
            notes.append(f"Request {request_num} Parse Error: {schema_err}")
            return RequestStatus.PROVIDER_REPLY_PARSE_ERROR.value, raw_body

        if truncated:
            return RequestStatus.TOKEN_LIMIT_EXCEEDED.value, text
        return RequestStatus.OK.value, text

    def _extract_json_value(self, text: str,
                            decoder: json.JSONDecoder) -> tuple[Any, str | None]:
        expected_kind = list if self.output_mode == "table_per_page" else dict
        first_value: Any = None
        found_any = False
        last_error = "no JSON found in the reply"
        position = 0
        openers_tried = 0
        while (match := _JSON_OPENER.search(text, position)) is not None:
            openers_tried += 1
            if openers_tried > _MAX_OPENERS_TRIED:
                return None, (f"no JSON value among the first {_MAX_OPENERS_TRIED} "
                              "brackets of the reply")
            start = match.start()
            try:
                value, end = decoder.raw_decode(text, start)
            except (RecursionError, MemoryError) as depth_error:
                return None, f"unparseable JSON: {depth_error}"
            except ValueError as parse_error:
                last_error = f"unparseable JSON: {parse_error}"
                position = start + 1
                continue
            if isinstance(value, expected_kind):
                return value, None
            if not found_any:
                first_value, found_any = value, True
            position = end
        if found_any:
            return first_value, None
        return None, last_error

    @staticmethod
    def _encode_page_claim(claim: Any) -> str:
        if isinstance(claim, int) and not isinstance(claim, bool):
            return str(claim)
        return json.dumps(claim, ensure_ascii=False)

    def _judge_answer_text(self, answer_text: str,
                           sent_pages: list[int]) -> tuple[tuple[AnswerRow, ...], str | None]:
        def _reject_constant(name: str):
            raise ValueError(f"{name} is not valid JSON")

        parsed, extraction_error = self._extract_json_value(
            answer_text, json.JSONDecoder(parse_constant=_reject_constant))
        if extraction_error is not None:
            return (), extraction_error
        non_finite = first_non_finite(parsed)
        if non_finite is not None:
            return (), f"non-finite number {non_finite} is not valid JSON"

        if self.output_mode == "table_per_file":
            if not isinstance(parsed, dict):
                return (), "expected a single JSON object"
            missing = [key for key in self.declared_columns if key not in parsed]
            if missing:
                return (), f"object is missing declared keys: {', '.join(missing)}"
            values = {key: parsed[key] for key in self.declared_columns}
            return (AnswerRow(None, "", "", dumps_model(values)),), None

        if not isinstance(parsed, list):
            return (), "expected a JSON array"
        if not parsed:
            return (), "empty JSON array"
        if not all(isinstance(row, dict) for row in parsed):
            return (), "every array item must be a JSON object"
        for row in parsed:
            missing = [key for key in self.declared_columns if key not in row]
            if missing:
                return (), f"a row is missing declared keys: {', '.join(missing)}"

        rows: list[AnswerRow] = []
        verified_pages: set[int] = set()
        misaligned = False
        for row in parsed:
            claim = row.get("page")
            values_json = dumps_model({key: row[key] for key in self.declared_columns})
            genuine = (isinstance(claim, int) and not isinstance(claim, bool)
                       and claim in sent_pages)
            if genuine and claim not in verified_pages:
                verified_pages.add(claim)
                rows.append(AnswerRow(claim, str(claim), "", values_json))
            elif genuine:
                misaligned = True
                rows.append(AnswerRow(
                    claim, str(claim), "duplicate_page_number", values_json))
            else:
                misaligned = True
                raw_claim = "" if "page" not in row else self._encode_page_claim(claim)
                rows.append(AnswerRow(
                    None, raw_claim, "hallucinated_page_number", values_json))

        for page in sent_pages:
            if page not in verified_pages:
                rows.append(AnswerRow(page, "", "no_row_returned", ""))
        if misaligned:
            return tuple(rows), "page numbers do not match the images sent"
        return tuple(rows), None

    def _corrective_messages(self, failed_answer: str, problem: str) -> list[dict[str, Any]]:
        shape = "array" if self.output_mode == "table_per_page" else "object"
        correction = (f"Your previous reply was not valid JSON: {problem}. "
                      f"Reply with ONLY the JSON {shape}, no fences, "
                      "no commentary.")
        if len(failed_answer) > _REPLAY_CAP_CHARS:
            failed_answer = failed_answer[:_REPLAY_CAP_CHARS] + _REPLAY_TRUNCATION_MARKER
        return [{"role": "assistant", "content": failed_answer},
                {"role": "user", "content": correction}]

    def _process_request(self, request_num: int, request_frames: list[tuple[int, Path, str]],
                       headers: dict, abort_flag: Any | None,
                       first_ordinal: int, total_images: int) -> RequestOutcome:
        notes: list[str] = []
        planned_pages = tuple(page for page, _, _ in request_frames)

        try:
            content, encoded_count, sent_pages = self._encode_request_content(
                request_frames, notes, first_ordinal, total_images, abort_flag)
            if abort_flag is not None and abort_flag.is_set():
                raise LLMRequestAbortedByUser("Stopped while preparing images.")
        except LLMRequestAbortedByUser as stopped:
            return RequestOutcome(request_num, planned_pages, RequestStatus.ABORTED_BY_USER.value,
                                  "", str(stopped))
        pages = tuple(sent_pages)

        if encoded_count == 0:
            err_msg = f"Request {request_num} aborted: Zero valid images encoded."
            network_client_logger.error(err_msg)
            notes.append(err_msg)
            return RequestOutcome(request_num, planned_pages,
                                  RequestStatus.ENCODING_FAILURE.value, "",
                                  " | ".join(notes))

        extra_messages: list[dict[str, Any]] | None = None
        text = ""
        kept_rows: tuple[AnswerRow, ...] = ()
        for attempt in range(1, self.json_max_attempts + 1):
            if abort_flag is not None and abort_flag.is_set():
                return RequestOutcome(request_num, pages, RequestStatus.ABORTED_BY_USER.value,
                                      text.strip(), "Stopped before preparing the request payload.")
            try:
                status, parsed, raw_body = self._send_request_with_retries(
                    request_num, headers,
                    self._build_payload(content, extra_messages), abort_flag, notes)
            except ConfigurationError as fatal:
                fatal.halted_request_answer = text.strip()
                fatal.halted_request_pages = pages
                raise
            if status != RequestStatus.OK.value:
                return RequestOutcome(request_num, pages, status,
                                      text.strip(), " | ".join(notes))

            status, text = self._parse_response_text(request_num, parsed, raw_body, notes)
            if status != RequestStatus.OK.value:
                return RequestOutcome(request_num, pages, status,
                                      text.strip(), " | ".join(notes))

            rows, problem = self._judge_answer_text(text, sent_pages)
            if problem is None:
                if attempt > 1:
                    notes.append(f"Request {request_num}: succeeded on attempt {attempt}.")
                network_client_logger.debug(f"Request {request_num} completed successfully.")
                return RequestOutcome(request_num, pages,
                                      RequestStatus.OK.value, text.strip(),
                                      " | ".join(notes), answer_rows=rows)

            msg = f"Request {request_num}: invalid JSON on attempt {attempt} ({problem})."
            network_client_logger.warning(msg)
            notes.append(msg)
            kept_rows = rows
            extra_messages = self._corrective_messages(text, problem)

        return RequestOutcome(request_num, pages,
                              RequestStatus.INVALID_JSON_ANSWER.value,
                              text.strip(), " | ".join(notes), answer_rows=kept_rows)

    def execute_network_inference(self, frames: list[tuple[int, Path, str]],
                                  abort_flag: Any | None = None, *,
                                  on_outcome: Callable[[RequestOutcome], None] | None = None) -> list[RequestOutcome]:
        if not frames:
            network_client_logger.warning("Network error: No frames provided for inference.")
            return []

        outcomes: list[RequestOutcome] = []
        request_plan = plan_requests(frames, self.maximum_images_per_request)
        headers = self._build_headers()

        def saved(outcome: RequestOutcome) -> RequestOutcome:
            if on_outcome is None:
                return outcome
            on_outcome(outcome)
            return outcome._replace(raw_answer="", answer_rows=())

        def remaining(reason: str) -> list[RequestOutcome]:
            return [saved(outcome) for outcome in
                    not_attempted_outcomes(request_plan, len(outcomes), reason)]

        for request_num, request_frames in request_plan:
            if abort_flag and abort_flag.is_set():
                network_client_logger.warning("LLM Inference sequence aborted by user flag.")
                outcomes.extend(remaining(
                    "Not attempted: run aborted by user before this request."))
                break

            network_client_logger.info(
                f"LLM Network: Preparing request {request_num} ({len(request_frames)} frames)...")
            try:
                outcome = self._process_request(
                    request_num, request_frames, headers, abort_flag,
                    first_ordinal=(request_num - 1) * self.maximum_images_per_request + 1,
                    total_images=len(frames))
            except ConfigurationError as fatal:
                outcomes.append(saved(RequestOutcome(
                    request_num, fatal.halted_request_pages,
                    RequestStatus.NETWORK_FAILURE.value,
                    fatal.halted_request_answer, str(fatal))))
                outcomes.extend(remaining(
                    "Not attempted: the batch halted before this request."))
                fatal.request_outcomes = list(outcomes)
                raise

            outcomes.append(saved(outcome))

            if (self.abort_on_malformed_json
                    and outcome.status == RequestStatus.INVALID_JSON_ANSWER.value):
                halt_msg = ("FATAL: the AI's answer was not valid JSON after "
                            f"{self.json_max_attempts} attempt(s).")
                network_client_logger.critical(halt_msg)
                halt = ConfigurationError(halt_msg)
                outcomes.extend(remaining(
                    "Not attempted: the batch halted before this request."))
                halt.request_outcomes = list(outcomes)
                raise halt

            if self.halt_batch_on_parse_error and outcome.status in (
                    RequestStatus.TOKEN_LIMIT_EXCEEDED.value,
                    RequestStatus.PROVIDER_REPLY_PARSE_ERROR.value):
                if outcome.status == RequestStatus.TOKEN_LIMIT_EXCEEDED.value:
                    halt_msg = _TRUNCATION_MSG
                else:
                    halt_msg = ("FATAL PARSE ERROR: Unexpected JSON schema "
                                f"returned from server. {outcome.error}")
                network_client_logger.critical(halt_msg)
                halt = ConfigurationError(halt_msg)
                outcomes.extend(remaining(
                    "Not attempted: the batch halted before this request."))
                halt.request_outcomes = list(outcomes)
                raise halt

        return outcomes
