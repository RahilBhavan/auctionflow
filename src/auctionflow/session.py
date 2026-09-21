from datetime import datetime, time
from zoneinfo import ZoneInfo


def _zone(config):
    return ZoneInfo(config.session_timezone)


def session_time(timestamp: datetime, config=None) -> time:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(_zone(config) if config else ZoneInfo("America/New_York")).time().replace(tzinfo=None)


def is_session_open(timestamp: datetime, config=None) -> bool:
    value = session_time(timestamp, config)
    start_h, start_m = map(int, (config.session_start if config else "09:35").split(":"))
    end_h, end_m = map(int, (config.session_end if config else "11:30").split(":"))
    return time(start_h, start_m) <= value < time(end_h, end_m)


def session_id(timestamp: datetime, config=None) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(_zone(config) if config else ZoneInfo("America/New_York")).date().isoformat()
