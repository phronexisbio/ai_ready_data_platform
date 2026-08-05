"""Input validation for TIFF/OME-TIFF image files — BUILD_PLAN.md §6, input gate."""

import io

import tifffile


def validate(content: bytes) -> tuple[bool, str | None]:
    if not content:
        return False, "empty file"

    try:
        arr = tifffile.imread(io.BytesIO(content))
    except Exception as e:
        return False, f"could not parse TIFF file: {e}"

    if arr.size == 0:
        return False, "image has zero pixels"
    if arr.ndim not in (2, 3):
        return False, f"unsupported image ndim {arr.ndim}"

    return True, None
