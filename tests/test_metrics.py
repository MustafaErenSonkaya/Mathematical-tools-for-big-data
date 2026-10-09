import math

from prometheus_client import generate_latest

from src.alerts import AlertManager
from src.detector import Detection
from src.metrics import Metrics


def test_all_required_metrics_are_exposed():
    m = Metrics(["temp", "cpu", "pressure"])
    text = generate_latest(m.registry).decode()
    for name in [
        "sensor_value",
        "sensor_zscore",
        "sensor_is_outlier",
        "sensor_alert_state",
        "alerts_fired_total",
        "alerts_suppressed_total",
        "points_processed_total",
    ]:
        assert f"# TYPE {name.removesuffix('_total')}" in text, name
    # Counters exist at 0 before anything happens (so increase() sees the first event).
    assert 'alerts_fired_total{sensor="cpu"} 0.0' in text


def test_observe_tracks_state_and_counters():
    m = Metrics(["s"])
    am = AlertManager(cooldown_seconds=60)
    get = m.registry.get_sample_value

    # Warmup point: z is NaN, not outlier.
    m.observe("s", Detection(1.0, None, False), am.state_of("s"), None)
    assert math.isnan(get("sensor_zscore", {"sensor": "s"}))

    # Three outliers open an alert.
    for t in range(3):
        event = am.process("s", t, 5.0)
        m.observe("s", Detection(9.0, 5.0, True), am.state_of("s"), event.kind if event else None)
    assert get("sensor_alert_state", {"sensor": "s"}) == 1
    assert get("sensor_is_outlier", {"sensor": "s"}) == 1
    assert get("alerts_fired_total", {"sensor": "s"}) == 1
    assert get("points_processed_total", {"sensor": "s"}) == 4

    # Resolve, then a repeat inside the cooldown is suppressed.
    for t, z in enumerate([0, 0, 0, 0, 0, 5, 5, 5, 5], start=3):
        event = am.process("s", t, z)
        m.observe("s", Detection(1.0, z, z > 3), am.state_of("s"), event.kind if event else None)
    assert get("sensor_alert_state", {"sensor": "s"}) == 0
    assert get("alerts_suppressed_total", {"sensor": "s"}) == 1
    assert get("alerts_fired_total", {"sensor": "s"}) == 1
