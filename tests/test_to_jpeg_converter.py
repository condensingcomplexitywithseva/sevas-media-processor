# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import io
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, UnidentifiedImageError

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import to_jpeg_converter
from schemas import Status
from to_jpeg_converter import (
    ToJpegConverter,
    is_frame_distinct,
    open_supported_image,
)


def make_converter(
    jpeg_quality=90,
    max_dimension=4096,
    max_file_size_kb=0,
    lowest_quality=10,
    white_background=(255, 255, 255),
):
    return ToJpegConverter(
        jpeg_quality=jpeg_quality,
        max_dimension=max_dimension,
        max_file_size_kb=max_file_size_kb,
        lowest_quality=lowest_quality,
        white_background=white_background,
    )


def noise_image(width, height, seed=0):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    return Image.fromarray(pixels, "RGB")


def reload(path: Path) -> tuple[Image.Image, bytes]:
    with Image.open(path) as img:
        img.load()
        return img.copy(), Path(path).read_bytes()


def rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    return pixel



def test_transparent_rgba_flattens_onto_background_not_black(tmp_path):
    img = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    out = tmp_path / "flat.jpg"

    status, _ = make_converter().process_image(img, out)

    assert status == Status.OK.value
    saved, _ = reload(out)
    r, g, b = rgb(saved, (0, 0))
    assert min(r, g, b) > 240


def test_transparent_la_flattens_onto_custom_background(tmp_path):
    img = Image.new("LA", (30, 30), (128, 0))
    out = tmp_path / "flat_la.jpg"

    status, _ = make_converter(white_background=(255, 0, 0)).process_image(img, out)

    assert status == Status.OK.value
    saved, _ = reload(out)
    r, g, b = rgb(saved, (15, 15))
    assert r > 200 and g < 60 and b < 60



def sideways_jpeg(width=100, height=50, orientation=6):
    base = Image.new("RGB", (width, height), "blue")
    exif = base.getexif()
    exif[274] = orientation
    buf = io.BytesIO()
    base.save(buf, "JPEG", exif=exif.tobytes())
    buf.seek(0)
    return Image.open(buf)


def test_exif_orientation_applied_and_all_metadata_stripped(tmp_path):
    out = tmp_path / "upright.jpg"

    status, _ = make_converter().process_image(sideways_jpeg(100, 50), out)

    assert status == Status.OK.value
    saved, raw = reload(out)
    assert saved.size == (50, 100)
    assert len(saved.getexif()) == 0
    assert b"Exif\x00\x00" not in raw



def test_oversized_image_shrinks_to_max_dimension_keeping_ratio(tmp_path):
    out = tmp_path / "small.jpg"

    status, comment = make_converter(max_dimension=1000).process_image(
        Image.new("RGB", (4000, 2000), "green"), out
    )

    assert status == Status.OK.value
    saved, _ = reload(out)
    assert saved.size == (1000, 500)
    assert "Downscaled from 4000x2000 to 1000x500" in comment


def test_shrunk_size_is_rounded_not_truncated(tmp_path):
    out = tmp_path / "rounded.jpg"

    status, comment = make_converter(max_dimension=2560).process_image(
        Image.new("RGB", (2316, 3088), "green"), out
    )

    assert status == Status.OK.value
    saved, _ = reload(out)
    assert saved.size == (1920, 2560)
    assert "Downscaled from 2316x3088 to 1920x2560" in comment


def test_small_image_is_never_upscaled(tmp_path):
    out = tmp_path / "asis.jpg"

    status, comment = make_converter(max_dimension=1000).process_image(
        Image.new("RGB", (100, 50), "green"), out
    )

    assert status == Status.OK.value
    saved, _ = reload(out)
    assert saved.size == (100, 50)
    assert "Downscaled" not in comment


def test_downscale_note_reports_display_size_not_stored_size(tmp_path):
    out = tmp_path / "rotated_small.jpg"

    status, comment = make_converter(max_dimension=1000).process_image(
        sideways_jpeg(4000, 2000, orientation=6), out
    )

    assert status == Status.OK.value
    saved, _ = reload(out)
    assert saved.size == (500, 1000)
    assert "Downscaled from 2000x4000 to 500x1000" in comment



