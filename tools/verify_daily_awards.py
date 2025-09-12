"""Dry-run verification for Daily Awards formatting."""

import asyncio
import re
import sys
from pathlib import Path

import discord

# Ensure repository root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cogs.daily_awards as da
from cogs.daily_awards import DailyAwards


def _sample_data():
    return {
        "top3": {
            "mvp": [
                {
                    "id": 1,
                    "score": 373.33,
                    "messages": 29,
                    "voice": 344,
                }
            ],
            "msg": [{"id": 2, "count": 29}],
            "vc": [{"id": 3, "minutes": 344}],
        }
    }


async def main() -> int:
    data = _sample_data()
    cog = DailyAwards.__new__(DailyAwards)
    embed = await DailyAwards._build_embed(cog, data)
    content = "@everyone"
    allowed = discord.AllowedMentions(everyone=True)

    errors = []

    if embed.title != "📢 Annonce des gagnants — classement de 00h00":
        errors.append("Titre incorrect")
    if embed.colour.value != 0xFF1801:
        errors.append("Couleur incorrecte")
    if len(embed.fields) != 3:
        errors.append("Nombre de sections différent de 3")
    if not re.match(r"Date : \d{2}/\d{2}/\d{4}$", embed.footer.text):
        errors.append("Date de footer invalide")
    if da.AWARD_ANNOUNCE_CHANNEL_ID != 1400552164979507263:
        errors.append(
            f"AWARD_ANNOUNCE_CHANNEL_ID={da.AWARD_ANNOUNCE_CHANNEL_ID} attendu 1400552164979507263"
        )

    print(f"Title: {embed.title}")
    for f in embed.fields:
        print(f"Field: {f.name} -> {f.value}")
    print(f"Footer: {embed.footer.text}")
    print(f"Content: {content}")
    print(f"AllowedMentions everyone: {allowed.everyone}")
    print(f"Channel target: {da.AWARD_ANNOUNCE_CHANNEL_ID}")

    if errors:
        for e in errors:
            print("ERROR:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
