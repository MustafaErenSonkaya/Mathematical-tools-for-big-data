"""Shared helpers for the tests."""

import numpy as np
import pytest

from src.alerts import AlertManager
from src.detector import OnlineDetector


def normal_values(n, seed, mean=50.0, std=1.0):
    """n well-behaved Gaussian values. Seeded so tests are deterministic."""
    return list(np.random.default_rng(seed).normal(mean, std, n))


def run_pipeline(values, detector, manager, sensor="s", t0=0.0, interval=1.0):
    """Push values through detector + alert manager (1 point per second). Return events."""
    events = []
    for i, v in enumerate(values):
        d = detector.update(v)
        e = manager.process(sensor, t0 + i * interval, d.z, v)
        if e is not None:
            events.append(e)
    return events


@pytest.fixture
def detector():
    return OnlineDetector(window_size=100, warmup=30, threshold=3.0)


@pytest.fixture
def manager():
    return AlertManager(enter_z=3.0, exit_z=2.0, k_consecutive=3, m_consecutive=5, cooldown_seconds=60)
