import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import utils.voice as voice_mod


class DummyVoice:
    def __init__(self) -> None:
        self.play_called_with = None

    def is_playing(self) -> bool:
        return False

    def play(self, source, after=None):  # pragma: no cover - simple setter
        self.play_called_with = source


def test_play_stream_adds_headers(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/ffmpeg")
    captured = {}

    def fake_ffmpeg_audio(url, *, before_options, options):
        captured["url"] = url
        captured["before"] = before_options
        captured["options"] = options
        return object()

    monkeypatch.setattr(voice_mod.discord, "FFmpegPCMAudio", fake_ffmpeg_audio)

    voice = DummyVoice()
    voice_mod.play_stream(voice, "http://example.com", headers="X-Test: value")

    assert captured["url"] == "http://example.com"
    assert "-headers 'X-Test: value'" in captured["before"]
    assert voice.play_called_with is not None