def test_size_budget_finds_lower_quality_and_says_so(tmp_path):
    out = tmp_path / "budget.jpg"

    status, comment = make_converter(max_file_size_kb=8).process_image(
        noise_image(128, 128), out
    )

    assert status == Status.OK.value
    assert "Compressed to quality" in comment
    assert out.stat().st_size <= 8 * 1024


def test_size_budget_impossible_saves_lowest_quality_with_warning(tmp_path):
    out = tmp_path / "forced.jpg"

    status, comment = make_converter(max_file_size_kb=2).process_image(
        noise_image(512, 512), out
    )

    assert status == Status.OK.value
    assert "Forced to lowest quality" in comment
    assert out.exists() and out.stat().st_size > 0


def test_generous_budget_leaves_no_warning(tmp_path):
    out = tmp_path / "roomy.jpg"

    status, comment = make_converter(max_file_size_kb=10_000).process_image(
        noise_image(64, 64), out
    )

    assert status == Status.OK.value
    assert comment == ""



@pytest.mark.parametrize("bad_input", [None, "not an image", 42, b"bytes"])
def test_non_image_input_degrades_to_failure_tuple(tmp_path, bad_input):
    status, comment = make_converter().process_image(bad_input, tmp_path / "x.jpg")

    assert status == Status.FAILURE.value
    assert "not a valid PIL Image" in comment


def test_zero_dimension_image_is_failure_not_crash(tmp_path):
    status, comment = make_converter().process_image(
        Image.new("RGB", (0, 0)), tmp_path / "zero.jpg"
    )

    assert status == Status.FAILURE.value
    assert "Invalid dimensions" in comment


