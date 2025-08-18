import asyncio
import logging
import os
import types
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.rate_limit import GlobalRateLimiter
from storage.xp_store import xp_store
from utils.rename_manager import rename_manager
from utils.channel_edit_manager import channel_edit_manager
from view import PlayerTypeView

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True
intents.presences = True

class RefugeBot(commands.Bot):
    async def setup_hook(self) -> None:
        self.add_view(PlayerTypeView())
        await xp_store.start()
        extensions = [
            "cogs.role_reminder",
            "cogs.roulette",
            "cogs.xp",
            "cogs.temp_vc",
            "cogs.misc",
            "cogs.radio",
            "cogs.stats",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
            except Exception:
                logging.exception("Failed to load extension %s", ext)
        limiter.start()
        await rename_manager.start()
        await channel_edit_manager.start()

    async def close(self) -> None:
        rename_manager.stop()
        channel_edit_manager.stop()
        await xp_store.aclose()
        await super().close()


bot = RefugeBot(command_prefix="!", intents=intents)

limiter = GlobalRateLimiter()
_orig_request = bot.http.request


async def _limited_request(self, route, **kwargs):
    buckets = ["global"]
    path = route.path
    method = route.method

    if method == "POST" and path.startswith("/channels") and path.endswith("/messages"):
        channel_id = path.split("/")[2]
        buckets.append(f"channel:{channel_id}")
    if method == "PATCH" and path.startswith("/channels") and "/messages" not in path:
        channel_id = path.split("/")[2]
        buckets.append("channel_edit")
        buckets.append(f"channel_edit:{channel_id}")
    if "reactions" in path:
        buckets.append("reactions")
    if "/members/" in path and "/roles/" in path:
        parts = path.split("/")
        if len(parts) > 5:
            user_id = parts[4]
            buckets.append(f"roles:{user_id}")

    for bucket in buckets:
        await limiter.acquire(bucket=bucket)

    max_attempts = 5
    attempts = 0
    while True:
        try:
            return await _orig_request(route, **kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = 0.0
                if e.response is not None:
                    retry_after = float(e.response.headers.get("Retry-After", 0))
                    if retry_after == 0:
                        try:
                            data = await e.response.json()
                            retry_after = data.get("retry_after", 0)
                        except Exception:
                            pass
                await asyncio.sleep(
                    retry_after + random.uniform(0.05, 0.25)
                )
                attempts += 1
                if attempts >= max_attempts:
                    raise
                continue
            raise


bot.http.request = types.MethodType(_limited_request, bot.http)




TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables"
    )

if __name__ == "__main__":
    bot.run(TOKEN)
