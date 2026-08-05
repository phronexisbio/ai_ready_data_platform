"""Output validation for image tensors — BUILD_PLAN.md §6: no all-zero or
all-saturated channels post-normalization, expected [C, H, W] shape, not a
corrupted/truncated read.
"""

_SATURATION_TOLERANCE = 1e-6


def validate_image_tensor(tensor: dict) -> tuple[bool, str | None]:
    shape = tensor.get("shape", [])
    if len(shape) != 3:
        return False, f"expected [C,H,W] shape, got {shape}"

    c, h, w = shape
    if c == 0 or h == 0 or w == 0:
        return False, f"degenerate shape {shape}"

    data = tensor.get("data", [])
    if len(data) != c:
        return False, f"data has {len(data)} channels, shape says {c} (corrupted/truncated read)"

    for ci, channel in enumerate(data):
        flat = [v for row in channel for v in row]
        if len(flat) != h * w:
            return False, f"channel {ci} has {len(flat)} pixels, expected {h * w} (corrupted/truncated read)"
        if all(v <= _SATURATION_TOLERANCE for v in flat):
            return False, f"channel {ci} is all-zero after normalization"
        if all(v >= 1.0 - _SATURATION_TOLERANCE for v in flat):
            return False, f"channel {ci} is all-saturated after normalization"

    return True, None
