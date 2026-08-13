from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"


def test_deno_install_is_pinned_and_checksum_verified() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "deno.land/install.sh" not in content
    assert re.search(r"ARG DENO_VERSION=\d+\.\d+\.\d+", content)

    checksums = re.findall(
        r"ARG DENO_SHA256_(?:AMD64|ARM64)=([0-9a-f]{64})",
        content,
    )
    assert len(checksums) == 2
    assert len(set(checksums)) == 2
    assert 'sha256sum -c -' in content
    assert "deno-${deno_arch}-unknown-linux-gnu.zip" in content


def test_runtime_stage_drops_build_only_download_tools() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    runtime_stage = content.rsplit("FROM python:3.11-slim", maxsplit=1)[1]

    assert "curl" not in runtime_stage
    assert "unzip" not in runtime_stage
    assert "COPY --from=deno-fetcher /usr/local/bin/deno" in runtime_stage


def test_runtime_uses_explicit_unprivileged_user() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG APP_UID=10001" in content
    assert "ARG APP_GID=10001" in content
    assert "USER refuge:refuge" in content
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in content


def test_railway_entrypoint_initialises_only_expected_volume_then_drops_privileges() -> None:
    content = ENTRYPOINT.read_text(encoding="utf-8")

    assert 'EXPECTED_VOLUME_PATH="/app/data"' in content
    assert "RAILWAY_VOLUME_MOUNT_PATH" in content
    assert 'chown -R "$APP_USER:$APP_GROUP" "$volume_path"' in content
    assert 'exec gosu "$APP_USER:$APP_GROUP" "$@"' in content
    assert content.rstrip().endswith('exec "$@"')


def test_dockerignore_excludes_sensitive_and_local_state() -> None:
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    required_patterns = {
        ".git/",
        ".env",
        ".env.*",
        "!.env.example",
        ".venv/",
        "venv/",
        "data/",
        "cookies*.txt",
        "**/cookies*.txt",
    }
    assert required_patterns <= ignored
