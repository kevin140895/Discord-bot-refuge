"""Configuration sécurisée de l'authentification yt-dlp pour Railway.

Le bot peut recevoir un fichier cookies Netscape/Mozilla soit via un chemin déjà
monté (``YOUTUBE_COOKIES_FILE``), soit via un secret base64
(``YOUTUBE_COOKIES_B64``). Le secret base64 est matérialisé dans le répertoire
temporaire avec des permissions 0600 et son contenu n'est jamais journalisé.

Pour les flux YouTube soumis aux Proof-of-Origin tokens, le bot peut aussi être
relié à un serveur HTTP bgutil séparé via ``YOUTUBE_POT_PROVIDER_URL``. Le
plugin Python reste dans l'image du bot, tandis que le générateur JavaScript est
isolé dans un autre service Railway. Lorsque ce provider est configuré, yt-dlp
utilise par défaut le client ``mweb`` recommandé pour les requêtes GVS.

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
from urllib.parse import urlsplit

import yt_dlp

logger = logging.getLogger(__name__)

YOUTUBE_COOKIES_B64_ENV = "YOUTUBE_COOKIES_B64"
YOUTUBE_COOKIES_FILE_ENV = "YOUTUBE_COOKIES_FILE"
YOUTUBE_USER_AGENT_ENV = "YOUTUBE_USER_AGENT"
YOUTUBE_POT_PROVIDER_URL_ENV = "YOUTUBE_POT_PROVIDER_URL"
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
    pot_provider_url: str | None = None


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


def _normalise_pot_provider_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise ValueError("URL provider PO Token vide")

    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL provider PO Token invalide (http/https attendu)")
    if parsed.username or parsed.password:
        raise ValueError("URL provider PO Token avec identifiants interdite")
    if parsed.query or parsed.fragment:
        raise ValueError("URL provider PO Token avec query/fragment interdite")
    if parsed.path not in {"", "/"}:
        raise ValueError("URL provider PO Token doit viser la racine du service")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("port provider PO Token invalide") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("port provider PO Token invalide")
    return raw


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
    """Charge les secrets/options yt-dlp sans exposer leur contenu dans les logs."""

    env = os.environ if environ is None else environ
    user_agent = str(env.get(YOUTUBE_USER_AGENT_ENV, "") or "").strip() or None

    pot_provider_url = None
    raw_provider_url = str(env.get(YOUTUBE_POT_PROVIDER_URL_ENV, "") or "").strip()
    if raw_provider_url:
        try:
            pot_provider_url = _normalise_pot_provider_url(raw_provider_url)
        except ValueError as exc:
            logger.warning("[ytdlp] YOUTUBE_POT_PROVIDER_URL inutilisable: %s", exc)

    explicit_path = str(env.get(YOUTUBE_COOKIES_FILE_ENV, "") or "").strip()
    if explicit_path:
        try:
            path = _validate_cookie_file(Path(explicit_path).expanduser())
            return YTDLPAuthConfig(
                str(path), user_agent, "file", pot_provider_url
            )
        except (OSError, ValueError) as exc:
            logger.warning("[ytdlp] YOUTUBE_COOKIES_FILE inutilisable: %s", exc)

    encoded = str(env.get(YOUTUBE_COOKIES_B64_ENV, "") or "").strip()
    if encoded:
        try:
            path = _materialise_base64_cookiefile(encoded, temp_dir=temp_dir)
            return YTDLPAuthConfig(
                str(path), user_agent, "railway_secret", pot_provider_url
            )
        except (OSError, ValueError) as exc:
            logger.warning("[ytdlp] YOUTUBE_COOKIES_B64 inutilisable: %s", exc)

    return YTDLPAuthConfig(None, user_agent, None, pot_provider_url)


def _copy_extractor_args(value: object) -> dict[str, object] | None:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        return None

    copied: dict[str, object] = {}
    for key, raw in value.items():
        copied[str(key)] = dict(raw) if isinstance(raw, Mapping) else raw
    return copied


def augment_ytdlp_options(options: Mapping[str, object] | None) -> dict[str, object]:
    """Injecte l'auth et le provider PO Token sans écraser un choix explicite."""

    merged: dict[str, object] = dict(options or {})
    config = _ACTIVE_CONFIG

    if config.cookiefile and not merged.get("cookiefile"):
        merged["cookiefile"] = config.cookiefile

    if config.user_agent:
        existing_headers = merged.get("http_headers")
        headers = dict(existing_headers) if isinstance(existing_headers, Mapping) else {}
        headers.setdefault("User-Agent", config.user_agent)
        merged["http_headers"] = headers

    if config.pot_provider_url:
        extractor_args = _copy_extractor_args(merged.get("extractor_args"))
        if extractor_args is None:
            logger.warning(
                "[ytdlp] extractor_args explicite non-mapping; injection PO Token ignorée"
            )
            return merged

        youtube_raw = extractor_args.get("youtube")
        youtube_args = dict(youtube_raw) if isinstance(youtube_raw, Mapping) else {}
        youtube_args.setdefault("player_client", ["mweb"])
        extractor_args["youtube"] = youtube_args

        provider_key = "youtubepot-bgutilhttp"
        provider_raw = extractor_args.get(provider_key)
        provider_args = (
            dict(provider_raw) if isinstance(provider_raw, Mapping) else {}
        )
        provider_args.setdefault("base_url", [config.pot_provider_url])
        extractor_args[provider_key] = provider_args
        merged["extractor_args"] = extractor_args

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

    if _ACTIVE_CONFIG.pot_provider_url:
        logger.info("[ytdlp] PO Token provider activé client=mweb transport=http")
    else:
        logger.info("[ytdlp] PO Token provider externe non configuré")

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
    "YOUTUBE_POT_PROVIDER_URL_ENV",
    "augment_ytdlp_options",
    "clear_ytdlp_metadata_cache",
    "configure_ytdlp_auth",
    "get_ytdlp_metadata_cache",
    "load_ytdlp_auth_config",
    "make_ytdlp_metadata_cache_key",
    "set_ytdlp_metadata_cache",
]
