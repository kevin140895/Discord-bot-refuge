import logging
from typing import Mapping

import discord
from discord.ext import commands

from storage.temp_vc_store import GENERIC_TEMP_VC_TYPE, TempVCRecord

logger = logging.getLogger(__name__)


def _is_complete_provenance_record(
    channel_id: int,
    record: TempVCRecord,
    *,
    expected_type: str,
) -> bool:
    """Return whether ``record`` is sufficient proof for destructive cleanup."""
    try:
        return (
            int(record["channel_id"]) == int(channel_id)
            and int(record["owner_id"]) > 0
            and bool(str(record["created_at"]).strip())
            and str(record["type"]) == expected_type
        )
    except (KeyError, TypeError, ValueError):
        return False


async def delete_empty_managed_temp_vcs(
    bot: commands.Bot,
    records: Mapping[int, TempVCRecord],
    *,
    expected_type: str = GENERIC_TEMP_VC_TYPE,
) -> set[int]:
    """Delete only empty Temp VCs backed by complete provenance records.

    The cleanup deliberately never discovers channels from category membership or
    channel names. A channel is eligible for deletion only when its ``channel_id``
    is present in the persisted registry with ``owner_id``, ``created_at`` and the
    expected ``type``. The stored creation timestamp must also match Discord's
    snowflake-backed ``created_at`` value for the live channel.
    """
    deleted: set[int] = set()

    for channel_id, record in records.items():
        if not _is_complete_provenance_record(
            channel_id,
            record,
            expected_type=expected_type,
        ):
            logger.warning(
                "[temp_vc_cleanup] preuve incomplète pour le salon %s; suppression ignorée",
                channel_id,
            )
            continue

        channel = bot.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            continue
        if channel.id != int(record["channel_id"]):
            continue
        if channel.created_at.isoformat() != str(record["created_at"]):
            logger.warning(
                "[temp_vc_cleanup] created_at incohérent pour le salon %s; suppression ignorée",
                channel_id,
            )
            continue
        if channel.members:
            continue

        try:
            await channel.delete(reason="Salon temporaire vide (registre bot)")
        except discord.HTTPException as exc:
            logger.warning("Suppression salon %s échouée: %s", channel.id, exc)
        else:
            deleted.add(channel.id)

    return deleted
