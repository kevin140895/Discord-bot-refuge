"""Audio-related constants and utilities."""

# Profil historique pour les radios live : latence faible, tampon minimal.
FFMPEG_BEFORE = "-fflags nobuffer -probesize 32k"
FFMPEG_OPTIONS = "-filter:a loudnorm"

# Profil dédié aux morceaux à la demande (YouTube/yt-dlp).
# Contrairement aux radios live, on conserve le buffering normal de FFmpeg et
# on autorise les reconnexions HTTP afin d'absorber les variations réseau sans
# provoquer de coupures audibles dans Discord.
FFMPEG_VOD_BEFORE = (
    "-reconnect_on_network_error 1 "
    "-reconnect_on_http_error 4xx,5xx "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5 "
    "-thread_queue_size 4096"
)
FFMPEG_VOD_OPTIONS = "-vn"
