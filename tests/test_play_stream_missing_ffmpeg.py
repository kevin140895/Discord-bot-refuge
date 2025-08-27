import logging
import shutil

from utils.voice import play_stream


class DummyVoice:
    def __init__(self) -> None:
        self.play_called = False

    def is_playing(self) -> bool:
        return False

    def play(self, source, after=None) -> None:  # pragma: no cover - only sets flag
        self.play_called = True


def test_play_stream_missing_ffmpeg(monkeypatch, caplog):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    voice = DummyVoice()
    with caplog.at_level(logging.WARNING):
        play_stream(voice, "http://example.com")
    assert "FFmpeg introuvable" in caplog.text
    assert not voice.play_called