def test_truncated_file_is_failure_not_crash(tmp_path):
    buf = io.BytesIO()
    noise_image(256, 256).save(buf, "JPEG")
    torn = io.BytesIO(buf.getvalue()[: buf.tell() // 2])
    img = Image.open(torn)

    status, _ = make_converter().process_image(img, tmp_path / "torn.jpg")

    assert status == Status.FAILURE.value



def test_cmyk_converts_with_visible_color_shift_warning(tmp_path):
    out = tmp_path / "cmyk.jpg"

    status, comment = make_converter().process_image(
        Image.new("CMYK", (40, 40)), out
    )

    assert status == Status.OK.value
    assert "colors may shift" in comment
    saved, _ = reload(out)
    assert saved.mode == "RGB"



def frame(value, shape=(8, 8)):
    return np.full(shape, value, dtype=np.uint8)


def test_identical_frames_are_duplicates():
    assert not is_frame_distinct(frame(100), frame(100), threshold=5.0)


def test_clearly_different_frames_are_distinct():
    assert is_frame_distinct(frame(0), frame(200), threshold=5.0)


def test_threshold_zero_disables_dedup_entirely():
    assert is_frame_distinct(frame(100), frame(100), threshold=0)
    assert is_frame_distinct(frame(100), frame(100), threshold=-1)


def test_first_frame_has_no_previous_and_is_distinct():
    assert is_frame_distinct(frame(100), None, threshold=5.0)


def test_shape_mismatch_fails_open_to_distinct():
    assert is_frame_distinct(frame(0, (4, 4)), frame(0, (8, 8)), threshold=5.0)


def test_non_array_input_fails_open_to_distinct():
    assert is_frame_distinct("junk", frame(0), threshold=5.0)
    assert is_frame_distinct(frame(0), "junk", threshold=5.0)


def test_uint8_underflow_regression_black_vs_white_is_distinct():
    assert is_frame_distinct(frame(0), frame(255), threshold=2000.0)



SENTINEL_MAKE = "SENTINEL-DEVICE-MAKE"
SENTINEL_DATE = "2026:01:01 12:00:00"
SENTINEL_XMP = b"<x:xmpmeta>SENTINEL-XMP-PAYLOAD</x:xmpmeta>"


def exif_with_gps():
    exif = Image.Exif()
    exif[271] = SENTINEL_MAKE
    exif[306] = SENTINEL_DATE
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (51.0, 30.0, 0.0)
    return exif


def _jpeg_with_metadata():
    base = Image.new("RGB", (32, 32), "orange")
    buf = io.BytesIO()
    base.save(buf, "JPEG", exif=exif_with_gps().tobytes())
    buf.seek(0)
    return Image.open(buf)


def _png_with_metadata():
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("Author", SENTINEL_MAKE)
    base = Image.new("RGB", (32, 32), "orange")
    buf = io.BytesIO()
    base.save(buf, "PNG", pnginfo=meta, exif=exif_with_gps())
    buf.seek(0)
    return Image.open(buf)


def _webp_with_metadata():
    base = Image.new("RGB", (32, 32), "orange")
    buf = io.BytesIO()
    base.save(buf, "WEBP", exif=exif_with_gps().tobytes(), xmp=SENTINEL_XMP)
    buf.seek(0)
    return Image.open(buf)


def _tiff_with_metadata():
    base = Image.new("RGB", (32, 32), "orange")
    buf = io.BytesIO()
    base.save(buf, "TIFF", tiffinfo={270: SENTINEL_MAKE})
    buf.seek(0)
    return Image.open(buf)


def _heic_with_metadata():
    import pillow_heif

    pillow_heif.register_heif_opener()
    base = Image.new("RGB", (32, 32), "orange")
    buf = io.BytesIO()
    base.save(buf, format="HEIF", exif=exif_with_gps().tobytes())
    buf.seek(0)
    return Image.open(buf)


@pytest.mark.parametrize(
    "builder",
    [
        _jpeg_with_metadata,
        _png_with_metadata,
        _webp_with_metadata,
        _tiff_with_metadata,
        _heic_with_metadata,
    ],
    ids=["jpeg", "png", "webp", "tiff", "heic"],
)
def test_no_metadata_survives_from_any_input_format(tmp_path, builder):
    img = builder()
    out = tmp_path / "clean.jpg"

    img.info["icc_profile"] = b"SENTINEL-ICC-PROFILE"

    status, _ = make_converter().process_image(img, out)

    assert status == Status.OK.value
    saved, raw = reload(out)
    assert len(saved.getexif()) == 0
    assert len(saved.getexif().get_ifd(0x8825)) == 0
    assert not saved.info.get("exif")
    assert not saved.info.get("xmp")
    assert not saved.info.get("icc_profile")
    for sentinel in (
        SENTINEL_MAKE.encode(),
        SENTINEL_DATE.encode(),
        b"SENTINEL-XMP-PAYLOAD",
        b"SENTINEL-ICC-PROFILE",
        b"Exif\x00\x00",
    ):
        assert sentinel not in raw



_DISPLAY_P3_ICC = __import__("base64").b64decode(
    "AAACGGFwcGwEAAAAbW50clJHQiBYWVogB+YAAQABAAAAAAAAYWNzcEFQUEwAAAAAQVBQTAAA"
    "AAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1hcHBsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAKZGVzYwAAAPwAAAAwY3BydAAAASwAAABQd3RwdAAA"
    "AXwAAAAUclhZWgAAAZAAAAAUZ1hZWgAAAaQAAAAUYlhZWgAAAbgAAAAUclRSQwAAAcwAAAAg"
    "Y2hhZAAAAewAAAAsYlRSQwAAAcwAAAAgZ1RSQwAAAcwAAAAgbWx1YwAAAAAAAAABAAAADGVu"
    "VVMAAAAUAAAAHABEAGkAcwBwAGwAYQB5ACAAUAAzbWx1YwAAAAAAAAABAAAADGVuVVMAAAA0"
    "AAAAHABDAG8AcAB5AHIAaQBnAGgAdAAgAEEAcABwAGwAZQAgAEkAbgBjAC4ALAAgADIAMAAy"
    "ADJYWVogAAAAAAAA9tUAAQAAAADTLFhZWiAAAAAAAACD3wAAPb////+7WFlaIAAAAAAAAEq/"
    "AACxNwAACrlYWVogAAAAAAAAKDgAABELAADIuXBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAA"
    "E9AAAApbc2YzMgAAAAAAAQxCAAAF3v//8yYAAAeTAAD9kP//+6L///2jAAAD3AAAwG4="
)

_P3_PURPLE = (128, 0, 128)
_SRGB_PURPLE = (141, 0, 133)


def _close(actual, expected, tolerance=6):
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected, strict=True))


