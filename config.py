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
RADIO_TEXT_CHANNEL_ID = 1402258805533970472
RADIO_STREAM_URL = os.getenv(
    "RADIO_STREAM_URL",
    "https://n08.radiojar.com/2b5w4a2kb?rj-ttl=5&rj-tok=AAABmNHaVWAAm4GDT5xyXjsi5A",
)
RADIO_RAP_STREAM_URL = "https://stream.laut.fm/24-7-rap"
RADIO_RAP_FR_STREAM_URL = "http://icecast.radiofrance.fr/mouvrapfr-midfi.mp3"

ROCK_RADIO_VC_ID = 1408081503707074650
ROCK_RADIO_STREAM_URL = os.getenv(
    "ROCK_RADIO_STREAM_URL",
    "http://stream.radioparadise.com/rock-192",
)

# ── Divers ───────────────────────────────────────────────────
XP_VIEWER_ROLE_ID = 1403510368340410550
TOP_MSG_ROLE_ID = 1406412171965104208
TOP_VC_ROLE_ID = 1406412383878119485
MVP_ROLE_ID = 1406412507433795595
AWARD_ANNOUNCE_CHANNEL_ID = 1400552164979507263
WRITER_ROLE_ID = TOP_MSG_ROLE_ID
VOICE_ROLE_ID = TOP_VC_ROLE_ID
ENABLE_DAILY_AWARDS: bool = os.getenv("ENABLE_DAILY_AWARDS", "1") != "0"

LEVEL_UP_CHANNEL = 1402419913716531352
CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472
TIKTOK_ANNOUNCE_CH = 1400552164979507263
ACTIVITY_SUMMARY_CH = 1400552164979507263
ROULETTE_CHANNEL_ID = 1405170020748755034
ROULETTE_XP_CHANNEL_ID = 1408834276228730900

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

ROULETTE_BOUNDARY_CHECK_INTERVAL_MINUTES: int = int(
    os.getenv("ROULETTE_BOUNDARY_CHECK_INTERVAL_MINUTES", "5")
)
"""Intervalle en minutes entre deux vérifications de l'état de la roulette."""

# ── Persistance et I/O ───────────────────────────────────────
DATA_DIR: str = _resolve_data_dir()
"""Répertoire de stockage persistant."""

# ── Double XP vocal ───────────────────────────────────────────
XP_DOUBLE_VOICE_SESSIONS_PER_DAY: int = int(
    os.getenv("XP_DOUBLE_VOICE_SESSIONS_PER_DAY", "2")
)
"""Nombre maximum de sessions Double XP vocal par jour."""

XP_DOUBLE_VOICE_DURATION_MINUTES: int = int(
    os.getenv("XP_DOUBLE_VOICE_DURATION_MINUTES", "60")
)
"""Durée d'une session Double XP vocal en minutes."""

XP_DOUBLE_VOICE_START_HOUR: int = int(
    os.getenv("XP_DOUBLE_VOICE_START_HOUR", "10")
)
"""Heure de début minimale pour une session (Europe/Paris)."""

XP_DOUBLE_VOICE_END_HOUR: int = int(
    os.getenv("XP_DOUBLE_VOICE_END_HOUR", "23")
)
"""Heure de début maximale pour une session (Europe/Paris)."""

XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID: int = int(
    os.getenv("XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID", str(ANNOUNCE_CHANNEL_ID))
)
"""Salon où sont annoncées les sessions Double XP vocal."""

# ── Jeux organisés ────────────────────────────────────────────
GAMES_DATA_DIR: str = os.getenv("GAMES_DATA_DIR", "/app/data/games")
"""Répertoire de persistance des événements de jeu."""

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

# ── API Metering ──────────────────────────────────────────────
BOT_ALERTS_CHANNEL_ID: int = int(os.getenv("BOT_ALERTS_CHANNEL_ID", "0"))
API_BUDGET_PER_10MIN: int = int(os.getenv("API_BUDGET_PER_10MIN", "10000"))
API_SOFT_LIMIT_PCT: float = float(os.getenv("API_SOFT_LIMIT_PCT", "85"))
API_HARD_LIMIT_PCT: float = float(os.getenv("API_HARD_LIMIT_PCT", "95"))
API_SLOW_CALL_MS: int = int(os.getenv("API_SLOW_CALL_MS", "1000"))
API_REPORT_INTERVAL_MIN: int = int(os.getenv("API_REPORT_INTERVAL_MIN", "1"))
