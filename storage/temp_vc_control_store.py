from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Mapping

from config import DATA_DIR
from utils.persistence import atomic_write_json_async, read_json_safe

OWNERS_FILE = Path(DATA_DIR) / "temp_vc_owners.json"


def load_temp_vc_owners() -> Dict[int, int]:
    """Load ``channel_id -> owner_id`` for controllable temporary voice channels."""
    data = read_json_safe(OWNERS_FILE)
    if not isinstance(data, dict):
        return {}

    owners: Dict[int, int] = {}
    for channel_id, owner_id in data.items():
        try:
            owners[int(channel_id)] = int(owner_id)
        except (TypeError, ValueError):
            logging.warning(
                "[temp_vc_control_store] Entrée propriétaire invalide ignorée: %r -> %r",
                channel_id,
                owner_id,
            )
    return owners


async def save_temp_vc_owners_async(mapping: Mapping[int, int]) -> None:
    """Persist temporary voice owners atomically."""
    payload = {
        str(int(channel_id)): int(owner_id)
        for channel_id, owner_id in mapping.items()
    }
    await atomic_write_json_async(OWNERS_FILE, payload)
