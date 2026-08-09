import utils.voice as voice_utils
from utils.audio import (
    FFMPEG_BEFORE,
    FFMPEG_OPTIONS,
    FFMPEG_VOD_BEFORE,
    FFMPEG_VOD_OPTIONS,
)


class DummyVoice:
    def __init__(self) -> None:
        self.played = None

    def is_playing(self) -> bool:
        return False

    def play(self, source, after=None) -> None:
        self.played = (source, after)


def test_radio_stream_keeps_low_latency_profile(monkeypatch):
    captured = {}

    def fake_ffmpeg(source, *, before_options, options):
        captured.update(
            source=source,
            before_options=before_options,
            options=options,
        )
        return object()

    monkeypatch.setattr(voice_utils.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(voice_utils.discord, "FFmpegPCMAudio", fake_ffmpeg)

    voice = DummyVoice()
    voice_utils.play_stream(voice, "https://radio.example/live")

    assert captured["before_options"] == FFMPEG_BEFORE
    assert captured["options"] == FFMPEG_OPTIONS
    assert "nobuffer" in captured["before_options"]
    assert "reconnect_streamed" not in captured["before_options"]


def test_on_demand_stream_uses_buffered_reconnect_profile(monkeypatch):
    captured = {}

    def fake_ffmpeg(source, *, before_options, options):
        captured.update(
            source=source,
            before_options=before_options,
            options=options,
        )
        return object()

    monkeypatch.setattr(voice_utils.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(voice_utils.discord, "FFmpegPCMAudio", fake_ffmpeg)

    voice = DummyVoice()
    voice_utils.play_stream(
        voice,
        "https://youtube.example/audio",
        headers="User-Agent: yt-dlp\r\nReferer: https://youtube.com/\r\n",
    )

    assert captured["options"] == FFMPEG_VOD_OPTIONS
    assert FFMPEG_VOD_BEFORE in captured["before_options"]
    assert "reconnect_on_network_error 1" in captured["before_options"]
    assert "reconnect_on_http_error 4xx,5xx" in captured["before_options"]
    assert "reconnect_streamed 1" in captured["before_options"]
    assert "thread_queue_size 4096" in captured["before_options"]
    assert "nobuffer" not in captured["before_options"]
    assert "User-Agent: yt-dlp" in captured["before_options"]
