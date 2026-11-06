from typing import Literal

from fastapi import APIRouter

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state
from vsniper.domain.contracts import AiCostStats, DashboardStats, ScoreDistribution

router = APIRouter(tags=["stats"])


@router.get("/users/{user_id}/stats/dashboard", response_model=DashboardStats)
def dashboard_stats(user_id: UserId) -> DashboardStats:
    return get_state().candidates.get_dashboard_stats(user_id)


@router.get("/stats/costs", response_model=AiCostStats)
def ai_cost_stats() -> AiCostStats:
    return get_state().candidates.get_ai_cost_stats()


@router.get("/users/{user_id}/stats/score-distribution", response_model=ScoreDistribution)
def score_distribution(
    user_id: UserId, window: Literal["1h", "6h", "12h", "1d", "7d", "30d", "all"] = "7d") -> ScoreDistribution:
    return get_state().candidates.get_score_distribution(user_id, window)
