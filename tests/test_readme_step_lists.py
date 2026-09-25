# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

HEADING = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
STEP_START = re.compile(r"^(\d+)\.\s+(.*)$")
CONTINUATION = re.compile(r"^\s{3,}(\S.*)$")
BACKTICKED = re.compile(r"`([^`]+)`")
BADGE_REPO = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/actions/")
FENCE = re.compile(r"^\s*(```|~~~)")

DEFAULT_BRANCH = "main"

FORBIDDEN_STEP_FORMS = {
    "parenthetical pointer": re.compile(r"\([^)]*\babove\b[^)]*\)", re.I),
    "'as described'": re.compile(r"\bas described\b", re.I),
    "'see the note' / 'see step'": re.compile(r"\bsee (the note|step|section|above|below)\b", re.I),
    "'instead'": re.compile(r"\binstead\b", re.I),
    "'do not run'": re.compile(r"\bdo not (run|use|extract)\b", re.I),
    "'step N' pointer": re.compile(r"\b(under|in|of|to) step \d+\b", re.I),
    "'Option N' pointer": re.compile(r"\bOption \d\b"),
}

CARRY_OVER_FORMS = re.compile(r"\b(after|once you|having|then)\b", re.I)


def numbered_lists(text):
    lists = {}
    lead_ins = {}
    heading = "(no heading)"
    in_fence = False
    steps = None
    previous_nonblank = ""
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        found = HEADING.match(line)
        if found:
            heading = found.group(2)
            steps = None
            previous_nonblank = ""
            continue
        start = STEP_START.match(line)
        if start:
            if steps is None or int(start.group(1)) == 1:
                steps = []
                key, number = heading, 2
                while key in lists:
                    key, number = f"{heading} (list {number})", number + 1
                lists[key] = steps
                lead_ins[key] = previous_nonblank
            steps.append(start.group(2).strip())
            continue
        more = CONTINUATION.match(line)
        if more and steps is not None and steps:
            steps[-1] = steps[-1] + " " + more.group(1).strip()
            continue
        if line.strip():
            previous_nonblank = line.strip()
            steps = None
    return lists, lead_ins


def readme_lists():
    return numbered_lists(README.read_text(encoding="utf-8"))


def backticked(step):
    return BACKTICKED.findall(step)


def steps_under(heading_fragment):
    lists, _ = readme_lists()
    matches = [h for h in lists if heading_fragment in h]
    assert len(matches) == 1, (
        f"expected exactly one README heading containing {heading_fragment!r}, "
        f"found {matches}"
    )
    return lists[matches[0]]


def readme_slug():
    found = BADGE_REPO.search(README.read_text(encoding="utf-8"))
    assert found, "the README badge URL no longer names the repository"
    return found.group(2)



def test_the_parser_finds_the_known_lists():
    lists, lead_ins = readme_lists()
    headings = list(lists)
    for expected in ("First: download and extract", "Option 1: Copy and paste the setup script",
                     "Option 2: Manual Setup", "Update with the setup script", "Update by hand"):
        assert any(expected in h for h in headings), (expected, headings)
    assert len(steps_under("Update by hand")) >= 10
    assert lead_ins["Option 1: Copy and paste the setup script"].endswith("To run it:")
    assert lead_ins["Option 1: Copy and paste the setup script (list 2)"] == "What the setup script does:"


def test_two_lists_under_one_heading_are_both_kept():
    text = (
        "## A\n"
        "What it does:\n"
        "1. Describes\n"
        "To run it:\n"
        "1. Acts\n"
        "2. Finishes\n"
    )
    lists, lead_ins = numbered_lists(text)
    assert lists == {"A": ["Describes"], "A (list 2)": ["Acts", "Finishes"]}
    assert lead_ins == {"A": "What it does:", "A (list 2)": "To run it:"}


