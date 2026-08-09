from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Mapping, Sequence

from models.refuge_world import RefugeBuildingState, RefugeHistoricalEvent, RefugeWorldState
from services.member_profile import (
    MemberProfileService,
    MemberProfileSnapshot,
    member_profile_service,
)
from services.refuge_casino import (
    CASINO_LEVEL_NAMES,
    CASINO_MAX_LEVEL,
    RefugeCasinoConfig,
)
from services.refuge_fire import FIRE_LEVEL_NAMES, FIRE_MAX_LEVEL, RefugeFireConfig
from services.refuge_hall import HALL_LEVEL_NAMES, HALL_MAX_LEVEL, RefugeHallConfig
from services.refuge_panel import (
    RefugePanelService,
    RefugePanelSnapshot,
    construction_label,
    event_label,
    refuge_panel_service,
)
from storage.refuge_world_store import RefugeWorldStore, refuge_world_store
from utils.achievements import ACHIEVEMENT_BY_ID
from utils.seasons import season_id_for, season_label
from utils.timezones import PARIS_TZ


EXPLORER_ZONE_ORDER: Final[tuple[str, ...]] = (
    "fire",
    "hall",
    "casino",
    "construction",
    "monuments",
    "mysteries",
)


@dataclass(frozen=True, slots=True)
class RefugeExplorerZoneSnapshot:
    zone_id: str
    title: str
    emoji: str
    details: tuple[str, ...]
    history: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RefugeExplorerSnapshot:
    zones: tuple[RefugeExplorerZoneSnapshot, ...]

    def get_zone(self, zone_id: str) -> RefugeExplorerZoneSnapshot:
        normalized = str(zone_id).strip().lower()
        for zone in self.zones:
            if zone.zone_id == normalized:
                return zone
        raise KeyError(normalized)


@dataclass(frozen=True, slots=True)
class RefugeFootprintTrace:
    occurred_at: str
    label: str


@dataclass(frozen=True, slots=True)
class RefugeFootprintSnapshot:
    user_id: int
    season_id: str
    season_label: str
    level: int
    xp: int
    season_xp: int
    season_messages: int
    season_voice_seconds: int
    season_casino_net: int
    achievements_unlocked: int
    achievements_total: int
    achievement_names: tuple[str, ...]
    casino_bets: int
    casino_net: int
    historical_traces: tuple[RefugeFootprintTrace, ...]


def _aware_utc(at: datetime | None = None) -> datetime:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_date(value: object) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "date inconnue"
    return parsed.astimezone(PARIS_TZ).strftime("%d/%m/%Y")


def _format_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours} h {minutes:02d}"
    return f"{minutes} min"


