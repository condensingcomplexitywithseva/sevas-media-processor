# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
from pathlib import Path
from typing import Any
import numpy as np
import io
import os
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from schemas import Status
from fs_utils import get_safe_path
import contextlib

logger = logging.getLogger("ImageConverter")

_PLUGINS_LOADED = False

def _lazy_load_image_plugins():
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        import pillow_avif  # noqa: F401  (the import itself registers AVIF)
    except ImportError as e:
        logger.warning(
            f"Modern image plugins missing. HEIC/AVIF files will fail: {e}"
        )
    except Exception as e:
        logger.warning(
            f"Unexpected register_heif_opener import failure: {e!s}"
        )
    except BaseException as e:
        logger.warning(
            f"Caught base exception during plugin import (e.g. KeyboardInterrupt from VS Code): {e!s}"
        )
    finally:
        _PLUGINS_LOADED = True


SUPPORTED_OPEN_FORMATS = (
    "JPEG", "PNG", "BMP", "DIB", "TIFF", "GIF", "WEBP", "HEIF", "AVIF",
)


def open_supported_image(path: str | Path) -> Image.Image:
    _lazy_load_image_plugins()
    Image.init()
    return Image.open(
        path, formats=[f for f in SUPPORTED_OPEN_FORMATS if f in Image.OPEN]
    )


def is_frame_distinct(
    current: Any, previous: Any, threshold: float
) -> bool:
    if previous is None:
        return True

    if threshold <= 0:
        return True

    if not isinstance(current, np.ndarray) or not isinstance(previous, np.ndarray):
        return True

    if current.shape != previous.shape:
        return True

    try:
        diff = current.astype("float") - previous.astype("float")
        squared = diff**2
        mse = np.sum(squared) / float(current.size)

        return mse > threshold
    except Exception as e:
        logger.debug(
            f"Math comparison failed, treating frame as distinct. Error: {e}"
        )
        return True


