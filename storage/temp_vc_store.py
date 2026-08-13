import asyncio
import logging
from pathlib import Path
from typing import Dict, Iterable, Mapping, Set, TypedDict

from config import DATA_DIR
from utils.persistence import atomic_write_json, atomic_write_json_async, read_json_safe

# Legacy ID-only store kept only for safe migration of existing deployments.
DATA_FILE = Path(DATA_DIR) / "temp_vc_ids.json"
TEMP_VC_REGISTRY_FILE = Path(DATA_DIR) / "temp_vc_registry.json"
LAST_NAMES_FILE = Path(DATA_DIR) / "temp_vc_last_names.json"
STREAMER_TEMP_VC_FILE = Path(DATA_DIR) / "streamer_temp_vc.json"

GENERIC_TEMP_VC_TYPE = "generic"


class TempVCRecord(TypedDict):
    """Persisted proof that a temporary voice channel was created by the bot."""

    channel_id: int
    owner_id: int
    created_at: str
    type: str


def build_temp_vc_record(
    channel_id: int,
    owner_id: int,
    created_at: str,
    *,
    record_type: str = GENERIC_TEMP_VC_TYPE,
) -> TempVCRecord:
    """Build and validate a provenance record before it is persisted."""
    cid = int(channel_id)
    oid = int(owner_id)
    timestamp = str(created_at).strip()
    kind = str(record_type).strip()

    if cid <= 0:
        raise ValueError("channel_id must be positive")
    if oid <= 0:
        raise ValueError("owner_id must be positive")
    if not timestamp:
        raise ValueError("created_at must not be empty")
    if not kind:
        raise ValueError("type must not be empty")

    return {
        "channel_id": cid,
        "owner_id": oid,
        "created_at": timestamp,
        "type": kind,
    }


def load_temp_vc_registry() -> Dict[int, TempVCRecord]:
    """Load validated Temp VC provenance records keyed by ``channel_id``."""
    data = read_json_safe(TEMP_VC_REGISTRY_FILE)
    if not isinstance(data, dict):
        return {}

    records: Dict[int, TempVCRecord] = {}
    for raw_key, raw_record in data.items():
        if not isinstance(raw_record, dict):
            logging.warning(
                "[temp_vc_store] Entrée registre invalide ignorée: %r -> %r",
                raw_key,
                raw_record,
            )
            continue
        try:
            key = int(raw_key)
            record = build_temp_vc_record(
                raw_record["channel_id"],
                raw_record["owner_id"],
                raw_record["created_at"],
                record_type=raw_record["type"],
            )
        except (KeyError, TypeError, ValueError):
            logging.warning(
                "[temp_vc_store] Entrée registre invalide ignorée: %r -> %r",
                raw_key,
                raw_record,
            )
            continue

        if record["channel_id"] != key:
            logging.warning(
                "[temp_vc_store] Entrée registre incohérente ignorée: clé=%s channel_id=%s",
                key,
                record["channel_id"],
            )
            continue
        records[key] = record
    return records


async def save_temp_vc_registry_async(
    records: Mapping[int, TempVCRecord], max_retries: int = 3
) -> None:
    """Persist Temp VC provenance records atomically, raising after final failure."""
    payload = {
        str(int(channel_id)): {
            "channel_id": int(record["channel_id"]),
            "owner_id": int(record["owner_id"]),
            "created_at": str(record["created_at"]),
            "type": str(record["type"]),
        }
        for channel_id, record in records.items()
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            await atomic_write_json_async(TEMP_VC_REGISTRY_FILE, payload)
            return
        except Exception as exc:
            last_error = exc
            logging.error(
                "[temp_vc_store] Écriture registre échouée pour %s: %s (tentative %d/%d)",
                TEMP_VC_REGISTRY_FILE,
                exc,
                attempt + 1,
                max_retries,
            )
            if attempt + 1 < max_retries:
                await asyncio.sleep(2 ** attempt)

    if last_error is not None:
        raise last_error


def load_temp_vc_ids() -> Set[int]:
    """Charge l'ancien store ID-only, uniquement pour migration contrôlée."""
    data = read_json_safe(DATA_FILE)
    if isinstance(data, list):
        return set(int(x) for x in data)
    return set()


def save_temp_vc_ids(ids: Iterable[int]) -> None:
    """Persiste l'ancien store ID-only (compatibilité/migration uniquement)."""
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
    """Sauvegarde l'ancien store ID-only (compatibilité/migration uniquement)."""
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
