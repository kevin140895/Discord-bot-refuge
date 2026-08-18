"""Configuration sécurisée de l'authentification yt-dlp pour Railway.

Le bot peut recevoir un fichier cookies Netscape/Mozilla soit via un chemin déjà
monté (``YOUTUBE_COOKIES_FILE``), soit via un secret base64
(``YOUTUBE_COOKIES_B64``). Le secret base64 est matérialisé dans le répertoire
temporaire avec des permissions 0600 et son contenu n'est jamais journalisé.

La configuration est appliquée au ``YoutubeDL`` global afin que tous les appels
(recherche, validation d'un candidat et résolution du flux) partagent la même
authentification sans dupliquer la logique dans les cogs.

Ce module expose aussi un petit cache TTL borné pour les métadonnées yt-dlp.
Seuls les champs stables utiles à l'interface (titre, page, durée, uploader...) y
sont conservés. Les URL média directes, formats et en-têtes HTTP ne sont jamais
mis en cache ici car ils peuvent être signés et expirer rapidement.
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

YTDLP_METADATA_CACHE_TTL_SECONDS = 3600.0
YTDLP_METADATA_CACHE_MAX_ENTRIES = 128
_YTDLP_METADATA_FIELDS = (
    "id",
    "title",
    "webpage_url",
    "original_url",
    "duration",
    "uploader",
    "channel",
    "extractor",
    "extractor_key",
)


@dataclass(frozen=True, slots=True)
class YTDLPAuthConfig:
    cookiefile: str | None = None
    user_agent: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class _YTDLPMetadataCacheEntry:
    value: dict[str, Any]
    expires_at: float


_ACTIVE_CONFIG = YTDLPAuthConfig()
_ORIGINAL_YOUTUBE_DL = yt_dlp.YoutubeDL
_YTDLP_METADATA_CACHE: OrderedDict[str, _YTDLPMetadataCacheEntry] = OrderedDict()
_YTDLP_METADATA_CACHE_LOCK = threading.Lock()


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


def make_ytdlp_metadata_cache_key(kind: str, target: str) -> str:
    """Construit une clé opaque et stable sans journaliser la requête utilisateur."""

    namespace = str(kind).strip().lower() or "metadata"
    raw_target = str(target).strip()
    if namespace == "search":
        raw_target = " ".join(raw_target.split()).casefold()
    digest = hashlib.sha256(raw_target.encode("utf-8", errors="replace")).hexdigest()
    return f"{namespace}:{digest}"


def _metadata_projection(
    info: Mapping[str, Any], *, fallback_url: str | None = None
) -> dict[str, Any] | None:
    projection = {
        field: info[field]
        for field in _YTDLP_METADATA_FIELDS
        if field in info and info[field] is not None
    }

    webpage_url = str(
        projection.get("webpage_url") or projection.get("original_url") or ""
    ).strip()
    if not webpage_url and fallback_url:
        fallback = str(fallback_url).strip()
        if fallback.startswith(("http://", "https://")):
            projection["webpage_url"] = fallback
            webpage_url = fallback

    if not webpage_url:
        return None
    if not projection.get("title"):
        projection["title"] = "Titre inconnu"
    return projection


def get_ytdlp_metadata_cache(cache_key: str) -> dict[str, Any] | None:
    """Retourne une copie des métadonnées encore valides, sinon ``None``."""

    now = time.monotonic()
    with _YTDLP_METADATA_CACHE_LOCK:
        entry = _YTDLP_METADATA_CACHE.get(cache_key)
        if entry is None:
            logger.info("[ytdlp] metadata cache miss key=%s", cache_key[:17])
            return None
        if entry.expires_at <= now:
            _YTDLP_METADATA_CACHE.pop(cache_key, None)
            logger.info("[ytdlp] metadata cache expired key=%s", cache_key[:17])
            return None
        _YTDLP_METADATA_CACHE.move_to_end(cache_key)
        logger.info("[ytdlp] metadata cache hit key=%s", cache_key[:17])
        return dict(entry.value)


def set_ytdlp_metadata_cache(
    cache_key: str,
    info: Mapping[str, Any],
    *,
    fallback_url: str | None = None,
) -> bool:
    """Stocke uniquement la projection stable d'un résultat yt-dlp."""

    projection = _metadata_projection(info, fallback_url=fallback_url)
    if projection is None:
        return False

    entry = _YTDLPMetadataCacheEntry(
        value=projection,
        expires_at=time.monotonic() + YTDLP_METADATA_CACHE_TTL_SECONDS,
    )
    with _YTDLP_METADATA_CACHE_LOCK:
        _YTDLP_METADATA_CACHE[cache_key] = entry
        _YTDLP_METADATA_CACHE.move_to_end(cache_key)
        while len(_YTDLP_METADATA_CACHE) > YTDLP_METADATA_CACHE_MAX_ENTRIES:
            _YTDLP_METADATA_CACHE.popitem(last=False)
    return True


def clear_ytdlp_metadata_cache() -> None:
    """Vide le cache de métadonnées du processus courant."""

    with _YTDLP_METADATA_CACHE_LOCK:
        _YTDLP_METADATA_CACHE.clear()


class RefugeYoutubeDL(_ORIGINAL_YOUTUBE_DL):
    """YoutubeDL qui applique automatiquement l'auth Railway du bot."""

    def __init__(self, params=None, auto_init=True):
        super().__init__(augment_ytdlp_options(params), auto_init=auto_init)


def configure_ytdlp_auth(
    environ: Mapping[str, str] | None = None,
    *,
    temp_dir: str | None = None,
) -> YTDLPAuthConfig:
    """Active la configuration yt-dlp pour le processus courant, de façon idempotente."""

    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = load_ytdlp_auth_config(environ, temp_dir=temp_dir)
    clear_ytdlp_metadata_cache()
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
        "[ytdlp] metadata cache actif ttl=%ss max_entries=%d",
        int(YTDLP_METADATA_CACHE_TTL_SECONDS),
        YTDLP_METADATA_CACHE_MAX_ENTRIES,
    )
    return _ACTIVE_CONFIG


__all__ = [
    "YTDLPAuthConfig",
    "YTDLP_METADATA_CACHE_MAX_ENTRIES",
    "YTDLP_METADATA_CACHE_TTL_SECONDS",
    "augment_ytdlp_options",
    "clear_ytdlp_metadata_cache",
    "configure_ytdlp_auth",
    "get_ytdlp_metadata_cache",
    "load_ytdlp_auth_config",
    "make_ytdlp_metadata_cache_key",
    "set_ytdlp_metadata_cache",
]
