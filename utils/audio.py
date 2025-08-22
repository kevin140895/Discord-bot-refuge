"""Audio-related constants and utilities."""

# Common FFmpeg options for audio streaming
FFMPEG_BEFORE = "-fflags nobuffer -probesize 32k"
FFMPEG_OPTIONS = "-filter:a loudnorm"
