"""
AlertManager: turns a noisy stream of z-scores into a few meaningful events.

A detector says "this POINT looks odd". Humans want "this SENSOR has a
problem" once, and "it's fixed" once. Paging someone for every odd point
would be useless (alert fatigue), so we keep a small state machine per sensor:

                 K consecutive |z| > enter_z
        NORMAL  ------------------------------>  ALERTING   (fires ALERT)
           ^                                         |
           |     M consecutive |z| < exit_z          |
           +-----------------------------------------+       (fires RESOLVED)

Why each rule exists:
  * K consecutive outliers to enter: a single spike (or two) is usually
    noise. Requiring a streak filters those out.
  * Hysteresis (enter at 3.0, exit below 2.0): with a single threshold, a
    value hovering around 3.0 would flip ALERT/RESOLVED/ALERT... ("flapping").
    Two thresholds create a dead zone in between where the state just stays.
  * M consecutive calm points to exit: the same idea in time. One calm
    point in the middle of an incident does not mean it is over.
  * Cooldown after RESOLVED: if the problem comes back right away it is most
    likely the same incident, so we don't page again; we only count it as
    "suppressed" so the information is not lost.

All times are the readings' timestamps (not the wall clock), so tests and
fast mode behave exactly like real time.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum


class State(Enum):
    NORMAL = "NORMAL"
    ALERTING = "ALERTING"


@dataclass(frozen=True)
class AlertEvent:
    """Something a human should hear about: an incident started or ended."""

    kind: str  # "ALERT" or "RESOLVED"
    incident_id: str
    sensor: str
    timestamp: float  # when this event was fired
    started_at: float  # timestamp of the first outlier of the incident
    z: float  # z-score of the point that triggered this event
    value: float | None  # raw sensor value of that point
    peak_z: float  # most extreme z-score seen so far in the incident (keeps its sign)
    duration_seconds: float | None = None  # only set for RESOLVED

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time_iso"] = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        return d


@dataclass
class SensorState:
    """Everything the AlertManager remembers about one sensor."""

    state: State = State.NORMAL
    # --- used while NORMAL ---
    outlier_streak: int = 0  # consecutive points with |z| > enter_z
    streak_started_at: float | None = None
    streak_peak_z: float = 0.0
    counted_as_suppressed: bool = False  # this streak was already counted as suppressed
    cooldown_until: float = float("-inf")  # no new alert before this timestamp
    suppressed_count: int = 0  # total streaks swallowed by the cooldown
    # --- used while ALERTING ---
    calm_streak: int = 0  # consecutive points with |z| < exit_z
    incident_id: str | None = None
    incident_started_at: float | None = None
    peak_z: float = 0.0


def _more_extreme(a: float, b: float) -> float:
    """Return whichever of a, b is further from zero (keeps the sign)."""
    return a if abs(a) >= abs(b) else b


def new_incident_id(sensor: str) -> str:
    """Unique id like 'cpu-3f9a1c2b'. uuid4 makes collisions practically impossible."""
    return f"{sensor}-{uuid.uuid4().hex[:8]}"


class AlertManager:
    def __init__(
        self,
        enter_z: float = 3.0,
        exit_z: float = 2.0,
        k_consecutive: int = 3,
        m_consecutive: int = 5,
        cooldown_seconds: float = 60.0,
    ):
        if exit_z >= enter_z:
            raise ValueError("exit_z must be lower than enter_z (that gap is the hysteresis)")
        if k_consecutive < 1 or m_consecutive < 1:
            raise ValueError("k_consecutive and m_consecutive must be >= 1")

        self.enter_z = enter_z
        self.exit_z = exit_z
        self.k = k_consecutive
        self.m = m_consecutive
        self.cooldown_seconds = cooldown_seconds
        self.sensors: dict[str, SensorState] = {}  # created lazily per sensor name

    def state_of(self, sensor: str) -> SensorState:
        return self.sensors.setdefault(sensor, SensorState())

    def process(
        self, sensor: str, timestamp: float, z: float | None, value: float | None = None
    ) -> AlertEvent | None:
        """
        Feed one z-score. Returns an AlertEvent when the sensor changes state,
        otherwise None. `z=None` (detector still warming up) is ignored.
        """
        if z is None:
            return None

        s = self.state_of(sensor)
        if s.state is State.NORMAL:
            return self._process_normal(sensor, s, timestamp, z, value)
        return self._process_alerting(sensor, s, timestamp, z, value)

    # ------------------------------------------------------------------ NORMAL
    def _process_normal(self, sensor, s: SensorState, ts, z, value) -> AlertEvent | None:
        if abs(z) <= self.enter_z:
            # Streak broken: start counting from zero again.
            s.outlier_streak = 0
            s.streak_started_at = None
            s.streak_peak_z = 0.0
            s.counted_as_suppressed = False
            return None

        # Another outlier in a row.
        if s.outlier_streak == 0:
            s.streak_started_at = ts
        s.outlier_streak += 1
        s.streak_peak_z = _more_extreme(s.streak_peak_z, z)

        if s.outlier_streak < self.k:
            return None  # not enough evidence yet

        # K in a row: this would open an incident... unless we are in cooldown.
        if ts < s.cooldown_until:
            if not s.counted_as_suppressed:  # count each streak only once
                s.suppressed_count += 1
                s.counted_as_suppressed = True
            return None
        # (If the cooldown expires while the streak is still going on, we fall
        #  through and open an alert: the problem is clearly persistent.)

        # Transition NORMAL -> ALERTING.
        s.state = State.ALERTING
        s.incident_id = new_incident_id(sensor)
        s.incident_started_at = s.streak_started_at
        s.peak_z = s.streak_peak_z
        s.calm_streak = 0
        s.outlier_streak = 0
        s.counted_as_suppressed = False
        return AlertEvent(
            kind="ALERT",
            incident_id=s.incident_id,
            sensor=sensor,
            timestamp=ts,
            started_at=s.incident_started_at,
            z=z,
            value=value,
            peak_z=s.peak_z,
        )

    # ---------------------------------------------------------------- ALERTING
    def _process_alerting(self, sensor, s: SensorState, ts, z, value) -> AlertEvent | None:
        s.peak_z = _more_extreme(s.peak_z, z)

        if abs(z) < self.exit_z:
            s.calm_streak += 1
        else:
            # Anything >= exit_z (even 2.5, below enter_z) resets the calm streak.
            # This is the hysteresis "dead zone".
            s.calm_streak = 0

        if s.calm_streak < self.m:
            return None  # still alerting, stay quiet: we already fired ALERT once

        # M calm points in a row: transition ALERTING -> NORMAL.
        event = AlertEvent(
            kind="RESOLVED",
            incident_id=s.incident_id,
            sensor=sensor,
            timestamp=ts,
            started_at=s.incident_started_at,
            z=z,
            value=value,
            peak_z=s.peak_z,
            duration_seconds=ts - s.incident_started_at,
        )
        s.state = State.NORMAL
        s.cooldown_until = ts + self.cooldown_seconds
        s.calm_streak = 0
        s.incident_id = None
        s.incident_started_at = None
        s.peak_z = 0.0
        s.outlier_streak = 0
        s.streak_started_at = None
        s.streak_peak_z = 0.0
        return event