def test_wide_gamut_p3_colors_are_baked_into_srgb_pixels(tmp_path):
    img = Image.new("RGB", (60, 60), _P3_PURPLE)
    img.info["icc_profile"] = _DISPLAY_P3_ICC
    out = tmp_path / "srgb.jpg"

    status, comment = make_converter().process_image(img, out)

    assert status == Status.OK.value
    assert "color profile" not in comment
    saved, _raw = reload(out)
    pixel = saved.getpixel((30, 30))
    assert _close(pixel, _SRGB_PURPLE), f"got {pixel}, want ~{_SRGB_PURPLE}"
    assert not saved.info.get("icc_profile")


def test_srgb_tagged_image_pixels_stay_put(tmp_path):
    from PIL import ImageCms

    srgb_bytes = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    img = Image.new("RGB", (60, 60), _P3_PURPLE)
    img.info["icc_profile"] = srgb_bytes
    out = tmp_path / "already_srgb.jpg"

    status, comment = make_converter().process_image(img, out)

    assert status == Status.OK.value
    assert "color profile" not in comment
    saved, _ = reload(out)
    assert _close(saved.getpixel((30, 30)), _P3_PURPLE)


def test_corrupt_icc_profile_falls_back_with_a_visible_warning(tmp_path):
    img = Image.new("RGB", (60, 60), _P3_PURPLE)
    img.info["icc_profile"] = b"this is not an ICC profile"
    out = tmp_path / "bad_profile.jpg"

    status, comment = make_converter().process_image(img, out)

    assert status == Status.OK.value
    assert "color profile could not be applied" in comment
    saved, _ = reload(out)
    assert _close(saved.getpixel((30, 30)), _P3_PURPLE)


def test_wide_gamut_conversion_survives_the_transparency_flatten(tmp_path):
    img = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    for x in range(20, 40):
        for y in range(20, 40):
            img.putpixel((x, y), (*_P3_PURPLE, 255))
    img.info["icc_profile"] = _DISPLAY_P3_ICC
    out = tmp_path / "flattened_srgb.jpg"

    status, _ = make_converter().process_image(img, out)

    assert status == Status.OK.value
    saved, _ = reload(out)
    assert _close(saved.getpixel((30, 30)), _SRGB_PURPLE, tolerance=8)
    r, g, b = rgb(saved, (5, 5))
    assert min(r, g, b) > 240



_SAVE_ONCE_SCRIPT = """\
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from PIL import Image
from to_jpeg_converter import ToJpegConverter

converter = ToJpegConverter(
    jpeg_quality=90, max_dimension=200, max_file_size_kb=0,
    lowest_quality=10, white_background=(255, 255, 255),
)
with Image.open(sys.argv[2]) as image:
    image.load()
    status, _comment = converter.process_image(image, Path(sys.argv[3]))
print(status)
"""


