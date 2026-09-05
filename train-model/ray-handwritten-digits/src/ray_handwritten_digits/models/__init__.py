"""手写数字模型工厂。"""

from ray_handwritten_digits.models.cnn import HandwrittenDigitsCNN, build_model

__all__ = ["HandwrittenDigitsCNN", "build_model"]
