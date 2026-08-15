# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import csv
import io
import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fs_utils import (
    format_hms,
    format_hms_for_filename,
    get_safe_path,
    read_prompt,
    sanitize_filename_prefix,
    humanize_paths,
)

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows long-path prefixing")



@windows_only
def test_plain_drive_path_gets_the_long_path_prefix():
    assert get_safe_path(Path(r"C:\data\x\file.txt")) == "\\\\?\\C:\\data\\x\\file.txt"


@windows_only
def test_unc_path_gets_the_unc_prefix_variant():
    assert (
        get_safe_path(Path(r"\\server\share\file.txt"))
        == "\\\\?\\UNC\\server\\share\\file.txt"
    )


@windows_only
def test_already_prefixed_unc_path_is_unchanged():
    already = "\\\\?\\UNC\\server\\share\\file.txt"
    assert get_safe_path(Path(already)) == already


@windows_only
def test_already_prefixed_drive_path_is_unchanged():
    already = "\\\\?\\C:\\data\\x\\file.txt"
    assert get_safe_path(Path(already)) == already


@windows_only
def test_deep_paths_beyond_260_chars_are_actually_usable(tmp_path):
    deep = tmp_path
    for _ in range(30):
        deep = deep / ("d" * 20)
    deep_file = deep / "leaf.txt"
    assert len(str(deep_file)) > 260

    os.makedirs(get_safe_path(deep), exist_ok=True)
    with open(get_safe_path(deep_file), "w", encoding="utf-8") as handle:
        handle.write("made it")
    with open(get_safe_path(deep_file), encoding="utf-8") as handle:
        assert handle.read() == "made it"



def test_the_prefix_is_removed_from_mid_sentence_not_just_from_a_bare_path():
    message = "cannot identify image file '\\\\?\\C:\\pics\\holiday.jpeg'"
    assert (
        humanize_paths(message)
        == "cannot identify image file 'C:\\pics\\holiday.jpeg'"
    )


@windows_only
def test_it_is_the_exact_inverse_of_get_safe_path():
    for original in (r"C:\data\x\file.txt", r"\\server\share\file.txt"):
        assert humanize_paths(get_safe_path(Path(original))) == original


def test_the_unc_form_becomes_a_network_path_not_the_literal_word_unc():
    assert (
        humanize_paths("\\\\?\\UNC\\nas\\media\\a.jpg")
        == "\\\\nas\\media\\a.jpg"
    )


@pytest.mark.parametrize(
    "untouched",
    [
        "",
        "No path in this message at all.",
        "C:\\already\\plain.txt",
        "Question? A backslash \\ and a share \\\\nas\\media.",
    ],
)
def test_text_without_the_prefix_passes_through_verbatim(untouched):
    assert humanize_paths(untouched) == untouched


def test_every_prefix_in_a_multi_error_comment_is_removed():
    joined = (
        "Details: cannot identify image file '\\\\?\\C:\\a.jpeg'; "
        "OS/Disk save error: [Errno 13] Permission denied: '\\\\?\\C:\\out\\1.jpg'"
    )
    cleaned = humanize_paths(joined)
    assert "\\\\?\\" not in cleaned
    assert "'C:\\a.jpeg'" in cleaned and "'C:\\out\\1.jpg'" in cleaned


def test_it_is_idempotent():
    once = humanize_paths("opening '\\\\?\\C:\\a.jpeg' and '\\\\?\\UNC\\nas\\b.jpg'")
    assert humanize_paths(once) == once


def test_the_real_pillow_message_comes_out_as_the_path_the_user_knows():
    doubled = "cannot identify image file '\\\\\\\\?\\\\C:\\\\pics\\\\a.jpeg'"
    assert (
        humanize_paths(doubled)
        == "cannot identify image file 'C:\\pics\\a.jpeg'"
    )


def test_the_repr_escaped_unc_spelling_becomes_a_usable_network_path():
    doubled = "'\\\\\\\\?\\\\UNC\\\\nas\\\\media\\\\a.jpg'"
    assert humanize_paths(doubled) == "'\\\\nas\\media\\a.jpg'"


def test_a_double_quoted_run_is_undoubled_too():
    doubled = "cannot identify image file \"\\\\\\\\?\\\\C:\\\\pics\\\\mum's a.jpeg\""
    assert (
        humanize_paths(doubled)
        == "cannot identify image file \"C:\\pics\\mum's a.jpeg\""
    )


def test_an_unquoted_network_path_is_never_collapsed():
    note = " [Orphaned path fallback. Original location: \\\\nas\\share\\clip.mp4]"
    assert humanize_paths(note) == note


