from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from config import DATA_DIR
from rendering.casino_royal import (
    CASINO_ROYAL_RENDERER_VERSION,
    CasinoRoyalRenderer,
    CasinoVisualState,
    build_casino_visual_state,
    casino_royal_renderer,
)
from services.refuge_casino import RefugeCasinoStatus


CASINO_VISUAL_CACHE_DIR: Final[Path] = Path(DATA_DIR) / "casino_visuals"
CASINO_VISUAL_CACHE_MAX_FILES: Final[int] = 72


@dataclass(frozen=True, slots=True)
class CasinoVisualAsset:
    path: Path
    state: CasinoVisualState
    cache_hit: bool

    @property
    def signature(self) -> str:
        return self.state.cache_key


class CasinoVisualCache:
    """Persistent low-churn cache for deterministic Casino hero renders."""

    def __init__(
        self,
        cache_dir: str | Path = CASINO_VISUAL_CACHE_DIR,
        *,
        renderer: CasinoRoyalRenderer = casino_royal_renderer,
        max_files: int = CASINO_VISUAL_CACHE_MAX_FILES,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.renderer = renderer
        self.max_files = max(1, int(max_files))
        self._lock = asyncio.Lock()

    def _path_for(self, state: CasinoVisualState) -> Path:
        return self.cache_dir / (
            f"casino_royal_v{CASINO_ROYAL_RENDERER_VERSION}_{state.cache_key}.png"
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
        path: Path,
    ) -> None:
        payload = self.renderer.render_png(status, state)
        self._write_atomic(path, payload)
        self._prune(path)

    async def get_or_render(
        self,
        status: RefugeCasinoStatus,
        *,
        at: datetime | None = None,
        phase_override: str | None = None,
        fortune_override: str | None = None,
        open_override: bool | None = None,
    ) -> CasinoVisualAsset:
        state = build_casino_visual_state(
            status,
            at=at,
            phase_override=phase_override,
            fortune_override=fortune_override,
            open_override=open_override,
        )
        path = self._path_for(state)
        if await asyncio.to_thread(self._is_cached, path):
            return CasinoVisualAsset(path=path, state=state, cache_hit=True)

        async with self._lock:
            if await asyncio.to_thread(self._is_cached, path):
                return CasinoVisualAsset(path=path, state=state, cache_hit=True)
            await asyncio.to_thread(self._render_and_store, status, state, path)
            return CasinoVisualAsset(path=path, state=state, cache_hit=False)


casino_visual_cache = CasinoVisualCache()


__all__ = [
    "CASINO_VISUAL_CACHE_DIR",
    "CASINO_VISUAL_CACHE_MAX_FILES",
    "CasinoVisualAsset",
    "CasinoVisualCache",
    "casino_visual_cache",
]
