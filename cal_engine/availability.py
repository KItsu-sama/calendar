"""Availability layer.

Turns "UNALLOCATED TIME (availability unknown)" into *usable* free blocks by
removing protected time (sleep, meals, travel) and buffers around activities.

Nothing here is stored in state.json: the profile is a hand-written sidecar
file (profile.json) and usable blocks are always computed, never saved --
the same rule PROJECT.md already uses for free time.

All times are minutes since midnight (0..1440) inside this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_MIN = 1440


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def _prev_day(day: str) -> str:
    return DAYS[(DAYS.index(day) - 1) % 7]


def _days(value) -> tuple[str, ...]:
    if value in (None, "ALL"):
        return DAYS
    return tuple(str(d).upper() for d in value)


def subtract(blocks: list[tuple[int, int]], cuts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove every cut interval from every block."""
    out: list[tuple[int, int]] = []
    for s, e in blocks:
        cur = s
        for cs, ce in sorted(cuts):
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                out.append((cur, cs))
            cur = max(cur, ce)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


@dataclass(frozen=True)
class Protected:
    id: str
    days: tuple[str, ...]
    start: int
    end: int
    soft: bool = False


@dataclass
class Profile:
    protected: list[Protected] = field(default_factory=list)
    buffer_min: int = 0
    min_block_min: int = 15
    preferred_windows: list[dict] = field(default_factory=list)
    max_flexible_min_per_day: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        prot = [
            Protected(
                id=p["id"],
                days=_days(p.get("days")),
                start=to_min(p["start"]),
                end=to_min(p["end"]),
                soft=bool(p.get("soft", False)),
            )
            for p in d.get("protected", [])
        ]
        return cls(
            protected=prot,
            buffer_min=int(d.get("buffer_between_activities_min", 0)),
            min_block_min=int(d.get("min_block_min", 15)),
            preferred_windows=list(d.get("preferred_study_windows", [])),
            max_flexible_min_per_day=d.get("max_flexible_minutes_per_day"),
        )

    @property
    def is_empty(self) -> bool:
        return not self.protected and self.buffer_min == 0

    def protected_for_day(self, day: str) -> list[tuple[int, int, Protected]]:
        """Protected intervals falling on `day`. An entry whose end <= start
        wraps past midnight (e.g. sleep 22:30-06:00): the tail lands on the
        following day."""
        out: list[tuple[int, int, Protected]] = []
        for p in self.protected:
            wraps = p.end <= p.start
            if not wraps:
                if day in p.days:
                    out.append((p.start, p.end, p))
                continue
            if day in p.days:
                out.append((p.start, DAY_MIN, p))
            if _prev_day(day) in p.days and p.end > 0:
                out.append((0, p.end, p))
        return out

    def usable_blocks(
        self,
        day: str,
        busy: list[tuple[int, int]],
        allow_soft: bool = False,
        min_block: int | None = None,
    ) -> list[tuple[int, int]]:
        """Gaps minus protected time minus buffers, dropping blocks that are
        shorter than `min_block` (default: profile.min_block_min).

        allow_soft=True lets soft-protected time (e.g. dinner) be used."""
        cuts = [(max(0, s - self.buffer_min), min(DAY_MIN, e + self.buffer_min)) for s, e in busy]
        for s, e, p in self.protected_for_day(day):
            if p.soft and allow_soft:
                continue
            cuts.append((s, e))
        blocks = subtract([(0, DAY_MIN)], cuts)
        mb = self.min_block_min if min_block is None else min_block
        return [(s, e) for s, e in blocks if e - s >= mb]


def load_profile(path: str | Path | None) -> Profile:
    """Missing path or missing file -> empty profile (availability unknown)."""
    if not path:
        return Profile()
    p = Path(path)
    if not p.exists():
        return Profile()
    return Profile.from_dict(json.loads(p.read_text(encoding="utf-8")))
