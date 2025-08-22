import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from config import GAMES_DATA_DIR
from utils.persistence import ensure_dir, read_json_safe, atomic_write_json_async

__all__ = [
    "GameEvent",
    "EVENTS",
    "load_events",
    "save_event",
    "get_multiplier",
    "record_participant",
    "set_voice_channel",
]


@dataclass
class GameEvent:
    id: str
    guild_id: int
    creator_id: int
    game_type: str
    game_name: str
    time: datetime
    channel_id: int
    message_id: int
    rsvps: Dict[str, str] = field(default_factory=dict)  # uid -> status
    first_bonus: bool = False
    voice_channel_id: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    participants: Set[int] = field(default_factory=set)
    state: str = "scheduled"  # scheduled, waiting, running, finished, cancelled
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["time"] = self.time.astimezone(timezone.utc).isoformat()
        d["created_at"] = self.created_at.astimezone(timezone.utc).isoformat()
        d["started_at"] = (
            self.started_at.astimezone(timezone.utc).isoformat()
            if self.started_at
            else None
        )
        d["ended_at"] = (
            self.ended_at.astimezone(timezone.utc).isoformat()
            if self.ended_at
            else None
        )
        d["participants"] = list(self.participants)
        return d

    @staticmethod
    def from_dict(data: Dict) -> "GameEvent":
        def parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            try:
                return datetime.fromisoformat(val).astimezone(timezone.utc)
            except ValueError:
                return None

        return GameEvent(
            id=data.get("id", uuid.uuid4().hex),
            guild_id=int(data.get("guild_id", 0)),
            creator_id=int(data.get("creator_id", 0)),
            game_type=data.get("game_type", ""),
            game_name=data.get("game_name", ""),
            time=parse_dt(data.get("time")) or datetime.now(timezone.utc),
            channel_id=int(data.get("channel_id", 0)),
            message_id=int(data.get("message_id", 0)),
            rsvps={str(k): str(v) for k, v in data.get("rsvps", {}).items()},
            first_bonus=bool(data.get("first_bonus", False)),
            voice_channel_id=(
                int(data["voice_channel_id"]) if data.get("voice_channel_id") else None
            ),
            started_at=parse_dt(data.get("started_at")),
            ended_at=parse_dt(data.get("ended_at")),
            participants=set(map(int, data.get("participants", []))),
            state=data.get("state", "scheduled"),
            created_at=parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
        )


EVENTS: Dict[str, GameEvent] = {}
VC_INDEX: Dict[int, str] = {}
_events_lock = asyncio.Lock()


def load_events() -> None:
    ensure_dir(GAMES_DATA_DIR)
    for fname in os.listdir(GAMES_DATA_DIR):
        if not fname.endswith(".json"):
            continue
        data = read_json_safe(os.path.join(GAMES_DATA_DIR, fname))
        try:
            evt = GameEvent.from_dict(data)
        except Exception:
            logging.exception("[game] Échec chargement événement %s", fname)
            continue
        EVENTS[evt.id] = evt
        if evt.voice_channel_id:
            VC_INDEX[evt.voice_channel_id] = evt.id
    logging.info("[game] %d événements chargés", len(EVENTS))


def _path_for(event_id: str) -> str:
    return os.path.join(GAMES_DATA_DIR, f"{event_id}.json")


async def save_event(evt: GameEvent) -> None:
    ensure_dir(GAMES_DATA_DIR)
    await atomic_write_json_async(_path_for(evt.id), evt.to_dict())


def set_voice_channel(evt: GameEvent, vc_id: Optional[int]) -> None:
    if evt.voice_channel_id:
        VC_INDEX.pop(evt.voice_channel_id, None)
    evt.voice_channel_id = vc_id
    if vc_id:
        VC_INDEX[vc_id] = evt.id


def get_multiplier(channel_id: int, user_id: int) -> float:
    eid = VC_INDEX.get(channel_id)
    if not eid:
        return 1.0
    evt = EVENTS.get(eid)
    if not evt:
        return 1.0
    status = evt.rsvps.get(str(user_id))
    if status == "yes":
        return 2.0
    if status == "maybe":
        return 1.5
    return 1.0


def record_participant(channel_id: int, user_id: int) -> None:
    eid = VC_INDEX.get(channel_id)
    if not eid:
        return
    evt = EVENTS.get(eid)
    if evt:
        evt.participants.add(user_id)

