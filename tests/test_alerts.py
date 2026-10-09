"""
The three required guarantees, tested end to end (detector + alert manager),
plus a few unit tests of the state machine with hand-written z-scores.
"""

import pytest

from src.alerts import AlertManager, State
from src.detector import OnlineDetector

from conftest import normal_values, run_pipeline


# ---------------------------------------------------------------- required tests
def test_sustained_20_point_anomaly_gives_exactly_one_alert_and_one_resolved(detector, manager):
    # 200 normal points, a 20-point level shift of +8 sigma, then 100 normal points.
    shift = [v + 8.0 for v in normal_values(20, seed=2)]
    values = normal_values(200, seed=1) + shift + normal_values(100, seed=3)

    events = run_pipeline(values, detector, manager)

    assert [e.kind for e in events] == ["ALERT", "RESOLVED"]
    alert, resolved = events
    assert alert.incident_id == resolved.incident_id  # same incident
    assert alert.started_at == 200  # first shifted point (t = index, 1 point/s)
    assert alert.timestamp == 202  # fired on the K=3rd outlier
    assert 20 <= resolved.duration_seconds <= 30  # ~20 s anomaly + 5 calm points
    assert resolved.peak_z > 5  # the shift was big, the peak must reflect it


def test_single_spike_does_not_alert_when_k_is_3(detector, manager):
    values = normal_values(200, seed=4)
    values[150] += 10.0  # one huge spike

    events = run_pipeline(values, detector, manager)

    assert events == []


def test_cooldown_suppresses_repeat_alert(detector, manager):
    # Incident 1, then the problem returns 20 s after RESOLVED (inside the 60 s cooldown).
    values = (
        normal_values(200, seed=5)
        + [v + 8.0 for v in normal_values(15, seed=6)]  # incident 1
        + normal_values(25, seed=7)  # resolves after 5 calm points
        + [v + 8.0 for v in normal_values(15, seed=8)]  # comes back: in cooldown
        + normal_values(50, seed=9)
    )

    events = run_pipeline(values, detector, manager)

    assert [e.kind for e in events] == ["ALERT", "RESOLVED"]
    assert manager.state_of("s").suppressed_count == 1  # counted once, not 13 times


# ------------------------------------------------------ state machine unit tests
def feed(manager, zs, sensor="s", t0=0):
    """Feed raw z-scores (1 per second). Return the list of events."""
    out = []
    for i, z in enumerate(zs):
        e = manager.process(sensor, t0 + i, z)
        if e:
            out.append(e)
    return out


def test_needs_k_consecutive_outliers(manager):
    # Two outliers, a normal point, two outliers: never 3 in a row.
    assert feed(manager, [0, 4, 4, 0, 4, 4, 0]) == []
    assert feed(manager, [4, 4, 4], t0=100)[0].kind == "ALERT"


def test_hysteresis_prevents_flapping(manager):
    # After entering, z hovers between 2 and 3: neither "outlier" nor "calm".
    # With a single threshold at 3 this would flap; here it must stay ALERTING.
    events = feed(manager, [4, 4, 4] + [2.5, 3.5, 2.9, 2.1] * 10)
    assert [e.kind for e in events] == ["ALERT"]
    assert manager.state_of("s").state is State.ALERTING


def test_calm_streak_must_be_consecutive(manager):
    # 4 calm, 1 not calm, then 5 calm: resolves only at the end.
    zs = [4, 4, 4] + [0, 0, 0, 0, 2.5] + [0, 0, 0, 0, 0]
    events = feed(manager, zs)
    assert [e.kind for e in events] == ["ALERT", "RESOLVED"]
    assert events[1].timestamp == len(zs) - 1


def test_resolved_reports_duration_and_signed_peak(manager):
    events = feed(manager, [0, -4, -9, -5, -3.5, 0, 0, 0, 0, 0])
    alert, resolved = events
    assert alert.started_at == 1
    assert resolved.duration_seconds == 9 - 1
    assert resolved.peak_z == -9  # most extreme value, sign preserved


def test_new_alert_after_cooldown_gets_new_id(manager):
    first = feed(manager, [4, 4, 4, 0, 0, 0, 0, 0])  # resolved at t=7, cooldown until 67
    later = feed(manager, [4, 4, 4], t0=100)
    assert later[0].kind == "ALERT"
    assert later[0].incident_id != first[0].incident_id


def test_alert_opens_if_streak_outlives_cooldown(manager):
    feed(manager, [4, 4, 4, 0, 0, 0, 0, 0])  # resolved at t=7, cooldown until 67
    # Outliers from t=60 to t=79: suppressed first, then alert once cooldown ends.
    events = feed(manager, [4] * 20, t0=60)
    assert [e.kind for e in events] == ["ALERT"]
    assert events[0].timestamp == 67
    assert manager.state_of("s").suppressed_count == 1


def test_sensors_are_independent(manager):
    for t in range(3):
        manager.process("cpu", t, 5.0)
        manager.process("temp", t, 0.0)
    assert manager.state_of("cpu").state is State.ALERTING
    assert manager.state_of("temp").state is State.NORMAL


def test_warmup_none_is_ignored(manager):
    assert feed(manager, [None, None, None]) == []


def test_invalid_hysteresis_rejected():
    with pytest.raises(ValueError):
        AlertManager(enter_z=2.0, exit_z=3.0)
