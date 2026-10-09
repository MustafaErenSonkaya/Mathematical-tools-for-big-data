import json
import time
from pathlib import Path

from src.main import load_config, run
from src.notifier import JsonlNotifier
from src.alerts import AlertEvent
from src.stream import SensorSpec, stream_readings

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def test_fast_mode_is_fast_and_uses_simulated_clock():
    specs = [SensorSpec("a", 0, 1), SensorSpec("b", 10, 1), SensorSpec("c", 5, 1)]
    start = time.perf_counter()
    readings = list(stream_readings(specs, interval_seconds=1.0, fast=True, max_ticks=1000, start_time=0))
    assert time.perf_counter() - start < 2  # 1000 simulated seconds, near-instant
    assert len(readings) == 3000  # one reading per sensor per tick
    assert readings[-1].timestamp == 999.0


def test_same_seed_same_data():
    specs = [SensorSpec("a", 0, 1)]
    run1 = [r.value for r in stream_readings(specs, fast=True, max_ticks=100, seed=7)]
    run2 = [r.value for r in stream_readings(specs, fast=True, max_ticks=100, seed=7)]
    assert run1 == run2


def test_all_anomaly_types_get_injected():
    specs = [SensorSpec("a", 0, 1)]
    labels = {r.anomaly for r in stream_readings(specs, fast=True, max_ticks=20000, anomaly_probability=0.01, seed=1)}
    assert {"spike", "shift", "drift"} <= labels


def test_jsonl_notifier_writes_one_line_per_event(tmp_path):
    path = tmp_path / "alerts.jsonl"
    n = JsonlNotifier(path)
    e = AlertEvent("ALERT", "x-1", "x", 0.0, 0.0, 4.0, 1.0, 4.0)
    n.send(e)
    n.send(e)
    n.close()
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["incident_id"] == "x-1"


def test_full_run_from_config(tmp_path, capsys):
    cfg = load_config(CONFIG)
    cfg["notifier"]["jsonl_path"] = str(tmp_path / "alerts.jsonl")
    manager, events, metrics = run(cfg, fast=True, max_ticks=3000, serve_metrics=False)

    # Every ALERT is followed by a RESOLVED, except possibly one still open per sensor.
    assert events["ALERT"] > 0
    assert 0 <= events["ALERT"] - events["RESOLVED"] <= len(manager.sensors)
    lines = (tmp_path / "alerts.jsonl").read_text().splitlines()
    assert len(lines) == events["ALERT"] + events["RESOLVED"]

    # The metrics agree with what actually happened.
    get = metrics.registry.get_sample_value
    assert sum(get("alerts_fired_total", {"sensor": s}) for s in manager.sensors) == events["ALERT"]
    for s, state in manager.sensors.items():
        assert get("points_processed_total", {"sensor": s}) == 3000
        assert get("alerts_suppressed_total", {"sensor": s}) == state.suppressed_count
