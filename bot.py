"""Discord bot entry point configuring extensions, rate limits, and tasks."""

import asyncio
import logging
import os
import types
import random
import time

import discord
from discord.ext import commands
from dotenv import load_dotenv

import config
from utils.rate_limit import GlobalRateLimiter
from storage.xp_store import xp_store
from utils.rename_manager import rename_manager
from utils.channel_edit_manager import channel_edit_manager
from view import PlayerTypeView
from utils.api_meter import api_meter, APICallCtx

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

async def announce_level_up(
    guild: discord.Guild,
    user: discord.abc.User,
    old_lvl: int,
    new_lvl: int,
    total_xp: int,
) -> None:
    """Annonce la montée de niveau d'un utilisateur dans un salon dédié."""
    channel = guild.get_channel(config.LEVEL_UP_CHANNEL)
    if channel is None:
        return

    embed = discord.Embed(
        title="🌟 Bravo aventurier du Refuge !",
        description=(
            f"**{user.display_name}** vient de franchir une étape importante ! 🎉\n\n"
            f"{user.mention} est passé du **niveau {old_lvl}** ➝ **niveau {new_lvl}**.\n"
            "Continue ton aventure et montre ta valeur parmi la communauté du Refuge 💪"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"XP total accumulé : {total_xp}")
    embed.set_thumbnail(url=user.display_avatar.url)

    await channel.send(embed=embed)

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
            "cogs.first_message",
            "cogs.temp_vc",
            "cogs.misc",
            "cogs.radio",
            "cogs.rock_radio",
            "cogs.stats",
            "cogs.welcome",
            "cogs.daily_ranking",
            "cogs.daily_summary_poster",
            "cogs.daily_awards",
            "cogs.api_stats",
            "cogs.game_events",
        ]
        async def _load(ext: str) -> None:
            try:
                await self.load_extension(ext)
            except Exception:
                logging.exception("Failed to load extension %s", ext)

        await asyncio.gather(*(_load(ext) for ext in extensions))
        limiter.start()
        await rename_manager.start()
        await channel_edit_manager.start()
        await api_meter.start(self)
        self.error_counter_task = self.loop.create_task(reset_http_error_counter())
        await self.tree.sync()

    async def close(self) -> None:
        rename_manager.stop()
        channel_edit_manager.stop()
        await xp_store.aclose()
        if hasattr(self, "error_counter_task"):
            self.error_counter_task.cancel()
        await super().close()


bot = RefugeBot(command_prefix="!", intents=intents)
bot.announce_level_up = announce_level_up

limiter = GlobalRateLimiter()
_orig_request = bot.http.request

http_error_counter = 0


async def reset_http_error_counter() -> None:
    global http_error_counter
    while True:
        await asyncio.sleep(600)
        if http_error_counter:
            logging.info("HTTP error counter reset from %d", http_error_counter)
            http_error_counter = 0


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
        start = time.perf_counter()
        status = 0
        retry_ms = 0
        error_code = None
        try:
            data = await _orig_request(route, **kwargs)
            status = 200
            return data
        except discord.HTTPException as e:
            status = e.status
            error_code = getattr(e, "code", None)
            if e.status in {401, 403, 429}:
                global http_error_counter
                http_error_counter += 1
                logging.warning("HTTP error count: %d", http_error_counter)
                if http_error_counter >= 10000:
                    logging.critical("HTTP error limit exceeded, shutting down bot")
                    await bot.close()
                    raise
                if http_error_counter >= 9000:
                    logging.error("HTTP error counter high; slowing down")
                    await asyncio.sleep(60)
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
                retry_ms = int(retry_after * 1000)
                await asyncio.sleep(retry_after + random.uniform(0.05, 0.25))
                attempts += 1
                api_meter.record_call(APICallCtx(
                    lib="discord.py",
                    method=method,
                    route=path,
                    major_param=next(iter(route.major_parameters), None),
                    status=status,
                    duration_ms=int((time.perf_counter() - start) * 1000),
                    retry_after_ms=retry_ms,
                    bucket=getattr(route, "bucket", None),
                    ratelimit_remaining=None,
                    ratelimit_reset=None,
                    error_code=error_code,
                    size_bytes=len(kwargs.get("data", b"")) if kwargs.get("data") else 0,
                ))
                if attempts >= max_attempts:
                    raise
                continue
            api_meter.record_call(APICallCtx(
                lib="discord.py",
                method=method,
                route=path,
                major_param=next(iter(route.major_parameters), None),
                status=status,
                duration_ms=int((time.perf_counter() - start) * 1000),
                retry_after_ms=retry_ms,
                bucket=getattr(route, "bucket", None),
                ratelimit_remaining=None,
                ratelimit_reset=None,
                error_code=error_code,
                size_bytes=len(kwargs.get("data", b"")) if kwargs.get("data") else 0,
            ))
            raise
        except Exception:
            api_meter.record_call(APICallCtx(
                lib="discord.py",
                method=method,
                route=path,
                major_param=next(iter(route.major_parameters), None),
                status=status,
                duration_ms=int((time.perf_counter() - start) * 1000),
                retry_after_ms=retry_ms,
                bucket=getattr(route, "bucket", None),
                ratelimit_remaining=None,
                ratelimit_reset=None,
                error_code=error_code,
                size_bytes=len(kwargs.get("data", b"")) if kwargs.get("data") else 0,
            ))
            raise
        finally:
            if status == 200:
                api_meter.record_call(APICallCtx(
                    lib="discord.py",
                    method=method,
                    route=path,
                    major_param=next(iter(route.major_parameters), None),
                    status=status,
                    duration_ms=int((time.perf_counter() - start) * 1000),
                    retry_after_ms=retry_ms,
                    bucket=getattr(route, "bucket", None),
                    ratelimit_remaining=None,
                    ratelimit_reset=None,
                    error_code=error_code,
                    size_bytes=len(kwargs.get("data", b"")) if kwargs.get("data") else 0,
                ))


bot.http.request = types.MethodType(_limited_request, bot.http)


@bot.before_invoke
async def _set_api_context(ctx: commands.Context) -> None:
    cog = ctx.cog.__class__.__name__ if ctx.cog else None
    command = ctx.command.qualified_name if ctx.command else None
    api_meter.set_context(cog, command)


@bot.tree.interaction_check
async def _set_api_context_slash(interaction: discord.Interaction) -> bool:
    cmd = interaction.command.name if interaction.command else None
    cog = getattr(interaction.command, "cog_name", None)
    api_meter.set_context(cog, cmd)
    return True




TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN manquant. Ajoute la variable dans Railway > Service > Variables"
    )

if __name__ == "__main__":
    bot.run(TOKEN)
