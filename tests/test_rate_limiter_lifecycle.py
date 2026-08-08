import pytest

from utils.rate_limit import GlobalRateLimiter


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