@pytest.mark.parametrize(
    "text, mangled_by_the_naive_version, required",
    [
        (
            " [Orphaned path fallback. Original location: \\\\nas\\share\\clip.mp4]",
            " [Orphaned path fallback. Original location: \\nas\\share\\clip.mp4]",
            " [Orphaned path fallback. Original location: \\\\nas\\share\\clip.mp4]",
        ),
        (
            "opening '\\\\?\\C:\\pics\\a.jpeg'",
            "opening '\\?\\C:\\pics\\a.jpeg'",
            "opening 'C:\\pics\\a.jpeg'",
        ),
    ],
)
def test_the_naive_blanket_unescape_is_a_trap_and_stays_out(
    text, mangled_by_the_naive_version, required
):
    naive_version = text.replace("\\\\", "\\")

    assert naive_version == mangled_by_the_naive_version, (
        "the hazard this gate exists for is no longer reproducible - "
        "re-derive the rule before trusting the gate"
    )
    assert humanize_paths(text) == required


@windows_only
@pytest.mark.parametrize(
    "typed",
    [
        "\\\\some-server\\media\\holiday.jpeg",
        "//some-server/media/holiday.jpeg",
        "\\\\some-server\\media\\mum's photo.jpeg",
        "\\\\127.0.0.1\\C$\\pics\\a.jpeg",
    ],
)
def test_a_remote_disk_survives_the_whole_round_trip(typed):
    safe = get_safe_path(Path(typed))
    assert safe.startswith("\\\\?\\UNC\\")

    readable = humanize_paths(f"cannot identify image file {safe!r}")

    assert str(Path(typed)) in readable
    assert "\\\\?\\" not in readable
    assert "UNC" not in readable


def test_a_control_character_in_the_name_degrades_safely_instead_of_undoubling():
    message = "cannot identify image file '\\\\\\\\?\\\\C:\\\\pics\\\\a\\x01b.jpeg'"
    cleaned = humanize_paths(message)

    assert "\\\\?\\" not in cleaned
    assert "\\x01" in cleaned
    assert "\x01" not in cleaned
    assert cleaned == "cannot identify image file 'C:\\\\pics\\\\a\\x01b.jpeg'"



def test_text_mode_returns_the_value_verbatim(tmp_path):
    looks_like_a_path = str(tmp_path / "not_read.txt")
    (tmp_path / "not_read.txt").write_text("file content", encoding="utf-8")
    assert read_prompt(looks_like_a_path, "TEXT") == looks_like_a_path


def test_file_mode_reads_the_referenced_file(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Describe the image. Ответь по-русски.", encoding="utf-8")
    assert read_prompt(str(prompt_file), "FILE") == "Describe the image. Ответь по-русски."


@pytest.mark.parametrize("blank", ["", "   "])
def test_file_mode_with_a_blank_value_returns_the_blank(blank):
    assert read_prompt(blank, "FILE") == blank


def test_file_mode_with_an_unreadable_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        read_prompt(str(tmp_path / "missing.txt"), "FILE")


def test_file_mode_refuses_bytes_that_are_not_text(tmp_path):
    bom = tmp_path / "bom.txt"
    bom.write_bytes("привет".encode("utf-16"))
    bomless = tmp_path / "bomless.txt"
    bomless.write_bytes("plain ascii words".encode("utf-16-le"))

    for path in (bom, bomless):
        with pytest.raises(ValueError):
            read_prompt(str(path), "FILE")


def test_text_looks_binary_draws_the_line_at_control_characters():
    from fs_utils import text_looks_binary

    assert not text_looks_binary("plain text with\ttabs\r\nand newlines")
    assert not text_looks_binary("")
    assert text_looks_binary("nul\x00inside")
    assert text_looks_binary("\x04garbage")
    assert text_looks_binary("del\x7finside")



def test_format_hms_renders_the_plan_examples():
    assert format_hms(93.37) == "00:01:33.37"
    assert format_hms(900.48) == "00:15:00.48"
    assert format_hms(0) == "00:00:00.00"


def test_format_hms_for_filename_renders_the_plan_examples():
    assert format_hms_for_filename(93.37) == "00_01_33_37"
    assert format_hms_for_filename(900.48) == "00_15_00_48"
    assert format_hms_for_filename(0) == "00_00_00_00"


def test_fields_are_fixed_width_two_digits():
    assert format_hms(3661.05) == "01:01:01.05"
    assert format_hms_for_filename(3661.05) == "01_01_01_05"


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0.996, "00:00:01.00"),
        (59.996, "00:01:00.00"),
        (3599.996, "01:00:00.00"),
    ],
)
def test_rounding_to_hundredths_happens_before_the_split_so_carries_propagate(
    seconds, expected
):
    assert format_hms(seconds) == expected


