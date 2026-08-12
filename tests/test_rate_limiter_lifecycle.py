import pytest

from utils.rate_limit import GlobalRateLimiter, TokenBucket


def test_rate_limiter_rejects_non_positive_global_rps(monkeypatch):
    for value in ("0", "-1"):
        monkeypatch.setenv("GLOBAL_RPS", value)
        with pytest.raises(ValueError, match=r"^GLOBAL_RPS must be >= 1$"):
            GlobalRateLimiter()


def test_rate_limiter_rejects_non_integer_global_rps(monkeypatch):
    monkeypatch.setenv("GLOBAL_RPS", "invalid")

    with pytest.raises(ValueError, match=r"^GLOBAL_RPS must be an integer >= 1$"):
        GlobalRateLimiter()


def test_rate_limiter_accepts_positive_global_rps(monkeypatch):
    monkeypatch.setenv("GLOBAL_RPS", "1")

    limiter = GlobalRateLimiter()

    assert limiter.global_rps == 1


def test_token_bucket_rejects_non_progressing_configuration():
    with pytest.raises(ValueError, match=r"^TokenBucket capacity must be > 0$"):
        TokenBucket(0, 1)

    with pytest.raises(ValueError, match=r"^TokenBucket refill_rate must be > 0$"):
        TokenBucket(1, 0)


@pytest.mark.asyncio
async def test_token_bucket_rejects_impossible_acquire_amount():
    bucket = TokenBucket(1, 1)

    with pytest.raises(
        ValueError, match=r"^TokenBucket acquire amount must not exceed capacity$"
    ):
        await bucket.acquire(2)


@pytest.mark.asyncio
async def test_rate_limiter_shutdown_is_idempotent_and_restartable():
    limiter = GlobalRateLimiter()
    limiter.strict = True

    limiter.start()
    first_task = limiter._task
    assert first_task is not None
    assert not first_task.done()

    await limiter.aclose()

    assert limiter._task is None
    assert first_task.cancelled()

    # Closing an already stopped limiter must remain harmless.
    await limiter.aclose()

    limiter.start()
    second_task = limiter._task
    assert second_task is not None
    assert second_task is not first_task
    assert not second_task.done()

    await limiter.aclose()

    assert limiter._task is None
    assert second_task.cancelled()
