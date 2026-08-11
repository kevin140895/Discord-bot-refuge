from __future__ import annotations

import utils.voice as voice_utils


class DummyVoice:
    def __init__(self) -> None:
        self.played = None

    def is_playing(self) -> bool:
        return False

    def play(self, source, after=None) -> None:
        self.played = (source, after)


def test_play_stream_preserves_trailing_crlf_for_ffmpeg_headers(monkeypatch):
    captured = {}

    def fake_quote(value: str) -> str:
        captured["header_value"] = value
        return "<quoted-headers>"

    def fake_ffmpeg(source, *, before_options, options):
        captured.update(
            source=source,
            before_options=before_options,
            options=options,
        )
        return object()

    monkeypatch.setattr(voice_utils.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(voice_utils.shlex, "quote", fake_quote)
    monkeypatch.setattr(voice_utils.discord, "FFmpegPCMAudio", fake_ffmpeg)

    voice = DummyVoice()
    headers = "User-Agent: yt-dlp\r\nReferer: https://youtube.com/\r\n"
    voice_utils.play_stream(
        voice,
        "https://youtube.example/audio",
        headers=headers,
        on_demand=True,
    )

    assert captured["header_value"] == headers
    assert captured["header_value"].endswith("\r\n")
    assert "-headers <quoted-headers>" in captured["before_options"]


def test_play_stream_adds_crlf_when_upstream_header_lacks_it(monkeypatch):
    captured = {}

    def fake_quote(value: str) -> str:
        captured["header_value"] = value
        return "<quoted-headers>"

    monkeypatch.setattr(voice_utils.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(voice_utils.shlex, "quote", fake_quote)
    monkeypatch.setattr(
        voice_utils.discord,
        "FFmpegPCMAudio",
        lambda *args, **kwargs: object(),
    )

    voice_utils.play_stream(
        DummyVoice(),
        "https://youtube.example/audio",
        headers="User-Agent: yt-dlp",
        on_demand=True,
    )

    assert captured["header_value"] == "User-Agent: yt-dlp\r\n"