def test_saved_jpeg_bytes_are_identical_across_process_invocations(tmp_path):
    source = tmp_path / "source.jpg"
    img = noise_image(300, 200, seed=7)
    exif = Image.Exif()
    exif[0x0132] = "2026:08:02 12:00:00"
    exif[0x0112] = 6
    img.save(source, "JPEG", quality=95, exif=exif)

    script = tmp_path / "save_once.py"
    script.write_text(_SAVE_ONCE_SCRIPT, encoding="utf-8")

    outputs = []
    for run in ("first", "second"):
        out = tmp_path / f"{run}.jpg"
        completed = subprocess.run(
            [sys.executable, str(script), str(SRC), str(source), str(out)],
            capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == Status.OK.value, completed.stdout
        outputs.append(out.read_bytes())

    assert outputs[0], "the save produced an empty file"
    assert outputs[0] == outputs[1], (
        "the same input produced different JPEG bytes in two separate "
        "process invocations - something time-varying or seeded reaches "
        "the save path, and byte-hashing outputs (decision 24) is unsound "
        "until it is found and removed"
    )



def _ppm_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PPM")
    return buf.getvalue()


@pytest.mark.parametrize("pil_format, suffix", [
    ("JPEG", ".jpg"), ("PNG", ".png"), ("BMP", ".bmp"),
    ("TIFF", ".tif"), ("GIF", ".gif"), ("WEBP", ".webp"),
])
def test_every_converted_format_still_opens(tmp_path, pil_format, suffix):
    path = tmp_path / f"sample{suffix}"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(path, pil_format)

    with open_supported_image(path) as opened:
        assert opened.format == pil_format


def test_unsupported_format_is_refused_though_pillow_can_read_it(tmp_path):
    path = tmp_path / "sample.ppm"
    path.write_bytes(_ppm_bytes())

    with Image.open(path) as control:
        assert control.format == "PPM"

    with pytest.raises(UnidentifiedImageError):
        open_supported_image(path)


def test_extension_cannot_smuggle_in_an_unsupported_parser(tmp_path):
    path = tmp_path / "innocent.jpg"
    path.write_bytes(_ppm_bytes())

    with pytest.raises(UnidentifiedImageError):
        open_supported_image(path)


def test_mislabelled_file_still_opens_when_both_formats_are_converted(tmp_path):
    path = tmp_path / "actually_a_png.bmp"
    Image.new("RGB", (8, 8), (5, 5, 5)).save(path, "PNG")

    with open_supported_image(path) as opened:
        assert opened.format == "PNG"


def test_unregistered_format_name_does_not_surface_as_a_keyerror(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        to_jpeg_converter,
        "SUPPORTED_OPEN_FORMATS",
        ("JPEG", "PNG", "NOSUCHPLUGIN"),
    )
    path = tmp_path / "junk.jpg"
    path.write_bytes(b"not an image at all")

    with pytest.raises(UnidentifiedImageError):
        open_supported_image(path)


def test_nothing_in_src_opens_an_image_without_the_allowlist():
    def image_open_call_lines(tree):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"
            ):
                qualifier = ast.unparse(node.func.value)
                if qualifier == "Image" or qualifier.endswith(".Image"):
                    yield node.lineno

    gate_calls, offenders = [], []
    for module in sorted(SRC.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        gate = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "open_supported_image"
            ),
            None,
        )
        for lineno in image_open_call_lines(tree):
            where = f"{module.relative_to(SRC).as_posix()}:{lineno}"
            if gate is not None and gate.lineno <= lineno <= (gate.end_lineno or gate.lineno):
                gate_calls.append(where)
            else:
                offenders.append(where)

    assert len(gate_calls) == 1, (
        "open_supported_image no longer holds exactly one Image.open call - "
        "the sweep's notion of the gate is stale, re-derive it: "
        + ", ".join(gate_calls)
    )
    assert offenders == [], (
        "these call Image.open directly instead of open_supported_image(): "
        + ", ".join(offenders)
    )



def _psd_bytes():
    header = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6
    header += struct.pack(">HIIHH", 1, 4, 4, 8, 1)
    return header + struct.pack(">III", 0, 0, 0) + struct.pack(">H", 0) + b"\x7f" * 16


def _eps_bytes():
    return (
        b"%!PS-Adobe-3.0 EPSF-3.0\n"
        b"%%BoundingBox: 0 0 8 8\n"
        b"%%Pages: 1\n"
        b"%%EndComments\n"
        b"%%Page: 1 1\n"
        b"showpage\n"
        b"%%EOF\n"
    )


def _fits_bytes():
    cards = [
        b"SIMPLE  =                    T",
        b"BITPIX  =                    8",
        b"NAXIS   =                    2",
        b"NAXIS1  =                    4",
        b"NAXIS2  =                    4",
        b"END",
    ]
    header = b"".join(card.ljust(80) for card in cards).ljust(2880)
    return header + (b"\x40" * 16).ljust(2880)


def _mcidas_bytes():
    words = [0] * 64
    words[1] = 4
    words[8] = 4
    words[9] = 4
    words[10] = 1
    words[13] = 1
    words[33] = 256
    return struct.pack(">64i", *words) + b"\x30" * 16


@pytest.mark.parametrize("pillow_format, builder", [
    ("PSD", _psd_bytes),
    ("EPS", _eps_bytes),
    ("FITS", _fits_bytes),
    ("MCIDAS", _mcidas_bytes),
])
def test_the_cve_reachable_parsers_are_out_of_reach(
    tmp_path, pillow_format, builder
):
    disguised = tmp_path / f"looks_like_a_photo_{pillow_format.lower()}.jpg"
    disguised.write_bytes(builder())

    with Image.open(disguised) as control:
        assert control.format == pillow_format

    with pytest.raises(UnidentifiedImageError):
        open_supported_image(disguised)