def _format_number(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _roman(level: int) -> str:
    values = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
    normalized = max(1, min(5, int(level)))
    return values[normalized]


def _building(state: RefugeWorldState, building_id: str) -> RefugeBuildingState | None:
    return next(
        (item for item in state.buildings if item.building_id == building_id),
        None,
    )


def _history_for_building(
    state: RefugeWorldState,
    building_id: str,
    *,
    limit: int = 4,
) -> tuple[str, ...]:
    matching = [
        event
        for event in state.events
        if str(event.data.get("building_id", "")).strip() == building_id
    ]
    matching.sort(
        key=lambda event: (
            _parse_timestamp(event.occurred_at)
            or datetime.min.replace(tzinfo=timezone.utc),
            event.event_id,
        ),
        reverse=True,
    )
    rows: list[str] = []
    for event in matching[: max(0, int(limit))]:
        label = event_label(event) or "Un événement a marqué ce lieu"
        rows.append(f"{_format_date(event.occurred_at)} · {label}")
    return tuple(rows)


def _next_milestone_line(
    *,
    level: int,
    max_level: int,
    thresholds: Sequence[int],
    level_names: Mapping[int, str],
    formatter,
) -> str:
    current_level = max(1, min(int(max_level), int(level)))
    if current_level >= int(max_level):
        return "Prochain palier : niveau maximal atteint."
    values = tuple(int(value) for value in thresholds)
    if len(values) != int(max_level) - 1:
        return "Prochain palier : seuil non calibré."
    target = values[current_level - 1]
    next_name = level_names.get(current_level + 1, f"niveau {current_level + 1}")
    return f"Prochain palier : {next_name} à {formatter(target)}."


def _sequence_count(value: object) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _hall_showcase_name(building: RefugeBuildingState | None) -> str | None:
    if building is None:
        return None
    showcase = building.state.get("rare_showcase")
    if not isinstance(showcase, Mapping):
        return None
    achievement_id = str(showcase.get("achievement_id", "")).strip()
    if not achievement_id:
        return None
    definition = ACHIEVEMENT_BY_ID.get(achievement_id)
    if definition is None:
        return achievement_id
    return f"{definition.emoji} {definition.name}"


def _build_fire_zone(
    *,
    panel: RefugePanelSnapshot,
    config: RefugeFireConfig,
) -> RefugeExplorerZoneSnapshot:
    state = panel.state
    building = _building(state, "fire")
    details = (
        f"Niveau permanent : {_roman(panel.fire_level)} — {panel.fire_name}.",
        f"Intensité récente : {panel.fire_intensity_name}.",
        f"Présent depuis : {_format_date(building.unlocked_at if building else None)}.",
        _next_milestone_line(
            level=panel.fire_level,
            max_level=FIRE_MAX_LEVEL,
            thresholds=config.level_thresholds_seconds,
            level_names=FIRE_LEVEL_NAMES,
            formatter=_format_duration,
        ),
    )
    return RefugeExplorerZoneSnapshot(
        zone_id="fire",
        title="Le Feu",
        emoji="🔥",
        details=details,
        history=_history_for_building(state, "fire"),
    )


def _build_hall_zone(
    *,
    panel: RefugePanelSnapshot,
    config: RefugeHallConfig,
) -> RefugeExplorerZoneSnapshot:
    state = panel.state
    building = _building(state, "hall")
    details = [
        f"Niveau permanent : {_roman(panel.hall_level)} — {panel.hall_name}.",
        f"Présent depuis : {_format_date(building.unlocked_at if building else None)}.",
        _next_milestone_line(
            level=panel.hall_level,
            max_level=HALL_MAX_LEVEL,
            thresholds=config.level_thresholds_points,
            level_names=HALL_LEVEL_NAMES,
            formatter=lambda value: f"{_format_number(value)} pts",
        ),
    ]
    if building is not None:
        plaque_count = _sequence_count(building.state.get("season_plaques"))
        gallery_count = _sequence_count(building.state.get("gallery_markers"))
        if plaque_count:
            details.insert(1, f"Plaques saisonnières inscrites : {plaque_count}.")
        if gallery_count:
            details.insert(1, f"Traces de galerie : {gallery_count}.")
    showcase = _hall_showcase_name(building)
    if showcase:
        details.insert(1, f"Vitrine rare actuelle : {showcase}.")
    return RefugeExplorerZoneSnapshot(
        zone_id="hall",
        title="Le Hall",
        emoji="🏆",
        details=tuple(details),
        history=_history_for_building(state, "hall"),
    )


def _build_casino_zone(
    *,
    panel: RefugePanelSnapshot,
    config: RefugeCasinoConfig,
) -> RefugeExplorerZoneSnapshot:
    state = panel.state
    building = _building(state, "casino")
    open_name = "Ouvert" if panel.casino_is_open else "Fermé"
    details = [
        f"Niveau permanent : {_roman(panel.casino_level)} — {panel.casino_name}.",
        f"Fortune actuelle : {panel.casino_fortune_name}.",
        f"État : {open_name}.",
        f"Présent depuis : {_format_date(building.unlocked_at if building else None)}.",
        _next_milestone_line(
            level=panel.casino_level,
            max_level=CASINO_MAX_LEVEL,
            thresholds=config.level_thresholds_points,
            level_names=CASINO_LEVEL_NAMES,
            formatter=lambda value: f"{_format_number(value)} pts",
        ),
    ]
    if building is not None:
        jackpot = building.state.get("last_jackpot")
        if isinstance(jackpot, Mapping):
            try:
                tier = int(jackpot.get("tier", 0))
            except (TypeError, ValueError):
                tier = 0
            if tier in {500, 1000}:
                details.insert(3, f"Dernier jackpot observé : {tier} XP.")
    return RefugeExplorerZoneSnapshot(
        zone_id="casino",
        title="Le Casino",
        emoji="🎰",
        details=tuple(details),
        history=_history_for_building(state, "casino"),
    )


def _build_construction_zone(state: RefugeWorldState) -> RefugeExplorerZoneSnapshot:
    construction = state.active_construction
    if construction is None:
        return RefugeExplorerZoneSnapshot(
            zone_id="construction",
            title="Le Chantier",
            emoji="🏗️",
            details=(
                "Aucun chantier actif.",
                "Le droit de bâtir s’ouvre uniquement après un accomplissement collectif validé.",
            ),
        )

    details = [construction_label(state)]
    if construction.opened_at:
        details.append(f"Ouvert le : {_format_date(construction.opened_at)}.")
    if construction.started_at:
        details.append(f"Construction commencée le : {_format_date(construction.started_at)}.")
    if construction.completes_at:
        details.append(f"Échéance prévue : {_format_date(construction.completes_at)}.")
    details.append("La progression dépend du temps écoulé, jamais d’actions répétitives.")
    return RefugeExplorerZoneSnapshot(
        zone_id="construction",
        title="Le Chantier",
        emoji="🏗️",
        details=tuple(details),
    )


def _build_monuments_zone() -> RefugeExplorerZoneSnapshot:
    return RefugeExplorerZoneSnapshot(
        zone_id="monuments",
        title="Les Monuments",
        emoji="🗿",
        details=(
            "Aucun monument communautaire n’est encore inscrit dans le Refuge.",
            "Cette section accueillera uniquement des constructions permanentes réellement obtenues par la communauté.",
        ),
    )


def _secret_events(state: RefugeWorldState) -> tuple[RefugeHistoricalEvent, ...]:
    events = [
        event
        for event in state.events
        if str(event.event_type).endswith("secret_discovered")
    ]
    events.sort(
        key=lambda event: (
            _parse_timestamp(event.occurred_at)
            or datetime.min.replace(tzinfo=timezone.utc),
            event.event_id,
        ),
        reverse=True,
    )
    return tuple(events)


def _build_mysteries_zone(state: RefugeWorldState) -> RefugeExplorerZoneSnapshot:
    discovered = _secret_events(state)
    if not discovered:
        details = (
            "Aucun mystère n’a encore été révélé.",
            "Seules les découvertes déjà réalisées apparaissent ici : aucune condition cachée n’est dévoilée.",
        )
        history: tuple[str, ...] = ()
    else:
        details = (
            f"Mystères révélés : {_format_number(len(discovered))}.",
            "Seules les découvertes déjà réalisées sont visibles ici.",
        )
        history = tuple(
            f"{_format_date(event.occurred_at)} · {event_label(event) or 'Mystère découvert'}"
            for event in discovered
        )
    return RefugeExplorerZoneSnapshot(
        zone_id="mysteries",
        title="Les Mystères",
        emoji="🌌",
        details=details,
        history=history,
    )


def build_explorer_snapshot(
    *,
    panel: RefugePanelSnapshot,
    fire_config: RefugeFireConfig,
    hall_config: RefugeHallConfig,
    casino_config: RefugeCasinoConfig,
) -> RefugeExplorerSnapshot:
    zones = (
        _build_fire_zone(panel=panel, config=fire_config),
        _build_hall_zone(panel=panel, config=hall_config),
        _build_casino_zone(panel=panel, config=casino_config),
        _build_construction_zone(panel.state),
        _build_monuments_zone(),
        _build_mysteries_zone(panel.state),
    )
    if tuple(zone.zone_id for zone in zones) != EXPLORER_ZONE_ORDER:
        raise RuntimeError("Refuge explorer zone order is inconsistent")
    return RefugeExplorerSnapshot(zones=zones)


def _user_id(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _achievement_label(achievement_id: object) -> str:
    definition = ACHIEVEMENT_BY_ID.get(str(achievement_id))
    if definition is None:
        return str(achievement_id)
    return f"{definition.emoji} {definition.name}"


def _personal_history_traces(
    *,
    user_id: int,
    state: RefugeWorldState,
    limit: int = 6,
) -> tuple[RefugeFootprintTrace, ...]:
    traces: list[RefugeFootprintTrace] = []
    seen: set[tuple[str, str]] = set()

    def add(occurred_at: object, label: str) -> None:
        timestamp = str(occurred_at or "").strip()
        text = str(label).strip()
        if not timestamp or not text:
            return
        key = (timestamp, text)
        if key in seen:
            return
        seen.add(key)
        traces.append(RefugeFootprintTrace(occurred_at=timestamp, label=text))

    for event in state.events:
        if _user_id(event.data.get("user_id")) != int(user_id):
            continue
        add(event.occurred_at, event_label(event) or "Une trace personnelle a marqué le Refuge")

    hall = _building(state, "hall")
    if hall is not None:
        firsts = hall.state.get("historical_firsts", ())
        if isinstance(firsts, (list, tuple)):
            for item in firsts:
                if not isinstance(item, Mapping):
                    continue
                if _user_id(item.get("user_id")) != int(user_id):
                    continue
                add(
                    item.get("unlocked_at"),
                    f"Première historique au Hall · {_achievement_label(item.get('achievement_id'))}",
                )

        markers = hall.state.get("gallery_markers", ())
        if isinstance(markers, (list, tuple)):
            for item in markers:
                if not isinstance(item, Mapping):
                    continue
                if _user_id(item.get("user_id")) != int(user_id):
                    continue
                achievement_id = item.get("achievement_id")
                if achievement_id:
                    label = f"Trace inscrite au Hall · {_achievement_label(achievement_id)}"
                else:
                    label = "Trace personnelle inscrite dans la galerie du Hall"
                add(item.get("occurred_at"), label)

    traces.sort(
        key=lambda item: (
            _parse_timestamp(item.occurred_at)
            or datetime.min.replace(tzinfo=timezone.utc),
            item.label,
        ),
        reverse=True,
    )
    return tuple(traces[: max(0, int(limit))])


def build_footprint_snapshot(
    *,
    profile: MemberProfileSnapshot,
    state: RefugeWorldState,
) -> RefugeFootprintSnapshot:
    achievement_names = tuple(
        _achievement_label(achievement_id)
        for achievement_id in profile.achievement_ids
    )
    return RefugeFootprintSnapshot(
        user_id=profile.user_id,
        season_id=profile.season_id,
        season_label=season_label(profile.season_id),
        level=profile.level,
        xp=profile.xp,
        season_xp=profile.season_xp,
        season_messages=profile.season_messages,
        season_voice_seconds=profile.season_voice_seconds,
        season_casino_net=profile.season_casino_net,
        achievements_unlocked=profile.achievements_unlocked,
        achievements_total=profile.achievements_total,
        achievement_names=achievement_names,
        casino_bets=profile.casino_bets,
        casino_net=profile.casino_net,
        historical_traces=_personal_history_traces(
            user_id=profile.user_id,
            state=state,
        ),
    )


class RefugeExplorationService:
    """Read-only orchestration for Explorer and Mon empreinte."""

    def __init__(
        self,
        *,
        panel_service: RefugePanelService = refuge_panel_service,
        member_profile_service_: MemberProfileService = member_profile_service,
        world_store: RefugeWorldStore = refuge_world_store,
    ) -> None:
        self.panel_service = panel_service
        self.member_profile_service = member_profile_service_
        self.world_store = world_store

    async def get_explorer(
        self,
        *,
        at: datetime | None = None,
    ) -> RefugeExplorerSnapshot:
        now = _aware_utc(at)
        panel = await self.panel_service.evaluate(at=now)
        return build_explorer_snapshot(
            panel=panel,
            fire_config=RefugeFireConfig.from_env(),
            hall_config=RefugeHallConfig.from_env(),
            casino_config=RefugeCasinoConfig.from_env(),
        )

    async def get_footprint(
        self,
        user_id: int,
        *,
        at: datetime | None = None,
    ) -> RefugeFootprintSnapshot:
        now = _aware_utc(at)
        current_season = season_id_for(now)
        profile, state = await asyncio.gather(
            self.member_profile_service.get_snapshot(
                int(user_id),
                season_id=current_season,
            ),
            self.world_store.get_state(),
        )
        return build_footprint_snapshot(profile=profile, state=state)


refuge_exploration_service = RefugeExplorationService()


__all__ = [
    "EXPLORER_ZONE_ORDER",
    "RefugeExplorerSnapshot",
    "RefugeExplorerZoneSnapshot",
    "RefugeExplorationService",
    "RefugeFootprintSnapshot",
    "RefugeFootprintTrace",
    "build_explorer_snapshot",
    "build_footprint_snapshot",
    "refuge_exploration_service",
]
