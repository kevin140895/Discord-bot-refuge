"""Configuration des IDs spécifiques au serveur Discord.

Les variables d'environnement sont chargées et validées une seule fois via
:class:`settings.Settings`. Les constantes historiques restent exposées pour
préserver la compatibilité avec les cogs existants.
"""

from __future__ import annotations

import os
import time

from settings import ConfigError, Settings


SETTINGS = Settings.from_env()

# ── Channel trigger (création salons temporaires / views) ─────────────
TRIGGER_CHANNEL_ID: int = SETTINGS.trigger_channel_id


# ── Informations globales ──────────────────────────────────────
GUILD_ID: int = SETTINGS.guild_id

# Fonctionnalités conservées dans le dépôt mais retirées du serveur actuel.
# Le chargeur automatique ignore ces cogs afin qu'ils ne démarrent aucune
# tâche de fond ni appel API tant qu'ils ne sont pas explicitement réactivés.
DISABLED_COGS: frozenset[str] = frozenset({"f1_standings", "nhl_notifications"})

TZ: str = SETTINGS.tz
os.environ["TZ"] = TZ
try:
    time.tzset()
except AttributeError:
    # ``tzset`` n'existe pas sur toutes les plateformes (ex: Windows)
    pass

# ── Horaires du casino ────────────────────────────────────────
CASINO_OPEN_HOUR: int = SETTINGS.casino_open_hour
CASINO_CLOSE_HOUR: int = SETTINGS.casino_close_hour
CASINO_SCHEDULE_LABEL = f"{CASINO_OPEN_HOUR:02d}h00 - {CASINO_CLOSE_HOUR:02d}h00"


# ── Salons statistiques ───────────────────────────────────────
STATS_MEMBERS_CHANNEL_ID = 1406435185813098537
STATS_ONLINE_CHANNEL_ID = 1413712632711745648
STATS_VOICE_CHANNEL_ID = 1406435190607184085


# ── Salons de fonctionnalités ─────────────────────────────────
ECONOMY_CHANNEL_ID: int = SETTINGS.economy_channel_id
F1_CHANNEL_ID: int = SETTINGS.f1_channel_id


# ── Rôles plateformes et notifications ────────────────────────
ROLE_PC = 1400560541529018408
ROLE_CONSOLE = 1400560660710162492
ROLE_MOBILE = 1404791652085928008
ROLE_NOTIFICATION = 1404882154370109450
ROLE_ANTHYX_COMMUNITY = 1453882345177219092
ROLE_PARIS_SPORTIFS = 1458439939359510680
VIP_24H_ROLE_ID: int = SETTINGS.vip_24h_role_id


# ── Récompenses par niveau ────────────────────────────────────
LEVEL_ROLE_REWARDS = {
    5: 1403510226354700430,   # Bronze
    10: 1403510368340410550,  # Argent
    20: 1403510466818605118,  # Or
}


# ── Salons temporaires & radio ────────────────────────────────
TEMP_VC_CATEGORY = 1400559884117999687
TEMP_VC_LIMITS = {TEMP_VC_CATEGORY: 5}
RENAME_DELAY = 3  # délai en secondes avant renommage des salons temporaires
TEMP_VC_CHECK_INTERVAL_SECONDS = 30  # fréquence de vérification des noms

LOBBY_VC_ID = 1405630965803520221

# ── Streamers : création de salons vocaux temporaires ──────────
# Salon vocal "lobby" sur lequel l'utilisateur clique/rejoint
STREAMER_LOBBY_VC_ID = 1458443268185391104

# Rôle requis pour déclencher et pour voir/rejoindre le salon créé
STREAMER_ROLE_ID = 1458444090931810456
STREAMER_ALLOWED_ROLE_ID = STREAMER_ROLE_ID

# Nom de base du salon vocal créé
STREAMER_VC_BASE_NAME = "Streamer"

# Alias cohérents (évite les doublons dans le code appelant)
TRIGGER_VOICE_CHANNEL_ID = STREAMER_LOBBY_VC_ID
ALLOWED_ROLE_ID = STREAMER_ROLE_ID

# Catégorie où créer les salons vocaux (0 = fallback sur la catégorie du trigger)
TEMP_VOICE_CATEGORY_ID: int = SETTINGS.temp_voice_category_id

# Délai avant suppression du salon si vide (secondes)
DELETE_DELAY_SECONDS: int = SETTINGS.delete_delay_seconds


RADIO_VC_ID = 1405695147114758245
RADIO_TEXT_CHANNEL_ID = 1409333722754580571
RADIO_STREAM_URL = SETTINGS.radio_stream_url
RADIO_RAP_STREAM_URL = "https://stream.laut.fm/englishrap"
RADIO_RAP_FR_STREAM_URL = SETTINGS.radio_rap_fr_stream_url

ROCK_RADIO_VC_ID = 1408081503707074650
ROCK_RADIO_STREAM_URL = SETTINGS.rock_radio_stream_url


# ── Divers ────────────────────────────────────────────────────
XP_VIEWER_ROLE_ID = 1403510368340410550
TOP_MSG_ROLE_ID = 1406412171965104208
TOP_VC_ROLE_ID = 1406412383878119485
MVP_ROLE_ID = 1406412507433795595

ANNOUNCE_CHANNEL_ID: int = SETTINGS.announce_channel_id
"""Salon utilisé pour les annonces de la machine à sous."""

AWARD_ANNOUNCE_CHANNEL_ID: int = SETTINGS.award_announce_channel_id

