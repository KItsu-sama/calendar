"""Duration string parsing: '30m' -> 30, '2h' -> 120, '90m' -> 90."""
import re

_DURATION_RE = re.compile(r"^(\d+)([mh])$")


class DurationParseError(ValueError):
    pass


def parse_duration_minutes(raw: str) -> int:
    """Parse a duration token like '30m' or '2h' into whole minutes."""
    match = _DURATION_RE.match(raw.strip().lower())
    if not match:
        raise DurationParseError(
            f"Invalid duration '{raw}'. Expected formats like '30m' or '2h'."
        )
    value, unit = match.groups()
    value = int(value)
    return value * 60 if unit == "h" else value