class ToJpegConverter:

    def __init__(
        self,
        jpeg_quality: int,
        max_dimension: int,
        max_file_size_kb: int,
        lowest_quality: int,
        white_background: tuple[int, int, int],
    ):
        _lazy_load_image_plugins()
        self.target_quality = jpeg_quality
        self.max_dimension_limit = max_dimension
        self.max_size_kb_limit = max_file_size_kb
        self.lowest_quality_limit = lowest_quality
        self.white_background_color = white_background

    def process_image(
        self, image: Any, output_path: Path
    ) -> tuple[str, str]:

        if image is None or not isinstance(image, Image.Image):
            return Status.FAILURE.value, "Input was not a valid PIL Image."

        temp_images = []
        current = image
        warnings = []

        try:
            exif_data = current.getexif()

            orientation = exif_data.get(274) if exif_data is not None else None
            if orientation is not None and orientation not in range(1, 9):
                orientation_warning = (
                    f"EXIF orientation {orientation} is outside the standard "
                    "1-8 range (common on some phones); tag ignored, image "
                    "kept as stored."
                )
                logger.warning(
                    f"[{output_path.name}] - {orientation_warning}"
                )
                warnings.append(orientation_warning)

            if current.mode == "CMYK":
                cmyk_warning = (
                    "CMYK color space detected. Converted to RGB; colors may shift."
                )
                logger.warning(
                    f"[{output_path.name}] - {cmyk_warning}"
                )
                warnings.append(cmyk_warning)

            converted = self._bake_wide_gamut_to_srgb(current, output_path, warnings)
            if converted is not current:
                temp_images.append(converted)
                current = converted

            flattened = self._defensively_flatten_to_rgb(current)

            if flattened is not current:
                temp_images.append(flattened)
                current = flattened

            try:
                if exif_data is not None:
                    current.info["exif"] = exif_data.tobytes()
            except Exception:
                pass

            try:
                transposed = ImageOps.exif_transpose(current)
                if transposed is not current:
                    temp_images.append(transposed)
                    current = transposed
            except Exception as e:
                msg = (
                    f"Non-fatal error: EXIF Rotation bypassed: {e!s}"
                )
                logger.warning(
                    f"[{output_path.name}] - {msg}", exc_info=True
                )
                warnings.append(msg)

            if self.max_dimension_limit and self.max_dimension_limit > 0:
                width, height = current.size

                if width <= 0 or height <= 0:
                    return (
                        Status.FAILURE.value,
                        f"Corruption: Invalid dimensions ({width}x{height}).",
                    )

                if (
                    width > self.max_dimension_limit
                    or height > self.max_dimension_limit
                ):
                    ratio = min(
                        self.max_dimension_limit / float(width),
                        self.max_dimension_limit / float(height),
                    )

                    new_size = (
                        max(1, round(width * ratio)),
                        max(1, round(height * ratio)),
                    )

                    resized = current.resize(
                        new_size, Image.Resampling.LANCZOS
                    )

                    if resized is not current:
                        temp_images.append(resized)
                        current = resized

                    warnings.append(
                        f"Downscaled from {width}x{height} to {new_size[0]}x{new_size[1]}"
                    )

            current.info.clear()
            current.getexif().clear()

            safe_out_path = get_safe_path(output_path)
            os.makedirs(os.path.dirname(safe_out_path), exist_ok=True)

            best_bytes = None

            with io.BytesIO() as buffer:
                if (
                    getattr(self, "max_size_kb_limit", 0)
                    and self.max_size_kb_limit > 0
                    and getattr(self, "lowest_quality_limit", 0)
                    and self.lowest_quality_limit > 0
                ):
                    low = self.lowest_quality_limit
                    high = self.target_quality
                    best_quality = None

                    while low <= high:
                        mid = (low + high) // 2
                        buffer.seek(0)
                        buffer.truncate(0)

                        current.save(
                            buffer, "JPEG", quality=mid, subsampling=0
                        )
                        size_kb = buffer.tell() / 1024

                        if size_kb <= self.max_size_kb_limit:
                            best_quality = mid
                            best_bytes = buffer.getvalue()
                            low = mid + 1
                        else:
                            high = mid - 1

                    if best_quality is None:
                        buffer.seek(0)
                        buffer.truncate(0)
                        current.save(
                            buffer, "JPEG", quality=self.lowest_quality_limit, subsampling=0
                        )
                        best_bytes = buffer.getvalue()
                        warnings.append(
                            f"Forced to lowest quality {self.lowest_quality_limit} ({buffer.tell() / 1024:.1f} KB)"
                        )
                    elif (
                        best_quality < self.target_quality
                        and best_bytes is not None
                    ):
                        warnings.append(
                            f"Compressed to quality {best_quality} ({len(best_bytes) / 1024:.1f} KB)"
                        )

            if best_bytes:
                with open(safe_out_path, "wb") as f:
                    f.write(best_bytes)
            else:
                current.save(
                    safe_out_path, "JPEG", quality=self.target_quality, subsampling=0
                )

            comment = "; ".join(warnings)
            return Status.OK.value, comment

        except UnidentifiedImageError:
            return Status.FAILURE.value, "File format unrecognizable by Pillow decoding engine."
        except OSError as e:
            return Status.FAILURE.value, f"OS/Disk save error: {e!s}"
        except Exception as e:
            return (
                Status.FAILURE.value,
                f"Unexpected fatal image error: {e!s}",
            )
        finally:
            for temp_img in temp_images:
                with contextlib.suppress(Exception):
                    temp_img.close()

    def _bake_wide_gamut_to_srgb(
        self, image: Image.Image, output_path: Path, warnings: list
    ) -> Image.Image:
        icc_bytes = image.info.get("icc_profile")
        if not icc_bytes or image.mode not in ("RGB", "RGBA"):
            return image
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_bytes))
            description = ImageCms.getProfileDescription(source_profile)
            if "srgb" in description.lower():
                return image
            converted = ImageCms.profileToProfile(
                image,
                source_profile,
                ImageCms.createProfile("sRGB"),
                outputMode=image.mode,
            )
            logger.debug(
                f"[{output_path.name}] - Converted '{description.strip()}' to sRGB."
            )
            return image if converted is None else converted
        except Exception as e:
            msg = "Embedded color profile could not be applied; colors may shift."
            logger.warning(f"[{output_path.name}] - {msg} ({e!s})")
            warnings.append(msg)
            return image

    def _defensively_flatten_to_rgb(self, image: Image.Image) -> Image.Image:
        if image.mode == "RGB":
            return image

        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")

            try:
                canvas = Image.new(
                    "RGB", rgba.size, self.white_background_color
                )
                canvas.paste(rgba, (0, 0), rgba)
                return canvas
            except (MemoryError, Exception) as e:
                raise ValueError(
                    f"RAM exhaustion or render crash during RGBA flattening: {e}"
                ) from e
            finally:
                rgba.close()

        try:
            return image.convert("RGB")
        except Exception as e:
            msg = f"Irreparable color space conversion failure for mode '{image.mode}': {e}"
            logger.error(msg)
            raise ValueError(msg) from e
