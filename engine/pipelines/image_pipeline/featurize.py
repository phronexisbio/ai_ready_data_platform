"""canonical ImageRecord -> representations — BUILD_PLAN.md §4b/§11.

Normalizes against the source dtype's fixed range (e.g. divide by 65535 for
uint16), not a per-image min/max rescale — a genuinely saturated sensor
channel (every pixel pinned at the dtype's max value) stays visibly saturated
(all ~1.0) after normalization this way, which is what makes the §6
output-validation gate able to catch it. A per-image min/max rescale would
silently launder that signal away (any non-constant channel gets stretched to
touch 0 and 1 no matter how compressed its real dynamic range was).

Segmentation is a stub (BUILD_PLAN §10 Phase 4: "segmentation stub") — real
cell/nucleus masking is a later phase.
"""

import hashlib

import numpy as np


def normalize(record) -> np.ndarray:
    arr = record.array
    if np.issubdtype(arr.dtype, np.integer):
        max_val = float(np.iinfo(arr.dtype).max)
    else:
        max_val = 1.0
    return np.clip(arr.astype(np.float32) / max_val, 0.0, 1.0)


def segment_stub(arr: np.ndarray) -> np.ndarray:
    """Identity passthrough — real segmentation (cell/nucleus masks) is a later phase."""
    return arr


def content_hash(record) -> str:
    """A deterministic identity string for the raw pixel content — the
    image_pipeline analogue of a molecule's canonical SMILES."""
    return hashlib.sha256(record.array.tobytes()).hexdigest()


def to_image_tensor(record) -> dict:
    normalized = normalize(record)
    segmented = segment_stub(normalized)
    return {
        "representation_type": "image_tensor",
        "shape": list(segmented.shape),
        "data": segmented.tolist(),
    }
