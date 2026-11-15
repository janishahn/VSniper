"""The image encoder behind the local taste head: Marqo-FashionSigLIP's image tower, exported to
ONNX with 8-bit weights by `research/export_onnx.py`."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

# Stored with every vector; vectors of another encoder are embedded again.
ENCODER_NAME = "fashion-siglip-int8"
ENCODER_FILE = "fashion-siglip.int8.onnx"

_INPUT_SIZE = 224
# Half of pop-os's cores: 94 ms per cover, and scans keep the other half.
_THREADS = 4


def prepare_image(content: bytes) -> np.ndarray:
    """A photo as the encoder expects it: RGB, squashed to a square, scaled to [-1, 1], channels first."""
    with Image.open(BytesIO(content)) as image:
        resized = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE), Image.Resampling.BICUBIC)
    return ((np.asarray(resized, dtype=np.float32) / 255 - 0.5) / 0.5).transpose(2, 0, 1)


class ImageEncoder:
    def __init__(self, path: Path) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = _THREADS
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])

    def embed(self, image: np.ndarray) -> np.ndarray:
        """The vector of one prepared image. Images go through one at a time: with 8-bit weights
        the value ranges are taken over the whole batch, so a vector would depend on its batch."""
        return np.asarray(self._session.run(None, {"image": image[None]})[0])[0]
