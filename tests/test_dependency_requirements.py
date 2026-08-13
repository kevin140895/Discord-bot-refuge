from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> list[str]:
    return [
        line.strip()
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_runtime_requirements_exclude_test_only_dependencies():
    runtime = _requirements("requirements.txt")

    assert not any(line.lower().startswith("pytest") for line in runtime)
    assert not any(line.lower().startswith("freezegun") for line in runtime)


def test_dev_requirements_extend_runtime_and_include_test_tools():
    dev = _requirements("requirements-dev.txt")

    assert "-r requirements.txt" in dev
    assert "pytest" in dev
    assert "pytest-asyncio" in dev
    assert "freezegun" in dev


def test_runtime_dependencies_have_breaking_change_guards():
    runtime = set(_requirements("requirements.txt"))

    assert "discord.py>=2.7,<3" in runtime
    assert "PyNaCl>=1.6.2,<1.7" in runtime
    assert "davey>=0.1.0" in runtime
    assert "python-dotenv>=1.0,<2" in runtime
    assert "Pillow>=10.0,<13" in runtime
    assert "imageio-ffmpeg>=0.4,<1" in runtime
    assert "aiohttp>=3.8,<4" in runtime
    assert "tzdata>=2024.1" in runtime


def test_music2_uses_known_good_youtube_runtime():
    runtime = set(_requirements("requirements.txt"))
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "yt-dlp[default]>=2025.11.12" in runtime
    assert not any(line.startswith("bgutil-ytdlp-pot-provider") for line in runtime)

    # Deno must stay explicit and reproducible. The old DENO_INSTALL marker
    # represented the remote install.sh path and must not become the contract.
    assert "deno.land/install.sh" not in dockerfile
    assert "ARG DENO_VERSION=" in dockerfile
    assert "DENO_SHA256_AMD64=" in dockerfile
    assert "DENO_SHA256_ARM64=" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "COPY --from=deno-fetcher /usr/local/bin/deno /usr/local/bin/deno" in dockerfile
    assert "deno --version" in dockerfile

    assert 'CMD ["python", "main.py"]' in dockerfile
    assert "bgutil-ytdlp-pot-provider" not in dockerfile
