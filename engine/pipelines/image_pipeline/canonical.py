"""The canonical shape every image_pipeline adapter converts into —
BUILD_PLAN.md §4a. Always [C, H, W], raw dtype preserved until featurize.py
normalizes it — normalization needs the source bit depth to detect real
sensor saturation (see featurize.normalize).
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class ImageRecord:
    name: str
    array: np.ndarray  # [C, H, W], raw dtype (uint8/uint16/float)
