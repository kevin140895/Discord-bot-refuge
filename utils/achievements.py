from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AchievementDefinition:
    """Static definition of one user achievement."""

    id: str
    name: str
    description: str
    category: str
    metric: str
    threshold: int
    emoji: str


ACHIEVEMENTS: tuple[AchievementDefinition, ...] = (
    AchievementDefinition(
        id="level_5",
        name="Membre Bronze",
        description="Atteindre le niveau 5.",
        category="xp",
        metric="level",
        threshold=5,
        emoji="🥉",
    ),
    AchievementDefinition(
        id="level_10",
        name="Membre Argent",
        description="Atteindre le niveau 10.",
        category="xp",
        metric="level",
        threshold=10,
        emoji="🥈",
    ),
    AchievementDefinition(
        id="level_20",
        name="Membre Or",
        description="Atteindre le niveau 20.",
        category="xp",
        metric="level",
        threshold=20,
        emoji="🥇",
    ),
    AchievementDefinition(
        id="casino_1_bet",
        name="Premier pari",
        description="Participer à un premier pari XP.",
        category="casino",
        metric="casino_bets",
        threshold=1,
        emoji="🎲",
    ),
    AchievementDefinition(
        id="casino_10_bets",
        name="Habitué du casino",
        description="Participer à 10 paris XP.",
        category="casino",
        metric="casino_bets",
        threshold=10,
        emoji="🎰",
    ),
    AchievementDefinition(
        id="casino_50_bets",
        name="Vétéran du casino",
        description="Participer à 50 paris XP.",
        category="casino",
        metric="casino_bets",
        threshold=50,
        emoji="🃏",
    ),
    AchievementDefinition(
        id="tenure_30_days",
        name="Un mois au Refuge",
        description="Être membre du Refuge depuis 30 jours.",
        category="anciennete",
        metric="tenure_days",
        threshold=30,
        emoji="🌱",
    ),
    AchievementDefinition(
        id="tenure_180_days",
        name="Pilier du Refuge",
        description="Être membre du Refuge depuis 180 jours.",
        category="anciennete",
        metric="tenure_days",
        threshold=180,
        emoji="🏕️",
    ),
    AchievementDefinition(
        id="tenure_365_days",
        name="Un an au Refuge",
        description="Être membre du Refuge depuis 365 jours.",
        category="anciennete",
        metric="tenure_days",
        threshold=365,
        emoji="🏆",
    ),
)

ACHIEVEMENT_BY_ID = {achievement.id: achievement for achievement in ACHIEVEMENTS}
CATEGORY_LABELS = {
    "xp": "Progression XP",
    "casino": "Casino XP",
    "anciennete": "Ancienneté",
}


def qualifying_achievement_ids(metrics: Mapping[str, int]) -> list[str]:
    """Return all achievements whose metric threshold is currently reached."""

    qualified: list[str] = []
    for achievement in ACHIEVEMENTS:
        try:
            value = int(metrics.get(achievement.metric, 0))
        except (TypeError, ValueError):
            value = 0
        if value >= achievement.threshold:
            qualified.append(achievement.id)
    return qualified


def achievement_progress(
    achievement: AchievementDefinition,
    metrics: Mapping[str, int],
) -> tuple[int, int]:
    """Return clamped ``(current, target)`` progress for display."""

    try:
        current = int(metrics.get(achievement.metric, 0))
    except (TypeError, ValueError):
        current = 0
    current = max(0, min(current, achievement.threshold))
    return current, achievement.threshold
