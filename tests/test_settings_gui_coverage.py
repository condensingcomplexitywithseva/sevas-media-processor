# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_validator import ProviderConfig, Settings

TEMPLATES = SRC / "templates"

CONTAINER_FIELDS = {
    "LLM_PROVIDERS": "container - edited through LLM_PROVIDERS.<provider>.<field> controls",
    "ENV_TOKENS": "container - edited through the ENV_TOKENS.<provider> token boxes",
}

CONTROL_NAME = re.compile(r'<(?:input|select|textarea)\b[^>]*\bname="([^"]+)"')


def control_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(TEMPLATES.rglob("*.html")):
        names.update(CONTROL_NAME.findall(path.read_text(encoding="utf-8")))
    return names


def top_level(names) -> set[str]:
    return {name.split(".")[0] for name in names}


def test_every_setting_has_a_form_control():
    present = top_level(control_names())
    missing = [
        field for field in Settings.model_fields
        if field not in present and field not in CONTAINER_FIELDS
    ]
    assert not missing, (
        "Settings fields with no GUI control (add a control on the tab the "
        "field's json_schema_extra 'tab' names, or list the field in "
        "CONTAINER_FIELDS with the reason it stays GUI-less):\n  "
        + "\n  ".join(missing)
    )


def test_container_fields_are_reached_through_dotted_controls():
    names = control_names()
    for field, reason in CONTAINER_FIELDS.items():
        assert field in Settings.model_fields, f"{field} is not a setting any more: {reason}"
        assert field not in names, f"{field} has a direct control now - drop it from CONTAINER_FIELDS"
        assert any(name.startswith(f"{field}.") for name in names), (
            f"{field} is listed as a container but no {field}.<...> control exists")


def test_no_control_names_an_unknown_setting():
    def names_a_setting(name: str) -> bool:
        head, _, rest = name.partition(".")
        if head not in Settings.model_fields:
            return False
        return head != "LLM_PROVIDERS" or rest.rsplit(".", 1)[-1] in ProviderConfig.model_fields

    unknown = [name for name in sorted(control_names()) if not names_a_setting(name)]
    assert not unknown, "form controls that name no setting:\n  " + "\n  ".join(unknown)


def test_every_control_sits_on_the_tab_its_schema_tag_names():
    tab_of_template = {
        path: path.stem.removesuffix("_content")
        for path in TEMPLATES.glob("tabs/*_content.html")
    }
    misplaced = []
    for path, tab in tab_of_template.items():
        for name in CONTROL_NAME.findall(path.read_text(encoding="utf-8")):
            field = Settings.model_fields.get(name.split(".")[0])
            if field is None:
                continue
            extra = field.json_schema_extra
            tagged = extra.get("tab") if isinstance(extra, dict) else None
            if tagged != tab:
                misplaced.append(f"{name}: control on tab '{tab}', schema says '{tagged}'")
    assert not misplaced, "\n  ".join(["controls on the wrong tab:", *misplaced])