def test_the_parser_joins_continuation_lines_and_skips_fences():
    text = (
        "## A\n"
        "Lead-in:\n"
        "1. First line\n"
        "   continued here\n"
        "2. Second\n"
        "```powershell\n"
        "1. not a step\n"
        "```\n"
        "## B\n"
        "1. Fresh list\n"
    )
    lists, lead_ins = numbered_lists(text)
    assert lists == {"A": ["First line continued here", "Second"], "B": ["Fresh list"]}
    assert lead_ins == {"A": "Lead-in:", "B": ""}


@pytest.mark.parametrize("form, sample", [
    ("parenthetical pointer", "Do the thing (Option 3: Manual Setup, above)."),
    ("'as described'", "Download and extract as described under First."),
    ("'see the note' / 'see step'", "Newer versions are untested (see the note under step 6)."),
    ("'instead'", "If you use another Python, run this instead: x"),
    ("'do not run'", "If you installed by hand, do not run the setup script."),
    ("'step N' pointer", "Repeat the command of step 6."),
    ("'Option N' pointer", "Follow Option 3 for the rest."),
])
def test_each_forbidden_form_is_really_caught(form, sample):
    assert FORBIDDEN_STEP_FORMS[form].search(sample), (form, sample)


def test_the_location_word_above_is_not_a_pointer():
    for step in (
        'On this project\'s GitHub page, click the green "Code" button above the file list.',
        "If it installs Python: close the window and start over from step 2.",
    ):
        assert not any(pattern.search(step) for pattern in FORBIDDEN_STEP_FORMS.values()), step


def test_a_carry_over_lead_in_is_really_caught():
    assert CARRY_OVER_FORMS.search("After moving settings.json into the new folder, do this:")
    assert not CARRY_OVER_FORMS.search("What the script does:")
    assert not CARRY_OVER_FORMS.search("The same script, pasted into a terminal:")



def test_no_step_points_elsewhere():
    lists, _ = readme_lists()
    offenders = []
    for heading, steps in lists.items():
        for number, step in enumerate(steps, start=1):
            for form, pattern in FORBIDDEN_STEP_FORMS.items():
                if pattern.search(step):
                    offenders.append(f"[{heading}] step {number}: {form}: {step!r}")
    assert not offenders, (
        "README steps must be self-contained (restate the action, never point at "
        "another section or step):\n  " + "\n  ".join(offenders)
    )


def test_no_list_opens_by_carrying_over_earlier_actions():
    lists, lead_ins = readme_lists()
    offenders = [
        f"[{heading}]: {lead_in!r}"
        for heading in lists
        if (lead_in := lead_ins[heading]) and CARRY_OVER_FORMS.search(lead_in)
    ]
    assert not offenders, (
        "a README list opens with a sentence that assumes earlier actions; list "
        "them as steps:\n  " + "\n  ".join(offenders)
    )


def test_the_manual_commands_are_identical_wherever_repeated():
    def commands(steps):
        return sorted(
            cmd for step in steps for cmd in backticked(step)
            if cmd.startswith(("python ", ".\\venv"))
        )
    setup = commands(steps_under("Manual Setup"))
    update = commands(steps_under("Update by hand"))
    assert setup, "Manual Setup no longer carries backticked venv/pip commands"
    assert setup == update


def test_the_promised_folder_name_is_what_github_produces():
    expected = f"{readme_slug()}-{DEFAULT_BRANCH}"
    for heading in ("First: download and extract", "Update with the setup script",
                    "Update by hand"):
        names = [n for step in steps_under(heading) for n in backticked(step)]
        assert expected in names, f"[{heading}] never names the folder {expected!r}: {names}"


def test_both_update_lists_name_the_same_moved_paths():
    script = steps_under("Update with the setup script")
    by_hand = steps_under("Update by hand")
    moved_script = [s for s in script if s.startswith("Move `")]
    moved_hand = [s for s in by_hand if s.startswith("Move `")]
    assert moved_script and moved_script == moved_hand
