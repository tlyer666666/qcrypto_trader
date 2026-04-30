from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig


@dataclass(frozen=True)
class RiskProfile:
    name: str
    max_notional_per_position: float
    max_total_notional: float
    max_daily_loss: float
    max_consecutive_losses: int
    min_confidence_offset: float


PROFILES: dict[str, RiskProfile] = {
    "capital_protection": RiskProfile(
        name="capital_protection",
        max_notional_per_position=50,
        max_total_notional=100,
        max_daily_loss=20,
        max_consecutive_losses=2,
        min_confidence_offset=0.08,
    ),
    "balanced": RiskProfile(
        name="balanced",
        max_notional_per_position=100,
        max_total_notional=300,
        max_daily_loss=50,
        max_consecutive_losses=3,
        min_confidence_offset=0.0,
    ),
    "aggressive": RiskProfile(
        name="aggressive",
        max_notional_per_position=250,
        max_total_notional=800,
        max_daily_loss=150,
        max_consecutive_losses=5,
        min_confidence_offset=-0.08,
    ),
}


def resolve_profile(risk: RiskConfig) -> RiskProfile:
    return PROFILES.get(risk.risk_mode, PROFILES["balanced"])


def effective_risk_limits(risk: RiskConfig) -> RiskProfile:
    profile = resolve_profile(risk)
    return RiskProfile(
        name=profile.name,
        max_notional_per_position=min(
            risk.max_notional_per_position, profile.max_notional_per_position
        ),
        max_total_notional=min(risk.max_total_notional, profile.max_total_notional),
        max_daily_loss=min(risk.max_daily_loss, profile.max_daily_loss),
        max_consecutive_losses=min(
            risk.max_consecutive_losses, profile.max_consecutive_losses
        ),
        min_confidence_offset=profile.min_confidence_offset,
    )

