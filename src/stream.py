"""
Simulated sensor stream.

Real systems read from Kafka, MQTT, a socket, a serial port... Here we fake it
with a Python *generator*: a function that `yield`s one reading at a time and
pauses until the consumer asks for the next one. That is exactly the shape of a
real stream ("here is one more value"), so the rest of the system does not
care whether the data is simulated or real.

Each sensor produces:

    value = baseline + slow_drift(t) + noise  (+ an anomaly offset, sometimes)

Anomaly types we inject (so the detector has something to find):
    * spike  - a single point far away from normal
    * shift  - the level jumps and stays there for 10..30 points, then returns
    * drift  - the level slowly ramps away over 30..60 points, then snaps back
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class Reading:
    """One measurement from one sensor."""

    sensor: str
    timestamp: float  # seconds since epoch, on the *simulated* clock (see below)
    value: float
    # Ground-truth label of the injected anomaly ("spike", "shift", "drift") or
    # None. The detector never looks at this; it is only for debugging/demo.
    anomaly: str | None = None


@dataclass
class SensorSpec:
    """How one simulated sensor behaves when everything is fine."""

    name: str
    baseline: float  # the "normal" level
    noise_std: float  # random jitter around the level
    drift_amplitude: float = 0.0  # size of the slow, harmless up/down wave
    drift_period: float = 3600.0  # length of that wave, in points


@dataclass
class _Anomaly:
    """An anomaly currently being injected into one sensor."""

    kind: str  # "spike" | "shift" | "drift"
    length: int  # how many points it lasts
    magnitude: float  # final offset added to the normal value
    step: int = 0  # how many points have been emitted so far

    def next_offset(self) -> float:
        self.step += 1
        if self.kind == "drift":
            # Ramp linearly from ~0 up to `magnitude` over `length` points.
            return self.magnitude * self.step / self.length
        # spike and shift add a constant offset.
        return self.magnitude

    @property
    def finished(self) -> bool:
        return self.step >= self.length


class SensorSimulator:
    """Generates values for one sensor, occasionally injecting an anomaly."""

    def __init__(self, spec: SensorSpec, anomaly_probability: float, rng: np.random.Generator):
        self.spec = spec
        self.anomaly_probability = anomaly_probability
        self.rng = rng
        self._anomaly: _Anomaly | None = None

    def next_value(self, tick: int) -> tuple[float, str | None]:
        s = self.spec

        # 1) Normal behaviour: baseline + slow sine-wave drift + Gaussian noise.
        #    The drift is slow enough that a rolling window can follow it,
        #    so it should NOT be flagged as an anomaly.
        drift = s.drift_amplitude * math.sin(2 * math.pi * tick / s.drift_period)
        value = s.baseline + drift + self.rng.normal(0.0, s.noise_std)

        # 2) Maybe start a new anomaly (only one at a time per sensor).
        if self._anomaly is None and self.rng.random() < self.anomaly_probability:
            self._anomaly = self._new_anomaly()

        # 3) Apply the active anomaly, if any.
        label = None
        if self._anomaly is not None:
            value += self._anomaly.next_offset()
            label = self._anomaly.kind
            if self._anomaly.finished:
                self._anomaly = None

        return value, label

    def _new_anomaly(self) -> _Anomaly:
        # Sizes are expressed in "noise sigmas" so they make sense for every
        # sensor, whatever its units are.
        sigma = self.spec.noise_std
        sign = float(self.rng.choice([-1.0, 1.0]))
        kind = str(self.rng.choice(["spike", "shift", "drift"]))

        if kind == "spike":
            return _Anomaly("spike", 1, sign * self.rng.uniform(6, 10) * sigma)
        if kind == "shift":
            length = int(self.rng.integers(10, 31))  # 10..30 inclusive
            return _Anomaly("shift", length, sign * self.rng.uniform(5, 8) * sigma)
        length = int(self.rng.integers(30, 61))  # 30..60 inclusive
        return _Anomaly("drift", length, sign * self.rng.uniform(8, 12) * sigma)


def specs_from_config(sensors_cfg: dict) -> list[SensorSpec]:
    """Turn the `stream.sensors` section of config.yaml into SensorSpec objects."""
    return [SensorSpec(name=name, **params) for name, params in sensors_cfg.items()]


def stream_readings(
    specs: list[SensorSpec],
    interval_seconds: float = 1.0,
    fast: bool = False,
    max_ticks: int | None = None,
    anomaly_probability: float = 0.005,
    seed: int | None = None,
    start_time: float | None = None,
) -> Iterator[Reading]:
    """
    Yield readings forever (or for `max_ticks` ticks). Each tick yields one
    Reading per sensor.

    Timestamps come from a *simulated clock*: start_time + tick * interval.
    In normal mode we also sleep `interval_seconds` between ticks, so the
    simulated clock matches the wall clock. In fast mode we skip the sleep:
    the data and timestamps are identical, it just arrives instantly. Because
    everything downstream (e.g. the 60 s alert cooldown) uses these
    timestamps, fast mode behaves exactly like real time, only quicker.
    """
    rng = np.random.default_rng(seed)  # same seed -> same data, great for tests
    sims = [SensorSimulator(spec, anomaly_probability, rng) for spec in specs]
    t0 = time.time() if start_time is None else start_time

    tick = 0
    while max_ticks is None or tick < max_ticks:
        timestamp = t0 + tick * interval_seconds
        for sim in sims:
            value, label = sim.next_value(tick)
            yield Reading(sim.spec.name, timestamp, value, label)
        tick += 1
        if not fast:
            time.sleep(interval_seconds)
