from __future__ import annotations

import logging
import shutil

from discord.ext import commands
from yt_dlp.version import __version__ as YT_DLP_VERSION


logger = logging.getLogger(__name__)

YTDLP_UPSTREAM_SHA = "5d6b8c8cd19785c3086ae3a9ec618c45e25eb3bc"


def runtime_snapshot() -> dict[str, str | None]:
    """Return the Music 2.0 yt-dlp runtime details useful in Railway logs."""
    return {
        "yt_dlp_version": YT_DLP_VERSION,
        "upstream_sha": YTDLP_UPSTREAM_SHA,
        "deno_path": shutil.which("deno"),
    }


class Music2YtDlpDiagnosticsCog(commands.Cog):
    """Log Music 2.0 extraction runtime details without altering playback."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        snapshot = runtime_snapshot()
        logger.info(
            "[music2] yt-dlp runtime: version=%s upstream_sha=%s deno=%s",
            snapshot["yt_dlp_version"],
            snapshot["upstream_sha"],
            snapshot["deno_path"] or "introuvable",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music2YtDlpDiagnosticsCog(bot))
