"""Configuration sécurisée de l'authentification yt-dlp pour Railway.

Le bot peut recevoir un fichier cookies Netscape/Mozilla soit via un chemin déjà
monté (``YOUTUBE_COOKIES_FILE``), soit via un secret base64
(``YOUTUBE_COOKIES_B64``). Le secret base64 est matérialisé dans le répertoire
temporaire avec des permissions 0600 et son contenu n'est jamais journalisé.

La configuration est appliquée au ``YoutubeDL`` global afin que tous les appels
(recherche, validation d'un candidat et résolution du flux) partagent la même
authentification sans dupliquer la logique dans les cogs.

Un petit cache TTL en mémoire est également appliqué aux extractions sans
téléchargement. Les recherches ``ytsearch`` peuvent être réutilisées pendant une
heure. Les extractions d'URL directes ne sont conservées que cinq minutes, car
les résultats yt-dlp contiennent souvent des URL média signées et éphémères.
Le cache est borné et synchronisé entre threads afin d'éviter les extractions
redondantes pour une même clé sans bloquer l'event loop Discord.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yt_dlp

logger = logging.getLogger(__name__)

YOUTUBE_COOKIES_B64_ENV = "YOUTUBE_COOKIES_B64"
YOUTUBE_COOKIES_FILE_ENV = "YOUTUBE_COOKIES_FILE"
YOUTUBE_USER_AGENT_ENV = "YOUTUBE_USER_AGENT"
COOKIE_FILENAME = "refuge-youtube-cookies.txt"
_VALID_COOKIE_HEADERS = {
    "# Netscape HTTP Cookie File",
    "# HTTP Cookie File",
}

YTDLP_SEARCH_CACHE_TTL_SECONDS = 3600.0
YTDLP_DIRECT_CACHE_TTL_SECONDS = 300.0
YTDLP_CACHE_MAX_ENTRIES = 128
_YTDLP_CACHE_STRIPES = 32
_YTDLP_CACHE_OPTION_KEYS = (
    "format",
    "noplaylist",
    "extract_flat",
    "ignoreerrors",
    "skip_download",
    "cookiefile",
    "http_headers",
    "extractor_args",
    "playliststart",
    "playlistend",
    "playlist_items",
)
_CACHE_MISS = object()


@dataclass(frozen=True, slots=True)
class YTDLPAuthConfig:
    cookiefile: str | None = None
    user_agent: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class _YTDLPCacheEntry:
    value: Any
    expires_at: float


_ACTIVE_CONFIG = YTDLPAuthConfig()
_ORIGINAL_YOUTUBE_DL = yt_dlp.YoutubeDL
_YTDLP_CACHE: OrderedDict[tuple[Any, ...], _YTDLPCacheEntry] = OrderedDict()
_YTDLP_CACHE_LOCK = threading.Lock()
_YTDLP_KEY_LOCKS = tuple(threading.Lock() for _ in range(_YTDLP_CACHE_STRIPES))


def _normalise_cookie_text(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("cookies YouTube non UTF-8") from exc

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if not lines or lines[0].strip() not in _VALID_COOKIE_HEADERS:
        raise ValueError("format cookies YouTube invalide (Netscape attendu)")
    return text.rstrip("\n") + "\n"


def _validate_cookie_file(path: Path) -> Path:
    if not path.is_file():
        raise ValueError("fichier cookies YouTube introuvable")
    _normalise_cookie_text(path.read_bytes())
    return path


def _materialise_base64_cookiefile(value: str, *, temp_dir: str | None = None) -> Path:
    compact = "".join(value.split())
    if not compact:
        raise ValueError("secret cookies YouTube vide")
    try:
        raw = base64.b64decode(compact, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("secret cookies YouTube base64 invalide") from exc

    text = _normalise_cookie_text(raw)
    directory = Path(temp_dir or tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / COOKIE_FILENAME

    fd, temporary = tempfile.mkstemp(
        prefix=f".{COOKIE_FILENAME}.",
        dir=str(directory),
        text=True,
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise

    return target


def load_ytdlp_auth_config(
    environ: Mapping[str, str] | None = None,
    *,
    temp_dir: str | None = None,
) -> YTDLPAuthConfig:
    """Charge les secrets yt-dlp sans jamais exposer leur contenu dans les logs."""

    env = os.environ if environ is None else environ
    user_agent = str(env.get(YOUTUBE_USER_AGENT_ENV, "") or "").strip() or None

    explicit_path = str(env.get(YOUTUBE_COOKIES_FILE_ENV, "") or "").strip()
    if explicit_path:
        try:
            path = _validate_cookie_file(Path(explicit_path).expanduser())
            return YTDLPAuthConfig(str(path), user_agent, "file")
        except (OSError, ValueError) as exc:
            logger.warning("[ytdlp] YOUTUBE_COOKIES_FILE inutilisable: %s", exc)

    encoded = str(env.get(YOUTUBE_COOKIES_B64_ENV, "") or "").strip()
    if encoded:
        try:
            path = _materialise_base64_cookiefile(encoded, temp_dir=temp_dir)
            return YTDLPAuthConfig(str(path), user_agent, "railway_secret")
        except (OSError, ValueError) as exc:
            logger.warning("[ytdlp] YOUTUBE_COOKIES_B64 inutilisable: %s", exc)

    return YTDLPAuthConfig(None, user_agent, None)


def augment_ytdlp_options(options: Mapping[str, object] | None) -> dict[str, object]:
    """Injecte les paramètres d'authentification sans écraser un choix explicite."""

    merged: dict[str, object] = dict(options or {})
    config = _ACTIVE_CONFIG

    if config.cookiefile and not merged.get("cookiefile"):
        merged["cookiefile"] = config.cookiefile

    if config.user_agent:
        existing_headers = merged.get("http_headers")
        headers = dict(existing_headers) if isinstance(existing_headers, Mapping) else {}
        headers.setdefault("User-Agent", config.user_agent)
        merged["http_headers"] = headers

    return merged


