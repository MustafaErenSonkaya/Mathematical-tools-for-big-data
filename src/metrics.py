"""
Prometheus metrics.

Prometheus works by *pulling*: our app only keeps the latest numbers in
memory and serves them as plain text at http://<host>:8000/metrics. Every few
seconds Prometheus fetches that page and stores each number with a timestamp.
Grafana then queries Prometheus to draw charts.

Two metric types are used:
  * Gauge   - a value that can go up and down (current reading, z-score, state)
  * Counter - a value that only goes up (events so far). Prometheus' rate()
              and increase() functions turn counters into "per second" or
              "how many in the last hour", and they handle app restarts
              (counter resets) correctly.

Every metric has a `sensor` label, so one metric name covers all sensors:
    sensor_value{sensor="cpu"} 41.7
"""

from __future__ import annotations

import math

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server

from src.alerts import SensorState, State
from src.detector import Detection


class Metrics:
    def __init__(self, sensors: list[str], registry: CollectorRegistry | None = None):
        # A private registry (instead of the global default one) keeps tests
        # independent: each test can create its own Metrics without clashes.
        self.registry = registry or CollectorRegistry()
        r = self.registry

        self.value = Gauge("sensor_value", "Latest raw sensor reading", ["sensor"], registry=r)
        self.zscore = Gauge("sensor_zscore", "z-score of the latest reading (NaN during warmup)", ["sensor"], registry=r)
        self.is_outlier = Gauge("sensor_is_outlier", "1 if the latest reading is an outlier, else 0", ["sensor"], registry=r)
        self.alert_state = Gauge("sensor_alert_state", "Alert state: 0 = NORMAL, 1 = ALERTING", ["sensor"], registry=r)
        # Counter names get a "_total" suffix automatically in the output.
        self.alerts_fired = Counter("alerts_fired", "ALERT events fired", ["sensor"], registry=r)
        self.alerts_suppressed = Counter("alerts_suppressed", "Alerts suppressed by the cooldown", ["sensor"], registry=r)
        self.points = Counter("points_processed", "Readings processed", ["sensor"], registry=r)

        # Last suppressed_count we saw per sensor, to turn it into counter increments.
        self._last_suppressed = {s: 0 for s in sensors}

        # Create every labelled series up front so they exist with value 0
        # from the first scrape. Without this, a counter only appears on its
        # first increment, and increase() would miss that first event.
        for s in sensors:
            for m in (self.alerts_fired, self.alerts_suppressed, self.points):
                m.labels(sensor=s)
            self.alert_state.labels(sensor=s).set(0)

    def serve(self, port: int) -> None:
        """Start the /metrics HTTP endpoint in a background thread."""
        start_http_server(port, registry=self.registry)

    def observe(self, sensor: str, detection: Detection, state: SensorState, event_kind: str | None) -> None:
        """Update all metrics after one reading went through detector + alert manager."""
        self.points.labels(sensor=sensor).inc()
        self.value.labels(sensor=sensor).set(detection.value)
        # NaN shows up as a gap in Grafana, which is exactly right for warmup.
        self.zscore.labels(sensor=sensor).set(math.nan if detection.z is None else detection.z)
        self.is_outlier.labels(sensor=sensor).set(1 if detection.is_outlier else 0)
        self.alert_state.labels(sensor=sensor).set(1 if state.state is State.ALERTING else 0)

        if event_kind == "ALERT":
            self.alerts_fired.labels(sensor=sensor).inc()

        # The AlertManager keeps a running total; add only what's new.
        new = state.suppressed_count - self._last_suppressed.get(sensor, 0)
        if new > 0:
            self.alerts_suppressed.labels(sensor=sensor).inc(new)
        self._last_suppressed[sensor] = state.suppressed_count
