"""Unit tests for engine/pipelines/image_pipeline — BUILD_PLAN.md §11 Phase 11."""

import io

import numpy as np
import tifffile

from engine.pipelines import image_pipeline as ip


def _tiff_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr)
    return buf.getvalue()


def test_default_representation_is_tensor():
    arr = np.random.default_rng(0).integers(1000, 40000, size=(2, 8, 8), dtype=np.uint16)
    results = ip.run(_tiff_bytes(arr), "x.tiff")
    assert {r["representation_type"] for r in results} == {"image_tensor"}


def test_normalization_uses_fixed_dtype_range_not_per_image_minmax():
    """The deliberate design choice from Phase 4: dividing by the dtype's max
    keeps a genuinely saturated channel visibly saturated, unlike a per-image
    min/max rescale which would stretch any non-constant channel to [0,1]
    regardless of its real dynamic range."""
    arr = np.full((1, 4, 4), 65535, dtype=np.uint16)  # fully saturated uint16 channel
    results = ip.run(_tiff_bytes(arr), "x.tiff")
    data = np.array(results[0]["tensor"]["data"])
    assert np.allclose(data, 1.0)


def test_2d_grayscale_gets_a_channel_dimension():
    arr = np.random.default_rng(1).integers(0, 255, size=(8, 8), dtype=np.uint8)
    results = ip.run(_tiff_bytes(arr), "x.tiff")
    assert results[0]["tensor"]["shape"][0] == 1  # [1, H, W]


def test_multichannel_shape_preserved():
    arr = np.random.default_rng(2).integers(0, 255, size=(3, 10, 12), dtype=np.uint8)
    results = ip.run(_tiff_bytes(arr), "x.tiff")
    assert results[0]["tensor"]["shape"] == [3, 10, 12]


def test_canonical_form_is_deterministic_content_hash():
    arr = np.random.default_rng(3).integers(0, 255, size=(2, 4, 4), dtype=np.uint8)
    content = _tiff_bytes(arr)
    r1 = ip.run(content, "x.tiff")[0]["canonical_form"]
    r2 = ip.run(content, "x.tiff")[0]["canonical_form"]
    assert r1 == r2  # same pixels -> same hash, every time