def _freeze_cache_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _freeze_cache_value(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(repr(_freeze_cache_value(item)) for item in value))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _cache_ttl_for_extract(url: object, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> float:
    """Retourne le TTL uniquement pour les appels simples ``download=False``."""

    if len(args) > 1:
        return 0.0
    if any(key != "download" for key in kwargs):
        return 0.0

    download = args[0] if args else kwargs.get("download", True)
    if download is not False:
        return 0.0

    target = str(url).strip()
    lowered = target.lower()
    if lowered.startswith("ytsearch"):
        return YTDLP_SEARCH_CACHE_TTL_SECONDS
    if lowered.startswith(("http://", "https://")):
        return YTDLP_DIRECT_CACHE_TTL_SECONDS
    return 0.0


def _build_cache_key(url: object, params: Mapping[str, Any]) -> tuple[Any, ...]:
    option_fingerprint = tuple(
        (key, _freeze_cache_value(params.get(key)))
        for key in _YTDLP_CACHE_OPTION_KEYS
        if key in params
    )
    return str(url).strip(), option_fingerprint


def _cache_log_id(cache_key: tuple[Any, ...]) -> str:
    digest = hashlib.sha256(repr(cache_key).encode("utf-8", errors="replace")).hexdigest()
    return digest[:10]


def _cache_get(cache_key: tuple[Any, ...], *, now: float | None = None) -> Any:
    current = time.monotonic() if now is None else now
    with _YTDLP_CACHE_LOCK:
        entry = _YTDLP_CACHE.get(cache_key)
        if entry is None:
            return _CACHE_MISS
        if entry.expires_at <= current:
            _YTDLP_CACHE.pop(cache_key, None)
            return _CACHE_MISS
        _YTDLP_CACHE.move_to_end(cache_key)
        return entry.value


def _cache_set(cache_key: tuple[Any, ...], value: Any, ttl: float) -> None:
    expires_at = time.monotonic() + ttl
    with _YTDLP_CACHE_LOCK:
        _YTDLP_CACHE[cache_key] = _YTDLPCacheEntry(value=value, expires_at=expires_at)
        _YTDLP_CACHE.move_to_end(cache_key)
        while len(_YTDLP_CACHE) > YTDLP_CACHE_MAX_ENTRIES:
            _YTDLP_CACHE.popitem(last=False)


def clear_ytdlp_cache() -> None:
    """Vide le cache yt-dlp du processus courant."""

    with _YTDLP_CACHE_LOCK:
        _YTDLP_CACHE.clear()


class RefugeYoutubeDL(_ORIGINAL_YOUTUBE_DL):
    """YoutubeDL qui applique l'auth Railway et un cache borné aux extractions."""

    def __init__(self, params=None, auto_init=True):
        super().__init__(augment_ytdlp_options(params), auto_init=auto_init)

    def extract_info(self, url, *args, **kwargs):
        ttl = _cache_ttl_for_extract(url, args, kwargs)
        if ttl <= 0:
            return super().extract_info(url, *args, **kwargs)

        cache_key = _build_cache_key(url, self.params)
        cached = _cache_get(cache_key)
        kind = "search" if str(url).lower().startswith("ytsearch") else "direct"
        cache_id = _cache_log_id(cache_key)
        if cached is not _CACHE_MISS:
            logger.info("[ytdlp] cache hit kind=%s key=%s", kind, cache_id)
            return cached

        key_lock = _YTDLP_KEY_LOCKS[hash(cache_key) % len(_YTDLP_KEY_LOCKS)]
        with key_lock:
            cached = _cache_get(cache_key)
            if cached is not _CACHE_MISS:
                logger.info("[ytdlp] cache hit kind=%s key=%s", kind, cache_id)
                return cached

            logger.info("[ytdlp] cache miss kind=%s key=%s", kind, cache_id)
            info = super().extract_info(url, *args, **kwargs)
            if info is not None:
                _cache_set(cache_key, info, ttl)
            return info


def configure_ytdlp_auth(
    environ: Mapping[str, str] | None = None,
    *,
    temp_dir: str | None = None,
) -> YTDLPAuthConfig:
    """Active la configuration yt-dlp pour le processus courant, de façon idempotente."""

    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = load_ytdlp_auth_config(environ, temp_dir=temp_dir)
    clear_ytdlp_cache()
    yt_dlp.YoutubeDL = RefugeYoutubeDL

    if _ACTIVE_CONFIG.cookiefile:
        logger.info(
            "[ytdlp] authentification YouTube activée source=%s user_agent=%s",
            _ACTIVE_CONFIG.source,
            bool(_ACTIVE_CONFIG.user_agent),
        )
    elif _ACTIVE_CONFIG.user_agent:
        logger.info("[ytdlp] User-Agent YouTube configuré sans cookies")
    else:
        logger.info("[ytdlp] extraction YouTube anonyme (aucun cookie configuré)")

    logger.info(
        "[ytdlp] cache actif search_ttl=%ss direct_ttl=%ss max_entries=%d",
        int(YTDLP_SEARCH_CACHE_TTL_SECONDS),
        int(YTDLP_DIRECT_CACHE_TTL_SECONDS),
        YTDLP_CACHE_MAX_ENTRIES,
    )
    return _ACTIVE_CONFIG


__all__ = [
    "YTDLPAuthConfig",
    "YTDLP_CACHE_MAX_ENTRIES",
    "YTDLP_DIRECT_CACHE_TTL_SECONDS",
    "YTDLP_SEARCH_CACHE_TTL_SECONDS",
    "augment_ytdlp_options",
    "clear_ytdlp_cache",
    "configure_ytdlp_auth",
    "load_ytdlp_auth_config",
]
