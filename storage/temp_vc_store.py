import asyncio
import logging
from pathlib import Path
from typing import Dict, Iterable, Mapping, Set

from config import DATA_DIR
from utils.persistence import atomic_write_json, atomic_write_json_async, read_json_safe

DATA_FILE = Path(DATA_DIR) / "temp_vc_ids.json"
LAST_NAMES_FILE = Path(DATA_DIR) / "temp_vc_last_names.json"
STREAMER_TEMP_VC_FILE = Path(DATA_DIR) / "streamer_temp_vc.json"


def load_temp_vc_ids() -> Set[int]:
    """Charge la liste des salons temporaires persistés."""
    data = read_json_safe(DATA_FILE)
    if isinstance(data, list):
        return set(int(x) for x in data)
    return set()


def save_temp_vc_ids(ids: Iterable[int]) -> None:
    """Persiste ``ids`` vers le fichier de stockage."""
    try:
        atomic_write_json(DATA_FILE, sorted(set(int(i) for i in ids)))
    except Exception as e:
        logging.error("[temp_vc_store] Écriture échouée pour %s: %s", DATA_FILE, e)


def load_last_names_cache() -> Dict[int, str]:
    """Charge le cache des derniers noms de salons."""
    data = read_json_safe(LAST_NAMES_FILE)
    if isinstance(data, dict):
        return {int(k): str(v) for k, v in data.items()}
    return {}


def load_streamer_temp_vcs() -> Dict[int, int]:
    """Charge le mapping ``channel_id -> owner_id`` des vocaux streamer."""
    data = read_json_safe(STREAMER_TEMP_VC_FILE)
    if not isinstance(data, dict):
        return {}

    mapping: Dict[int, int] = {}
    for channel_id, owner_id in data.items():
        try:
            mapping[int(channel_id)] = int(owner_id)
        except (TypeError, ValueError):
            logging.warning(
                "[temp_vc_store] Entrée streamer invalide ignorée: %r -> %r",
                channel_id,
                owner_id,
            )
    return mapping


async def save_temp_vc_ids_async(
    ids: Iterable[int], max_retries: int = 3
) -> None:
    """Sauvegarde asynchrone sérialisée avec retries des IDs temporaires."""
    payload = sorted(set(int(i) for i in ids))
    for attempt in range(max_retries):
        try:
            await atomic_write_json_async(DATA_FILE, payload)
            return
        except Exception as e:
            logging.error(
                "[temp_vc_store] Écriture échouée pour %s: %s (tentative %d/%d)",
                DATA_FILE,
                e,
                attempt + 1,
                max_retries,
            )
            if attempt + 1 < max_retries:
                await asyncio.sleep(2 ** attempt)


async def save_last_names_cache(
    cache: Dict[int, str], max_retries: int = 3
) -> None:
    """Sauvegarde asynchrone sérialisée avec retries du cache des noms."""
    payload = {str(k): v for k, v in cache.items()}
    for attempt in range(max_retries):
        try:
            await atomic_write_json_async(LAST_NAMES_FILE, payload)
            return
        except Exception as e:
            logging.error(
                "[temp_vc_store] Écriture échouée pour %s: %s (tentative %d/%d)",
                LAST_NAMES_FILE,
                e,
                attempt + 1,
                max_retries,
            )
            if attempt + 1 < max_retries:
                await asyncio.sleep(2 ** attempt)


async def save_streamer_temp_vcs_async(mapping: Mapping[int, int]) -> None:
    """Persiste atomiquement les propriétaires des vocaux streamer temporaires."""
    payload = {str(int(channel_id)): int(owner_id) for channel_id, owner_id in mapping.items()}
    await atomic_write_json_async(STREAMER_TEMP_VC_FILE, payload)
