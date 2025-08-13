from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

def is_open_now(tz: str = "Europe/Paris", start_h: int = 10, end_h: int = 22) -> bool:
    """
    Retourne True si l'heure locale (tz) est entre start_h:00 inclus et end_h:00 exclus.
    """
    now = datetime.now(ZoneInfo(tz))
    start = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=0, second=0, microsecond=0)
    return start <= now < end

def next_boundary_dt(now: datetime | None = None, tz: str = "Europe/Paris",
                     start_h: int = 10, end_h: int = 22) -> datetime:
    """
    Donne la prochaine 'frontière' (prochain 10:00 ou 22:00 local).
    Utile pour (dés)activer le bouton automatiquement au bon moment.
    """
    if now is None:
        now = datetime.now(ZoneInfo(tz))

    candidates = []
    a = now.replace(hour=start_h, minute=0, second=0, microsecond=0)
    b = now.replace(hour=end_h, minute=0, second=0, microsecond=0)

    if a <= now:
        a = a + timedelta(days=1)
    if b <= now:
        b = b + timedelta(days=1)

    candidates.extend([a, b])
    return min(candidates)
