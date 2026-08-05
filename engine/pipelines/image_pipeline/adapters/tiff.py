"""TIFF/OME-TIFF input -> canonical ImageRecord, via tifffile."""

import io

import numpy as np
import tifffile

from engine.pipelines.image_pipeline.canonical import ImageRecord


def _to_chw(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return arr[np.newaxis, :, :]
    if arr.ndim == 3:
        # Heuristic: multi-channel microscopy stacks (Cell Painting: typically
        # 5 channels) rarely exceed a couple dozen, while H/W are usually in
        # the hundreds-to-thousands — treat the smallest axis as channel.
        channel_axis = int(np.argmin(arr.shape))
        return np.moveaxis(arr, channel_axis, 0)
    raise ValueError(f"unsupported image ndim {arr.ndim} (shape {arr.shape})")


def parse(content: bytes) -> list[ImageRecord]:
    arr = tifffile.imread(io.BytesIO(content))
    return [ImageRecord(name="image", array=_to_chw(arr))]