def test_rounding_rule_is_round_to_nearest_hundredth():
    assert format_hms(0.014) == "00:00:00.01"
    assert format_hms(0.016) == "00:00:00.02"


def test_hours_at_100_or_more_widen_the_field_without_crash_or_modulo():
    assert format_hms(360000) == "100:00:00.00"
    assert format_hms_for_filename(360000) == "100_00_00_00"


@pytest.mark.parametrize("bad", [-0.01, -1, float("nan"), float("inf"), float("-inf")])
def test_negative_or_non_finite_input_is_a_programmer_error(bad):
    with pytest.raises(ValueError):
        format_hms(bad)
    with pytest.raises(ValueError):
        format_hms_for_filename(bad)


def test_the_two_helpers_can_never_drift():
    for seconds in (0, 0.996, 59.996, 93.37, 900.48, 3599.996, 3661.05, 360000, 359999.994):
        assert format_hms_for_filename(seconds) == (
            format_hms(seconds).replace(":", "_").replace(".", "_")
        )



def test_plan_examples_render_as_specified():
    assert sanitize_filename_prefix("IMG_0141", 20) == "IMG_0141"
    assert (
        sanitize_filename_prefix("fed monetary policy report charts", 20)
        == "fed monetary policy"
    )
    assert (
        sanitize_filename_prefix("How To Mod Stronghold 4 Graphics & Style", 20)
        == "How To Mod Stronghol"
    )
    assert sanitize_filename_prefix("день рождения 🎂🎈", 20) == "день рождения 🎂🎈"


def test_spaces_and_international_characters_survive_verbatim():
    for stem in (
        "фото (копия) №2",
        "صورة العائلة القديمة",
        "全家福 2026年春节",
        "试试 café ñandú → фото Ω",
    ):
        assert sanitize_filename_prefix(stem, 64) == stem


def test_windows_illegal_characters_become_single_spaces():
    assert (
        sanitize_filename_prefix('a<b>c:d"e/f\\g|h?i*j', 64)
        == "a b c d e f g h i j"
    )
    assert sanitize_filename_prefix("scan<<<>>>2026", 64) == "scan 2026"


def test_ascii_control_characters_become_single_spaces():
    assert (
        sanitize_filename_prefix("tab\there\nnewline\x00null\x7fdel", 64)
        == "tab here newline null del"
    )


def test_cleaning_happens_before_truncation_not_after():
    assert sanitize_filename_prefix("a<<<<bcdef", 5) == "a bcd"


def test_truncation_counts_code_points_so_an_emoji_cannot_be_split():
    result = sanitize_filename_prefix("🎂" * 25, 20)
    assert result == "🎂" * 20
    result.encode("utf-8")


def test_splitting_a_combining_sequence_is_ugly_but_valid_unicode():
    result = sanitize_filename_prefix("👍🏽", 1)
    assert result == "👍"
    result.encode("utf-8")


def test_trailing_dots_and_spaces_are_trimmed():
    assert sanitize_filename_prefix("backup copy 2.", 64) == "backup copy 2"
    assert sanitize_filename_prefix("draft...", 64) == "draft"
    assert sanitize_filename_prefix("abcde fghij", 6) == "abcde"


@pytest.mark.parametrize("hopeless", ["***", "???", "...", "\x00\x01\x02", "   ", '<>:"/\\|?*'])
def test_all_illegal_names_come_back_empty_for_the_legacy_fallback(hopeless):
    assert sanitize_filename_prefix(hopeless, 64) == ""


def test_length_zero_means_no_prefix():
    assert sanitize_filename_prefix("IMG_0141", 0) == ""


def test_negative_length_is_a_programmer_error():
    with pytest.raises(ValueError):
        sanitize_filename_prefix("IMG_0141", -1)


def test_lone_surrogates_die_here_not_in_the_export():
    raw_stem = "IMG\udce9 0141"
    with pytest.raises(UnicodeEncodeError):
        raw_stem.encode("utf-8")
    cleaned = sanitize_filename_prefix(raw_stem, 64)
    assert cleaned == "IMG 0141"
    assert json.loads(json.dumps(cleaned, ensure_ascii=False)) == cleaned
    buffer = io.StringIO()
    csv.writer(buffer).writerow([cleaned])
    buffer.getvalue().encode("utf-8")
