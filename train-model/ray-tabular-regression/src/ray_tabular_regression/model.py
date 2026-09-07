from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class RidgeModel:
    weights: list[float]
    intercept: float
    means: list[float]
    scales: list[float]


def predict(model, features):
    return [model.intercept + sum(w * ((x - mean) / scale)
            for w, x, mean, scale in zip(model.weights, row, model.means, model.scales))
            for row in features]


def dumps(model):
    return json.dumps(asdict(model), sort_keys=True, separators=(",", ":")).encode()


def loads(raw):
    return RidgeModel(**json.loads(raw))


def fit_ridge(features, targets, alpha, *, admission=None, binding=None):
    """Official Driver-only fitting adapter. Never call from local checks/tests."""
    from .admission import require
    require(admission, binding)
    import numpy as np
    x = np.asarray(features, dtype=float); y = np.asarray(targets, dtype=float)
    means = x.mean(axis=0); scales = x.std(axis=0); scales[scales == 0] = 1.0
    z = (x - means) / scales
    augmented = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(augmented.shape[1]) * float(alpha); penalty[0, 0] = 0
    coefficients = np.linalg.solve(augmented.T @ augmented + penalty, augmented.T @ y)
    return RidgeModel(coefficients[1:].tolist(), float(coefficients[0]), means.tolist(), scales.tolist())
