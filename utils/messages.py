"""Message templates for various features."""
from __future__ import annotations

LEVEL_FEED_TEMPLATES = {
    "pari_xp_up": (
        "🔥 {mention} passe **niveau {new_level}**\n"
        "+{xp_gain} XP – activité détectée 💬⚡\n\n"
        "GG ! Le Refuge te voit 👀"
    ),
    "machine_a_sous_up": (
        "🔥 {mention} passe **niveau {new_level}**\n"
        "+{xp_gain} XP – activité détectée 💬⚡\n\n"
        "GG ! Le Refuge te voit 👀"
    ),
    "pari_xp_down": (
        "{mention} repasse au **niveau {new_level}**\n"
        "(—{xp_loss} XP)\n\n"
        "Pas grave ! Le Refuge t’attend pour remonter ⚔️"
    ),
    "message_up": (
        "🔥 {mention} passe **niveau {new_level}**\n"
        "+{xp_gain} XP – activité détectée 💬⚡\n\n"
        "GG ! Le Refuge te voit 👀"
    ),
    "message_down": (
        "{mention} repasse au **niveau {new_level}**\n"
        "(—{xp_loss} XP)\n\n"
        "Pas grave ! Le Refuge t’attend pour remonter ⚔️"
    ),
}

__all__ = ["LEVEL_FEED_TEMPLATES"]
