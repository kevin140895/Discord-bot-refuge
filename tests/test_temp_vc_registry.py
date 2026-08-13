import json
from unittest.mock import AsyncMock

import pytest

import storage.temp_vc_store as store


def test_load_registry_keeps_only_complete_consistent_records(tmp_path, monkeypatch):
    registry_file = tmp_path / "temp_vc_registry.json"
    registry_file.write_text(
        json.dumps(
            {
                "10": {
                    "channel_id": 10,
                    "owner_id": 20,
                    "created_at": "2026-08-13T00:00:00+00:00",
                    "type": "generic",
                },
                "11": {
                    "channel_id": 999,
                    "owner_id": 21,
                    "created_at": "2026-08-13T00:00:01+00:00",
                    "type": "generic",
                },
                "12": {
                    "channel_id": 12,
                    "owner_id": 0,
                    "created_at": "",
                    "type": "generic",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "TEMP_VC_REGISTRY_FILE", registry_file)

    records = store.load_temp_vc_registry()

    assert records == {
        10: {
            "channel_id": 10,
            "owner_id": 20,
            "created_at": "2026-08-13T00:00:00+00:00",
            "type": "generic",
        }
    }


@pytest.mark.asyncio
async def test_save_registry_persists_full_provenance(monkeypatch):
    writer = AsyncMock()
    monkeypatch.setattr(store, "atomic_write_json_async", writer)

    record = store.build_temp_vc_record(
        100,
        200,
        "2026-08-13T00:00:00+00:00",
    )
    await store.save_temp_vc_registry_async({100: record})

    writer.assert_awaited_once_with(
        store.TEMP_VC_REGISTRY_FILE,
        {
            "100": {
                "channel_id": 100,
                "owner_id": 200,
                "created_at": "2026-08-13T00:00:00+00:00",
                "type": "generic",
            }
        },
    )
