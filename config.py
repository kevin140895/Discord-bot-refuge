"""Configuration des IDs spécifiques au serveur Discord.
Modifiez les valeurs ci-dessous pour adapter le bot à votre serveur."""

import os
import time


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

# ── Informations globales ──────────────────────────────────────
GUILD_ID: int = int(os.getenv("GUILD_ID", "0"))

TZ: str = os.getenv("TZ", "Europe/Paris")
os.environ["TZ"] = TZ
try:
    time.tzset()
except AttributeError:
    # ``tzset`` n'existe pas sur toutes les plateformes (ex: Windows)
    pass

# ── Salons statistiques ───────────────────────────────────────
STATS_MEMBERS_CHANNEL_ID = 1406435185813098537
STATS_ONLINE_CHANNEL_ID = 1413712632711745648
STATS_VOICE_CHANNEL_ID = 1406435190607184085

# ── Rôles plateformes et notifications ───────────────────────
ROLE_PC = 1400560541529018408
ROLE_CONSOLE = 1400560660710162492
ROLE_MOBILE = 1404791652085928008
ROLE_NOTIFICATION = 1404882154370109450
VIP_24H_ROLE_ID: int = int(os.getenv("VIP_24H_ROLE_ID", "0"))

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
RADIO_TEXT_CHANNEL_ID = 1409333722754580571
RADIO_STREAM_URL = os.getenv(
    "RADIO_STREAM_URL",
    "https://stream.laut.fm/hiphop-forever",
)
RADIO_RAP_STREAM_URL = "https://stream.laut.fm/englishrap"

ROCK_RADIO_VC_ID = 1408081503707074650
ROCK_RADIO_STREAM_URL = os.getenv(
    "ROCK_RADIO_STREAM_URL",
    "https://stream.laut.fm/rockworld",
)

# ── Divers ───────────────────────────────────────────────────
XP_VIEWER_ROLE_ID = 1403510368340410550
TOP_MSG_ROLE_ID = 1406412171965104208
TOP_VC_ROLE_ID = 1406412383878119485
MVP_ROLE_ID = 1406412507433795595
ANNOUNCE_CHANNEL_ID: int = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))
"""Salon utilisé pour les annonces de la machine à sous."""

AWARD_ANNOUNCE_CHANNEL_ID: int = int(
    os.getenv("AWARD_ANNOUNCE_CHANNEL_ID", str(ANNOUNCE_CHANNEL_ID))
)
WRITER_ROLE_ID = TOP_MSG_ROLE_ID
VOICE_ROLE_ID = TOP_VC_ROLE_ID
ENABLE_DAILY_AWARDS: bool = os.getenv("ENABLE_DAILY_AWARDS", "1") != "0"

LEVEL_UP_CHANNEL = 1402419913716531352
LEVEL_FEED_CHANNEL_ID: int = int(
    os.getenv("LEVEL_FEED_CHANNEL_ID", str(LEVEL_UP_CHANNEL))
)
ENABLE_GAME_LEVEL_FEED: bool = os.getenv("ENABLE_GAME_LEVEL_FEED", "1") != "0"
CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472
TIKTOK_ANNOUNCE_CH: int = int(
    os.getenv("TIKTOK_ANNOUNCE_CH", str(ANNOUNCE_CHANNEL_ID))
)
ACTIVITY_SUMMARY_CH: int = int(
    os.getenv("ACTIVITY_SUMMARY_CH", str(ANNOUNCE_CHANNEL_ID))
)
UPDATE_CHANNEL_ID = 1400550888246083585
MACHINE_A_SOUS_CHANNEL_ID = 1405170020748755034
MACHINE_A_SOUS_XP_CHANNEL_ID = MACHINE_A_SOUS_CHANNEL_ID

# ── Rappels de rôles et notifications ────────────────────────
REMINDER_CHANNEL_ID: int = int(
    os.getenv("REMINDER_CHANNEL_ID", str(ANNOUNCE_CHANNEL_ID))
)
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

# ── Machine à sous ──────────────────────────────────────────
MACHINE_A_SOUS_ROLE_ID: int = 1405170057792979025
"""Rôle attribué au gagnant de la machine à sous."""

MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES: int = int(
    os.getenv("MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES", "1")
)
"""Intervalle en minutes entre deux vérifications de l'état de la machine à sous."""

# ── Pari XP ─────────────────────────────────────────────────
PARI_XP_CHANNEL_ID: int = 1408834276228730900
"""Salon dédié à la roulette XP."""

PARI_XP_ROLE_ID: int = int(os.getenv("PARI_XP_ROLE_ID", "0"))
"""Rôle optionnel attribué au dernier gagnant de la roulette XP."""

# ── Persistance et I/O ───────────────────────────────────────
DATA_DIR: str = _resolve_data_dir()
"""Répertoire de stockage persistant."""

# ── Double XP vocal ───────────────────────────────────────────
"""Les sessions Double XP vocal ne sont plus générées automatiquement."""

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
