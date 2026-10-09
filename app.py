"""
Sensor anomaly detector
=======================

What this program does, in plain words:

1. It pretends to be two sensors (temperature and pressure) that send a
   reading every 0.5 seconds. Most readings are normal, but sometimes we
   inject problems on purpose:
     - a "spike": one single weird reading
     - a "sustained anomaly": the value jumps and stays wrong for a while

2. For every reading it asks: "Is this unusual compared to the last 50
   normal readings?" It answers with a z-score: how many standard
   deviations the reading is away from the recent average. If |z| > 3,
   the reading is an outlier.

3. It raises alerts in a smart way, so you don't get spammed:
     - ALERT    only after 3 outliers in a row (a single spike is ignored)
     - RESOLVED only after 5 normal readings in a row
     - while a sensor is already alerting, no new alert is sent
     - after RESOLVED there is a 30 s cooldown with no new alerts

4. It publishes everything as Prometheus metrics on http://localhost:8000/metrics
   so Prometheus can collect them and Grafana can draw the charts.
"""
import math
import random
import time
from collections import deque

from prometheus_client import Counter, Gauge, start_http_server

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
READ_EVERY_SECONDS = 0.5   # how often each sensor produces a reading
WINDOW_SIZE = 50           # how many recent normal readings we compare against
MIN_READINGS = 10          # don't judge anything until we have this many readings
Z_LIMIT = 3.0              # |z-score| above this counts as an outlier
OUTLIERS_TO_ALERT = 3      # outliers in a row needed to raise an alert
NORMALS_TO_RESOLVE = 5     # normal readings in a row needed to clear an alert
COOLDOWN_SECONDS = 30.0    # quiet period after an alert is resolved

# ---------------------------------------------------------------------------
# Prometheus metrics (each one is labelled with the sensor name)
# ---------------------------------------------------------------------------
SENSOR_VALUE = Gauge("sensor_value", "Current sensor reading", ["sensor"])
SENSOR_ZSCORE = Gauge("sensor_zscore", "How unusual the reading is (z-score)", ["sensor"])
SENSOR_ALERT_STATE = Gauge("sensor_alert_state", "0 = NORMAL, 1 = ALERTING", ["sensor"])
ALERTS_FIRED = Counter("alerts_fired_total", "How many alerts have been raised", ["sensor"])


class FakeSensor:
    """Produces realistic-looking readings, with problems injected at random."""

    def __init__(self, name, normal_value, noise):
        self.name = name
        self.normal_value = normal_value  # the value the sensor usually hovers around
        self.noise = noise                # how much normal readings wobble
        self.reading_count = 0
        self.anomaly_readings_left = 0    # >0 while a sustained anomaly is happening
        self.anomaly_shift = 0.0          # how far off the anomaly pushes the value

    def read(self):
        self.reading_count += 1

        # Normal behaviour: a slow wave plus random noise.
        value = (self.normal_value
                 + math.sin(self.reading_count / 40) * self.noise
                 + random.gauss(0, self.noise))

        # Keep the first readings clean so the detector can learn what "normal" looks like.
        if self.reading_count <= WINDOW_SIZE:
            return value

        # 1% chance per reading: start a sustained anomaly lasting 10-30 readings.
        if self.anomaly_readings_left == 0 and random.random() < 0.01:
            self.anomaly_readings_left = random.randint(10, 30)
            direction = random.choice([-1, 1])
            self.anomaly_shift = direction * random.uniform(8, 12) * self.noise

        if self.anomaly_readings_left > 0:
            self.anomaly_readings_left -= 1
            return value + self.anomaly_shift

        # 3% chance per reading: a single spike.
        if random.random() < 0.03:
            direction = random.choice([-1, 1])
            return value + direction * random.uniform(6, 10) * self.noise

        return value


class AnomalyDetector:
    """Decides whether readings are unusual and when to raise or clear an alert."""

    def __init__(self, sensor_name):
        self.sensor_name = sensor_name
        self.recent_normal_readings = deque(maxlen=WINDOW_SIZE)
        self.state = "NORMAL"            # either "NORMAL" or "ALERTING"
        self.outliers_in_a_row = 0
        self.normals_in_a_row = 0
        self.quiet_until = 0.0           # end of the cooldown after a resolve

    def zscore(self, value):
        """How many standard deviations `value` is from the recent average."""
        history = self.recent_normal_readings
        if len(history) < MIN_READINGS:
            return 0.0
        average = sum(history) / len(history)
        spread = math.sqrt(sum((x - average) ** 2 for x in history) / len(history))
        if spread == 0:
            return 0.0
        return (value - average) / spread

    def check(self, value, now):
        """Process one reading. Returns its z-score."""
        z = self.zscore(value)
        is_outlier = abs(z) > Z_LIMIT

        # Only normal readings go into the history. If outliers went in, a long
        # anomaly would slowly become "the new normal" and stop being detected.
        if is_outlier:
            self.outliers_in_a_row += 1
            self.normals_in_a_row = 0
        else:
            self.recent_normal_readings.append(value)
            self.normals_in_a_row += 1
            self.outliers_in_a_row = 0

        if self.state == "NORMAL":
            in_cooldown = now < self.quiet_until
            if self.outliers_in_a_row >= OUTLIERS_TO_ALERT and not in_cooldown:
                self.state = "ALERTING"
                ALERTS_FIRED.labels(self.sensor_name).inc()
                print(f"ALERT    {self.sensor_name}: {self.outliers_in_a_row} unusual readings in a row "
                      f"(value={value:.2f}, z={z:.2f})", flush=True)

        elif self.state == "ALERTING":
            if self.normals_in_a_row >= NORMALS_TO_RESOLVE:
                self.state = "NORMAL"
                self.quiet_until = now + COOLDOWN_SECONDS
                print(f"RESOLVED {self.sensor_name}: back to normal for {self.normals_in_a_row} readings "
                      f"(no new alerts for {COOLDOWN_SECONDS:.0f}s)", flush=True)

        return z


def main():
    start_http_server(8000)
    print("Metrics available at http://localhost:8000/metrics", flush=True)

    sensors = [
        FakeSensor("temperature", normal_value=22.0, noise=0.3),
        FakeSensor("pressure", normal_value=101.3, noise=0.5),
    ]
    detectors = {sensor.name: AnomalyDetector(sensor.name) for sensor in sensors}

    # Show the alert counter as 0 from the start instead of only after the first alert.
    for sensor in sensors:
        ALERTS_FIRED.labels(sensor.name)

    while True:
        now = time.monotonic()
        for sensor in sensors:
            value = sensor.read()
            detector = detectors[sensor.name]
            z = detector.check(value, now)

            SENSOR_VALUE.labels(sensor.name).set(value)
            SENSOR_ZSCORE.labels(sensor.name).set(z)
            SENSOR_ALERT_STATE.labels(sensor.name).set(1 if detector.state == "ALERTING" else 0)

        time.sleep(READ_EVERY_SECONDS)


if __name__ == "__main__":
    main()
