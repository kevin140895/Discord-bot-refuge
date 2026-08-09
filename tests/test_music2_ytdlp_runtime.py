from __future__ import annotations

from pathlib import Path

from yt_dlp.extractor.youtube._video import YoutubeIE

import cogs.music2_ytdlp_diagnostics as diagnostics


PINNED_SHA = "5d6b8c8cd19785c3086ae3a9ec618c45e25eb3bc"


def test_ytdlp_requirement_is_pinned_to_verified_upstream_revision() -> None:
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
        encoding="utf-8"
    )

    assert (
        "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/"
        f"{PINNED_SHA}.tar.gz"
    ) in requirements
    assert "yt-dlp[default]>=" not in requirements


def test_youtube_default_clients_include_post_july_maintenance() -> None:
    assert YoutubeIE._DEFAULT_CLIENTS == ("visionos", "android_vr", "web")


def test_runtime_snapshot_reports_pin_and_deno(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: "/usr/local/bin/deno")

    snapshot = diagnostics.runtime_snapshot()

    assert snapshot["yt_dlp_version"] == diagnostics.YT_DLP_VERSION
    assert snapshot["upstream_sha"] == PINNED_SHA
    assert snapshot["deno_path"] == "/usr/local/bin/deno"
