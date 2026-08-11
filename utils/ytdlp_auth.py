"""Configuration sécurisée de l'authentification yt-dlp pour Railway.

Le bot peut recevoir un fichier cookies Netscape/Mozilla soit via un chemin déjà
monté (``YOUTUBE_COOKIES_FILE``), soit via un secret base64
(``YOUTUBE_COOKIES_B64``). Le secret base64 est matérialisé dans le répertoire
temporaire avec des permissions 0600 et son contenu n'est jamais journalisé.

La configuration est appliquée au ``YoutubeDL`` global afin que tous les appels
(recherche, validation d'un candidat et résolution du flux) partagent la même
authentification sans dupliquer la logique dans les cogs.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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


@dataclass(frozen=True, slots=True)
class YTDLPAuthConfig:
    cookiefile: str | None = None
    user_agent: str | None = None
    source: str | None = None


_ACTIVE_CONFIG = YTDLPAuthConfig()
_ORIGINAL_YOUTUBE_DL = yt_dlp.YoutubeDL


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

    return _ACTIVE_CONFIG


__all__ = [
    "YTDLPAuthConfig",
    "augment_ytdlp_options",
    "configure_ytdlp_auth",
    "load_ytdlp_auth_config",
]
