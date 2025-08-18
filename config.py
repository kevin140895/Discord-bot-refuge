"""Configuration des IDs spécifiques au serveur Discord.
Modifiez les valeurs ci-dessous pour adapter le bot à votre serveur."""

import os


def _resolve_data_dir() -> str:
    """Resolve the directory used for persistent storage.

    Priority order:
    1. ``DATA_DIR`` environment variable
    2. ``/app/data`` (Railway default mount)
    3. ``/data`` legacy path
    """
    env = os.getenv("DATA_DIR")
    if env:
        return env
    if os.path.isdir("/app/data"):
        return "/app/data"
    return "/data"

# ── Salons statistiques ───────────────────────────────────────
STATS_CATEGORY_ID = 1406408038692294676  # Catégorie "📊 Statistiques"

# ── Rôles plateformes et notifications ───────────────────────
ROLE_PC = 1400560541529018408
ROLE_CONSOLE = 1400560660710162492
ROLE_MOBILE = 1404791652085928008
ROLE_NOTIFICATION = 1404882154370109450

# ── Récompenses par niveau ───────────────────────────────────
LEVEL_ROLE_REWARDS = {
    5: 1403510226354700430,  # Bronze
    10: 1403510368340410550,  # Argent
    20: 1403510466818605118,  # Or
}

# ── Salons temporaires & radio ───────────────────────────────
TEMP_VC_CATEGORY = 1400559884117999687
TEMP_VC_LIMITS = {TEMP_VC_CATEGORY: 5}
RENAME_DELAY = 3  # délai en secondes avant renommage des salons temporaires
TEMP_VC_CHECK_INTERVAL_SECONDS = 30  # fréquence de vérification des noms

LOBBY_VC_ID = 1405630965803520221
RADIO_VC_ID = 1405695147114758245
RADIO_MUTED_ROLE_ID = 1403510368340410550
RADIO_STREAM_URL = os.getenv(
    "RADIO_STREAM_URL", "http://stream.laut.fm/englishrap"
)

# ── Divers ───────────────────────────────────────────────────
XP_VIEWER_ROLE_ID = 1403510368340410550
TOP_MSG_ROLE_ID = 1406412171965104208
TOP_VC_ROLE_ID = 1406412383878119485
MVP_ROLE_ID = 1406412507433795595

LEVEL_UP_CHANNEL = 1402419913716531352
CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472
TIKTOK_ANNOUNCE_CH = 1400552164979507263
ACTIVITY_SUMMARY_CH = 1400552164979507263
ROULETTE_CHANNEL_ID = 1405170020748755034

# ── Rappels de rôles et notifications ────────────────────────
REMINDER_CHANNEL_ID: int = 1400552164979507263
"""Salon unique où sont envoyés les rappels de rôles."""

ROLE_CHOICE_CHANNEL_ID: int = 1400560866478395512
"""Salon contenant les boutons pour choisir ses rôles."""

IGNORED_ROLE_IDS: set[int] = {
    1402071696277635157,
    1404054439706234910,
    1403510368340410550,
    1405170057792979025,
    1402302249035894968,
}
"""Rôles à ignorer lors des rappels."""

# ── Roue de la fortune ──────────────────────────────────────
ROULETTE_ROLE_ID: int = 1405170057792979025
"""Rôle attribué au gagnant de la roulette."""

ANNOUNCE_CHANNEL_ID: int = 1400552164979507263
"""Salon utilisé pour les annonces de la roulette."""

# ── Persistance et I/O ───────────────────────────────────────
DATA_DIR: str = _resolve_data_dir()
"""Répertoire de stockage persistant."""

CHANNEL_EDIT_MIN_INTERVAL_SECONDS: int = int(
    os.getenv("CHANNEL_EDIT_MIN_INTERVAL_SECONDS", "180")
)
"""Intervalle minimal entre deux modifications du même salon."""

CHANNEL_EDIT_DEBOUNCE_SECONDS: int = int(
    os.getenv("CHANNEL_EDIT_DEBOUNCE_SECONDS", "15")
)
"""Délai appliqué avant l'édition d'un salon."""

CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS: int = int(
    os.getenv("CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS", "10")
)
"""Intervalle minimal global entre les éditions de salons."""

# ── Renommage des salons ────────────────────────────────────
CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL: int = int(
    os.getenv("CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL", "5")
)
"""Intervalle minimal entre deux renommages du même salon."""

CHANNEL_RENAME_MIN_INTERVAL_GLOBAL: int = int(
    os.getenv("CHANNEL_RENAME_MIN_INTERVAL_GLOBAL", "2")
)
"""Intervalle minimal global entre les renommages de salons."""

CHANNEL_RENAME_DEBOUNCE_SECONDS: int = int(
    os.getenv("CHANNEL_RENAME_DEBOUNCE_SECONDS", "2")
)
"""Délai appliqué avant le renommage d'un salon."""

CHANNEL_RENAME_MAX_RETRIES: int = int(
    os.getenv("CHANNEL_RENAME_MAX_RETRIES", "5")
)
"""Nombre maximum de tentatives de renommage en cas de 429."""

CHANNEL_RENAME_BACKOFF_BASE: float = float(
    os.getenv("CHANNEL_RENAME_BACKOFF_BASE", "2")
)
"""Base du délai exponentiel entre les tentatives de renommage."""

# ── Propriétaire du bot ──────────────────────────────────────
OWNER_ID: int = int(os.getenv("OWNER_ID", "541417878314942495"))
