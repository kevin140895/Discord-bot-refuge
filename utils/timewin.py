from zoneinfo import ZoneInfo
from datetime import datetime, timedelta


def is_open_now(
    tz: str | ZoneInfo = "Europe/Paris", start_h: int = 10, end_h: int = 22
) -> bool:
    """Check if the current time falls within a daily window.

    Vérifie si l'heure locale (``tz``) est comprise
    entre ``start_h``:00 inclus et ``end_h``:00 exclus.

    Args:
        tz: IANA time zone name or :class:`~zoneinfo.ZoneInfo` object.
        start_h: Hour (0-23) marking the start of the window, inclusive.
        end_h: Hour (0-23) marking the end of the window, exclusive.

    Returns:
        ``True`` if the current time in ``tz`` is between
        ``start_h`` and ``end_h``.

    Examples:
        >>> is_open_now("UTC", 9, 17)
        True  # when run at 10:00 UTC

    Notes:
        * The window does not wrap past midnight; ``start_h`` must be
          < ``end_h``.
        * A :class:`zoneinfo.ZoneInfoNotFoundError` is raised for
          unknown time zones.
    """
    tzinfo = ZoneInfo(tz) if isinstance(tz, str) else tz
    now = datetime.now(tzinfo)
    start = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=0, second=0, microsecond=0)
    return start <= now < end


def next_boundary_dt(
    now: datetime | None = None,
    tz: str | ZoneInfo = "Europe/Paris",
    start_h: int = 10,
    end_h: int = 22,
) -> datetime:
    """Return the next opening or closing time after ``now``.

    Renvoie la prochaine frontière (prochain 10:00 ou 22:00 local).

    Args:
        now: Reference time. If ``None``, the current time in ``tz`` is used.
        tz: IANA time zone name or :class:`~zoneinfo.ZoneInfo` object.
        start_h: Hour (0-23) when the window opens.
        end_h: Hour (0-23) when the window closes.

    Returns:
        A timezone-aware :class:`datetime.datetime` corresponding to the next
        boundary.

    Examples:
        >>> from datetime import datetime
        >>> from zoneinfo import ZoneInfo
        >>> now = datetime(2023, 1, 1, 9, tzinfo=ZoneInfo("UTC"))
        >>> next_boundary_dt(now, tz="UTC", start_h=10, end_h=22)
        datetime(2023, 1, 1, 10, 0, tzinfo=ZoneInfo('UTC'))

    Notes:
        * The function assumes ``start_h`` < ``end_h`` and does not support
          windows crossing midnight.
        * Invalid time zones raise :class:`zoneinfo.ZoneInfoNotFoundError`.
        * If ``now`` is naive, it is interpreted in the provided ``tz``.
    """
    tzinfo = ZoneInfo(tz) if isinstance(tz, str) else tz
    if now is None:
        now = datetime.now(tzinfo)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tzinfo)
    else:
        now = now.astimezone(tzinfo)

    candidates = []
    a = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    b = now.replace(hour=end_h, minute=0, second=0, microsecond=0)

    if a <= now:
        a = a + timedelta(days=1)
    if b <= now:
        b = b + timedelta(days=1)

    candidates.extend([a, b])
    return min(candidates)
