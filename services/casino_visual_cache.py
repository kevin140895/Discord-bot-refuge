from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from config import DATA_DIR
from rendering.casino_legends import (
    CASINO_LEGEND_RENDERER_VERSION,
    apply_casino_legend_overlay,
)
from rendering.casino_reactions import (
    CASINO_REACTION_RENDERER_VERSION,
    apply_casino_reaction_overlay,
)
from rendering.casino_royal import (
    CASINO_ROYAL_RENDERER_VERSION,
    CasinoRoyalRenderer,
    CasinoVisualState,
    build_casino_visual_state,
    casino_royal_renderer,
)
from services.casino_legends import (
    CasinoLegendState,
    casino_legend_state_from_status,
)
from services.casino_reactions import (
    CasinoReactionService,
    CasinoReactionState,
    NORMAL_CASINO_REACTION,
    casino_reaction_override,
    casino_reaction_service,
)
from services.refuge_casino import RefugeCasinoStatus


logger = logging.getLogger(__name__)
CASINO_VISUAL_CACHE_DIR: Final[Path] = Path(DATA_DIR) / "casino_visuals"
CASINO_VISUAL_CACHE_MAX_FILES: Final[int] = 96


@dataclass(frozen=True, slots=True)
class CasinoVisualAsset:
    path: Path
    state: CasinoVisualState
    reaction: CasinoReactionState
    legends: CasinoLegendState
    cache_hit: bool

    @property
    def signature(self) -> str:
        raw = (
            f"v{CASINO_ROYAL_RENDERER_VERSION}:"
            f"r{CASINO_REACTION_RENDERER_VERSION}:"
            f"l{CASINO_LEGEND_RENDERER_VERSION}:"
            f"{self.state.cache_key}:{self.reaction.cache_key}:{self.legends.cache_key}"
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    async def read_bytes(self) -> bytes:
        """Read the cached PNG without blocking the Discord event loop."""

        return await asyncio.to_thread(self.path.read_bytes)


class CasinoVisualCache:
    """Persistent low-churn cache for deterministic Casino hero renders."""

    def __init__(
        self,
        cache_dir: str | Path = CASINO_VISUAL_CACHE_DIR,
        *,
        renderer: CasinoRoyalRenderer = casino_royal_renderer,
        reaction_service: CasinoReactionService = casino_reaction_service,
        max_files: int = CASINO_VISUAL_CACHE_MAX_FILES,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.renderer = renderer
        self.reaction_service = reaction_service
        self.max_files = max(1, int(max_files))
        self._lock = asyncio.Lock()

    def _path_for(
        self,
        state: CasinoVisualState,
        reaction: CasinoReactionState,
        legends: CasinoLegendState,
    ) -> Path:
        return self.cache_dir / (
            f"casino_royal_v{CASINO_ROYAL_RENDERER_VERSION}_"
            f"r{CASINO_REACTION_RENDERER_VERSION}_"
            f"l{CASINO_LEGEND_RENDERER_VERSION}_"
            f"{state.cache_key}_{reaction.cache_key}_{legends.cache_key}.png"
        )

    def _is_cached(self, path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _write_atomic(self, path: Path, payload: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _prune(self, keep: Path) -> None:
        try:
            files = [
                path
                for path in self.cache_dir.glob("casino_royal_v*.png")
                if path.is_file()
            ]
            files.sort(
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return

        survivors = {keep}
        for path in files:
            if len(survivors) < self.max_files:
                survivors.add(path)
                continue
            if path in survivors:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    def _render_and_store(
        self,
        status: RefugeCasinoStatus,
        state: CasinoVisualState,
        reaction: CasinoReactionState,
        legends: CasinoLegendState,
        path: Path,
    ) -> None:
        payload = self.renderer.render_png(status, state)
        payload = apply_casino_reaction_overlay(payload, reaction)
        payload = apply_casino_legend_overlay(payload, legends)
        self._write_atomic(path, payload)
        self._prune(path)

    async def _resolve_reaction(
        self,
        *,
        state: CasinoVisualState,
        at: datetime | None,
        reaction_override: str | None,
    ) -> CasinoReactionState:
        if not state.is_open:
            return NORMAL_CASINO_REACTION
        if reaction_override is not None:
            return casino_reaction_override(reaction_override)
        try:
            return await self.reaction_service.evaluate(at=at)
        except Exception:
            logger.exception("[CasinoVisual] réaction Lot 4 indisponible, fallback calme")
            return NORMAL_CASINO_REACTION

    async def get_or_render(
        self,
        status: RefugeCasinoStatus,
        *,
        at: datetime | None = None,
        phase_override: str | None = None,
        fortune_override: str | None = None,
        open_override: bool | None = None,
        reaction_override: str | None = None,
        legend_override: str | None = None,
    ) -> CasinoVisualAsset:
        state = build_casino_visual_state(
            status,
            at=at,
            phase_override=phase_override,
            fortune_override=fortune_override,
            open_override=open_override,
        )
        reaction = await self._resolve_reaction(
            state=state,
            at=at,
            reaction_override=reaction_override,
        )
        legends = casino_legend_state_from_status(
            status,
            marker_override=legend_override,
        )
        path = self._path_for(state, reaction, legends)
        if await asyncio.to_thread(self._is_cached, path):
            return CasinoVisualAsset(
                path=path,
                state=state,
                reaction=reaction,
                legends=legends,
                cache_hit=True,
            )

        async with self._lock:
            if await asyncio.to_thread(self._is_cached, path):
                return CasinoVisualAsset(
                    path=path,
                    state=state,
                    reaction=reaction,
                    legends=legends,
                    cache_hit=True,
                )
            await asyncio.to_thread(
                self._render_and_store,
                status,
                state,
                reaction,
                legends,
                path,
            )
            return CasinoVisualAsset(
                path=path,
                state=state,
                reaction=reaction,
                legends=legends,
                cache_hit=False,
            )


casino_visual_cache = CasinoVisualCache()


__all__ = [
    "CASINO_VISUAL_CACHE_DIR",
    "CASINO_VISUAL_CACHE_MAX_FILES",
    "CasinoVisualAsset",
    "CasinoVisualCache",
    "casino_visual_cache",
]
