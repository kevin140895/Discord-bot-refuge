"""Message templates for various features."""
from __future__ import annotations

LEVEL_FEED_TEMPLATES = {
    "pari_xp_up": "🆙 {mention} passe **niv. {new_level}** (de {old_level}) grâce à 🤑 *Roulette Refuge* ! ({xp_delta} XP)",
    "machine_a_sous_up": "🆙 {mention} passe **niv. {new_level}** (de {old_level}) grâce à 🎰 *Machine à sous* ! ({xp_delta} XP)",
    "pari_xp_down": "⬇️ {mention} retombe au **niv. {new_level}** (depuis {old_level}) à cause de 🤑 *Roulette Refuge*. ({xp_delta} XP)",
}

__all__ = ["LEVEL_FEED_TEMPLATES"]
