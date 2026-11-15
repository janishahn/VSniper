"""Per-bucket taste head: a logistic model on frozen encoder vectors plus the big model's score predicted
from the vector (the "luna direction"). Plain numpy port of the recipe in research/head_recipe.py."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np

MIN_LIKES = 3
MAX_NEGATIVES = 2000
DISLIKE_WEIGHT = 5.0
LOGISTIC_C = 10.0
RIDGE_ALPHA = 1000.0
LUNA_FOLDS = 5


@dataclass(frozen=True)
class TasteHead:
    center: np.ndarray  # (d,) mean raw vector, subtracted before L2 normalization
    weights: np.ndarray  # (d + 1,) logistic weights on [unit image vector, luna z-score]
    bias: float
    luna_weights: np.ndarray  # (d,) luna ridge folded into an affine map on raw vectors
    luna_bias: float
    luna_mean: float
    luna_std: float

    def score(self, vectors: np.ndarray) -> np.ndarray:
        raw = vectors.astype(np.float64)
        image = raw - self.center
        image /= np.linalg.norm(image, axis=1, keepdims=True)
        luna = (raw @ self.luna_weights + self.luna_bias - self.luna_mean) / self.luna_std
        return image @ self.weights[:-1] + luna * self.weights[-1] + self.bias

    def to_bytes(self) -> bytes:
        buffer = BytesIO()
        np.savez(
            buffer,
            center=self.center,
            weights=self.weights,
            bias=self.bias,
            luna_weights=self.luna_weights,
            luna_bias=self.luna_bias,
            luna_mean=self.luna_mean,
            luna_std=self.luna_std,
        )
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> TasteHead:
        with np.load(BytesIO(data), allow_pickle=False) as z:
            return cls(
                center=z["center"],
                weights=z["weights"],
                bias=float(z["bias"]),
                luna_weights=z["luna_weights"],
                luna_bias=float(z["luna_bias"]),
                luna_mean=float(z["luna_mean"]),
                luna_std=float(z["luna_std"]),
            )


def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float = RIDGE_ALPHA) -> tuple[np.ndarray, float]:
    """Ridge with an unpenalized intercept, as sklearn Ridge(alpha). Returns (coef, intercept)."""
    x_mean, y_mean = X.mean(0), y.mean()
    Xc = X - x_mean
    coef = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(X.shape[1]), Xc.T @ (y - y_mean))
    return coef, float(y_mean - x_mean @ coef)


def fit_logistic(
    X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray, C: float = LOGISTIC_C
) -> tuple[np.ndarray, float]:
    """L2 logistic regression as sklearn LogisticRegression(C, class_weight="balanced"), solved by Newton's
    method with a backtracking line search. Minimizes C * sum_i s_i * logloss_i + 0.5 * ||coef||^2 where s_i
    is the sample weight times n / (2 * unweighted count of the sample's class). Returns (coef, intercept)."""
    s = sample_weight * (len(y) / (2 * np.bincount(y.astype(np.int64), minlength=2)))[y.astype(np.int64)]
    A = np.c_[X, np.ones(len(X))]
    penalty = np.r_[np.ones(X.shape[1]), 0.0]

    def objective(beta: np.ndarray) -> float:
        z = A @ beta
        return float(C * s @ (np.logaddexp(0, z) - y * z) + 0.5 * beta[:-1] @ beta[:-1])

    beta = np.zeros(A.shape[1])
    value = objective(beta)
    for _ in range(100):
        p = 1 / (1 + np.exp(-(A @ beta)))
        grad = C * A.T @ (s * (p - y)) + penalty * beta
        if np.abs(grad).max() < 1e-9:
            break
        hessian = C * (A.T * (s * p * (1 - p))) @ A + np.diag(penalty)
        step = np.linalg.solve(hessian, grad)
        t = 1.0
        while (new_value := objective(beta - t * step)) > value - 1e-4 * t * (grad @ step):
            t /= 2
        beta, value = beta - t * step, new_value
    return beta[:-1], float(beta[-1])


def _luna_affine(raw: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, float]:
    """Ridge on standardized raw vectors, folded into weights and bias that apply to raw vectors directly."""
    mean, std = raw.mean(0), raw.std(0)
    coef, intercept = fit_ridge((raw - mean) / std, target)
    weights = coef / std
    return weights, intercept - float(mean @ weights)


def train_head(
    vectors: np.ndarray, verdict: np.ndarray, luna_target: np.ndarray, rng: np.random.Generator
) -> TasteHead:
    raw = vectors.astype(np.float64)
    n, d = raw.shape

    # Luna feature: out-of-fold predictions for rows with a target (the scaler is refit per fold, as in
    # the research), the full-data model for the rest; z-scored with the out-of-fold mean and std.
    known = np.flatnonzero(np.isfinite(luna_target))
    if len(known):
        oof = np.empty(len(known))
        for fold in np.array_split(rng.permutation(len(known)), LUNA_FOLDS):
            train = np.setdiff1d(np.arange(len(known)), fold)
            w, b = _luna_affine(raw[known[train]], luna_target[known[train]])
            oof[fold] = raw[known[fold]] @ w + b
        luna_weights, luna_bias = _luna_affine(raw[known], luna_target[known])
        luna_raw = raw @ luna_weights + luna_bias
        luna_raw[known] = oof
        luna_mean, luna_std = float(oof.mean()), float(oof.std())
    else:
        luna_weights, luna_bias, luna_raw, luna_mean, luna_std = np.zeros(d), 0.0, np.zeros(n), 0.0, 1.0

    center = raw.mean(0)
    image = raw - center
    image /= np.linalg.norm(image, axis=1, keepdims=True)
    features = np.c_[image, (luna_raw - luna_mean) / luna_std]

    likes, dislikes, unrated = (np.flatnonzero(verdict == v) for v in (1, 0, -1))
    negatives = rng.choice(unrated, min(MAX_NEGATIVES, len(unrated) // 2), replace=False)
    rows = np.r_[likes, dislikes, negatives]
    labels = np.r_[np.ones(len(likes)), np.zeros(len(dislikes) + len(negatives))]
    sample_weight = np.r_[np.ones(len(likes)), np.full(len(dislikes), DISLIKE_WEIGHT), np.ones(len(negatives))]
    weights, bias = fit_logistic(features[rows], labels, sample_weight)
    return TasteHead(center, weights, bias, luna_weights, luna_bias, luna_mean, luna_std)
