from __future__ import annotations

import numpy as np

from vsniper.domain.scoring.taste_head import (
    LOGISTIC_C,
    TasteHead,
    fit_logistic,
    fit_ridge,
    train_head,
)


def test_fit_ridge_matches_closed_form() -> None:
    rng = np.random.default_rng(0)
    X, y = rng.normal(size=(20, 4)), rng.normal(size=20)
    coef, intercept = fit_ridge(X, y, alpha=3.0)

    # Augmented least squares with an unpenalized intercept column.
    A = np.c_[X, np.ones(20)]
    expected = np.linalg.solve(A.T @ A + np.diag([3.0] * 4 + [0.0]), A.T @ y)
    np.testing.assert_allclose(np.r_[coef, intercept], expected, atol=1e-10)


def test_fit_logistic_reaches_weighted_optimum() -> None:
    rng = np.random.default_rng(1)
    X = rng.normal(size=(200, 5))
    y = (X @ np.array([2.0, -1.0, 0.0, 0.5, 0.0]) + rng.normal(size=200) > 1.0).astype(np.float64)
    w = rng.uniform(0.5, 3.0, size=200)
    coef, intercept = fit_logistic(X, y, w)

    s = w * (200 / (2 * np.bincount(y.astype(int))))[y.astype(int)]
    p = 1 / (1 + np.exp(-(X @ coef + intercept)))
    np.testing.assert_allclose(LOGISTIC_C * X.T @ (s * (p - y)) + coef, 0, atol=1e-7)
    assert abs(LOGISTIC_C * s @ (p - y)) < 1e-7
    assert coef[0] > 0 > coef[1]


def test_head_ranks_like_cluster_first_and_round_trips() -> None:
    rng = np.random.default_rng(2)
    d, liked = 32, np.zeros(32)
    liked[0] = 4.0
    background = rng.normal(size=(600, d))
    likes = rng.normal(size=(10, d)) + liked
    vectors = np.vstack([background, likes]).astype(np.float32)
    verdict = np.r_[np.full(600, -1), np.ones(10)].astype(int)
    luna_target = np.where(rng.random(610) < 0.8, 50 + 10 * vectors[:, 0], np.nan)
    head = train_head(vectors, verdict, luna_target, rng)

    held_likes = (rng.normal(size=(20, d)) + liked).astype(np.float16)
    held_random = rng.normal(size=(200, d)).astype(np.float16)
    assert np.median(head.score(held_likes)) > np.quantile(head.score(held_random), 0.95)
    np.testing.assert_array_equal(TasteHead.from_bytes(head.to_bytes()).score(held_likes), head.score(held_likes))


def test_head_without_luna_targets_has_zero_luna_feature() -> None:
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(100, 8))
    verdict = np.r_[np.full(90, -1), np.ones(5), np.zeros(5)].astype(int)
    head = train_head(vectors, verdict, np.full(100, np.nan), rng)

    assert not head.luna_weights.any()
    assert np.isfinite(head.score(vectors)).all()
