from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_starts_music_through_entrypoint():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'CMD ["sh", "/app/docker-entrypoint.sh"]' in dockerfile


def test_entrypoint_starts_bgutil_provider_before_bot():
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

    provider = "../src/main.ts"
    bot = "python main.py"

    assert "deno run" in entrypoint
    assert "--allow-net" in entrypoint
    assert provider in entrypoint
    assert bot in entrypoint
    assert entrypoint.index(provider) < entrypoint.index(bot)


def test_entrypoint_fails_if_provider_dies_during_startup():
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'kill -0 "$provider_pid"' in entrypoint
    assert "bgutil PO token provider failed to start" in entrypoint
