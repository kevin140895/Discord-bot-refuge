from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> list[str]:
    return [
        line.strip()
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _locked_requirement_starts(path: str) -> list[str]:
    return [
        line
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line
        and not line[0].isspace()
        and not line.startswith("#")
        and not line.startswith("--")
        and not line.startswith("-r ")
    ]


def test_runtime_manifest_excludes_test_only_dependencies():
    runtime = _requirements("requirements.in")

    assert not any(line.lower().startswith("pytest") for line in runtime)
    assert not any(line.lower().startswith("freezegun") for line in runtime)


def test_dev_manifest_extends_runtime_and_includes_test_tools():
    dev = _requirements("requirements-dev.in")

    assert "-r requirements.in" in dev
    assert "pytest" in dev
    assert "pytest-asyncio" in dev
    assert "freezegun" in dev
    assert "mypy" in dev
    assert "ruff" in dev
    assert "pip-audit" in dev


def test_runtime_manifest_keeps_breaking_change_guards():
    runtime = set(_requirements("requirements.in"))

    assert "discord.py>=2.7,<3" in runtime
    assert "PyNaCl>=1.6.2,<1.7" in runtime
    assert "davey>=0.1.0" in runtime
    assert "python-dotenv>=1.0,<2" in runtime
    assert "Pillow>=10.0,<13" in runtime
    assert "imageio-ffmpeg>=0.4,<1" in runtime
    assert "aiohttp>=3.8,<4" in runtime
    assert "tzdata>=2024.1" in runtime


def test_generated_locks_are_exact_and_hashed():
    for path in ("requirements.txt", "requirements-dev.txt", "requirements-youtube-pot.txt"):
        text = (ROOT / path).read_text(encoding="utf-8")
        starts = _locked_requirement_starts(path)

        assert starts, f"{path} must contain locked packages"
        assert "--hash=sha256:" in text
        assert all("==" in line for line in starts), starts


def test_lock_toolchain_is_pinned():
    tools = set(_requirements("requirements-tools.txt"))

    assert "pip==26.1.2" in tools
    assert "pip-tools==7.6.0" in tools


def test_music2_uses_known_good_youtube_runtime():
    runtime = set(_requirements("requirements.in"))
    runtime_lock = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pot_lock = (ROOT / "requirements-youtube-pot.txt").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "yt-dlp[default]>=2025.11.12" in runtime
    assert "yt-dlp==" in runtime_lock

    # Keep the provider plugin reproducible while avoiding the old architecture
    # that cloned and ran the JavaScript provider inside the bot container.
    assert "bgutil-ytdlp-pot-provider==1.3.1" in pot_lock
    assert "130635912e2450757438f72068b900076ac1a62d9f26a00afbe6f2ab258e8b25" in pot_lock
    assert "e62b21f9b2e4479d59af87a8900387c34892e8d7fdb223f266749a90e0be22de" in pot_lock
    assert "requirements-youtube-pot.txt" in dockerfile
    assert "--require-hashes -r requirements-youtube-pot.txt" in dockerfile

    # Deno remains explicit and reproducible for yt-dlp's JS challenge support.
    assert "deno.land/install.sh" not in dockerfile
    assert "ARG DENO_VERSION=" in dockerfile
    assert "DENO_SHA256_AMD64=" in dockerfile
    assert "DENO_SHA256_ARM64=" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "COPY --from=deno-fetcher /usr/local/bin/deno /usr/local/bin/deno" in dockerfile
    assert "deno --version" in dockerfile

    # The bot container must not own the provider process lifecycle anymore.
    assert "git clone" not in dockerfile
    assert "/bgutil-ytdlp-pot-provider/server" not in dockerfile
    assert "127.0.0.1:4416" not in dockerfile
    assert 'CMD ["python", "main.py"]' in dockerfile