WRITER_ROLE_ID = TOP_MSG_ROLE_ID
VOICE_ROLE_ID = TOP_VC_ROLE_ID
ENABLE_DAILY_AWARDS: bool = SETTINGS.enable_daily_awards

LEVEL_UP_CHANNEL = 1402419913716531352
LEVEL_FEED_CHANNEL_ID: int = SETTINGS.level_feed_channel_id
ENABLE_GAME_LEVEL_FEED: bool = SETTINGS.enable_game_level_feed

CHANNEL_ROLES = 1400560866478395512
CHANNEL_WELCOME = 1400550333796716574
LOBBY_TEXT_CHANNEL = 1402258805533970472

TIKTOK_ANNOUNCE_CH: int = SETTINGS.tiktok_announce_ch
ACTIVITY_SUMMARY_CH: int = SETTINGS.activity_summary_ch

UPDATE_CHANNEL_ID = 1400550888246083585

MACHINE_A_SOUS_CHANNEL_ID = 1405170020748755034
MACHINE_A_SOUS_XP_CHANNEL_ID = MACHINE_A_SOUS_CHANNEL_ID

FEEDBACK_PORTAL_CHANNEL_ID = 1400574356597506180
FEEDBACK_STAFF_CHANNEL_ID = 1404224143330906222


# ── Rappels de rôles et notifications ─────────────────────────
REMINDER_CHANNEL_ID: int = SETTINGS.reminder_channel_id
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


# ── Machine à sous ───────────────────────────────────────────
MACHINE_A_SOUS_ROLE_ID: int = 1405170057792979025
"""Rôle attribué au gagnant de la machine à sous."""

MACHINE_A_SOUS_BOUNDARY_CHECK_INTERVAL_MINUTES: int = (
    SETTINGS.machine_a_sous_boundary_check_interval_minutes
)
"""Intervalle en minutes entre deux vérifications de l'état de la machine à sous."""


# ── Pari XP ──────────────────────────────────────────────────
PARI_XP_CHANNEL_ID: int = 1408834276228730900
"""Salon dédié à la roulette XP."""

PARI_XP_ROLE_ID: int = SETTINGS.pari_xp_role_id
"""Rôle optionnel attribué au dernier gagnant de la roulette XP."""


# ── Persistance et I/O ────────────────────────────────────────
DATA_DIR: str = SETTINGS.data_dir
"""Répertoire de stockage persistant."""


# ── Double XP vocal ───────────────────────────────────────────
"""Les sessions Double XP vocal ne sont plus générées automatiquement."""

XP_DOUBLE_VOICE_DURATION_MINUTES: int = SETTINGS.xp_double_voice_duration_minutes
"""Durée d'une session Double XP vocal en minutes."""

XP_DOUBLE_VOICE_START_HOUR: int = SETTINGS.xp_double_voice_start_hour
"""Heure de début minimale pour une session (Europe/Paris)."""

XP_DOUBLE_VOICE_END_HOUR: int = SETTINGS.xp_double_voice_end_hour
"""Heure de début maximale pour une session (Europe/Paris)."""

XP_DOUBLE_VOICE_ANNOUNCE_CHANNEL_ID: int = (
    SETTINGS.xp_double_voice_announce_channel_id
)
"""Salon où sont annoncées les sessions Double XP vocal."""


# ── Jeux organisés ────────────────────────────────────────────
GAMES_DATA_DIR: str = SETTINGS.games_data_dir
"""Répertoire de persistance des événements de jeu, sous ``DATA_DIR`` par défaut."""


# ── Renommage des salons ──────────────────────────────────────
CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL: int = (
    SETTINGS.channel_rename_min_interval_per_channel
)
"""Intervalle minimal entre deux renommages du même salon."""

CHANNEL_RENAME_MIN_INTERVAL_GLOBAL: int = SETTINGS.channel_rename_min_interval_global
"""Intervalle minimal global entre les renommages de salons."""

CHANNEL_RENAME_DEBOUNCE_SECONDS: int = SETTINGS.channel_rename_debounce_seconds
"""Délai appliqué avant le renommage d'un salon."""

CHANNEL_RENAME_MAX_RETRIES: int = SETTINGS.channel_rename_max_retries
"""Nombre maximum de tentatives de renommage en cas de 429."""

CHANNEL_RENAME_BACKOFF_BASE: float = SETTINGS.channel_rename_backoff_base
"""Base du délai exponentiel entre les tentatives de renommage."""


# ── Propriétaire du bot ──────────────────────────────────────
OWNER_ID: int = SETTINGS.owner_id


# ── API Metering ─────────────────────────────────────────────
BOT_ALERTS_CHANNEL_ID: int = SETTINGS.bot_alerts_channel_id
API_BUDGET_PER_10MIN: int = SETTINGS.api_budget_per_10min
API_SOFT_LIMIT_PCT: float = SETTINGS.api_soft_limit_pct
API_HARD_LIMIT_PCT: float = SETTINGS.api_hard_limit_pct
API_SLOW_CALL_MS: int = SETTINGS.api_slow_call_ms
API_REPORT_INTERVAL_MIN: int = SETTINGS.api_report_interval_min
API_METER_PERSIST_INTERVAL_SECONDS: int = (
    SETTINGS.api_meter_persist_interval_seconds
)
"""Intervalle de persistance par lot des métriques API (secondes)."""


# ── Logs critiques ────────────────────────────────────────────
CRITICAL_LOG_CHANNEL_ID: int = SETTINGS.critical_log_channel_id
