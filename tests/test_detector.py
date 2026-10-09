import numpy as np
import pytest

from src.detector import OnlineDetector

from conftest import normal_values


def test_warmup_returns_no_score():
    det = OnlineDetector(window_size=50, warmup=10)
    results = [det.update(v) for v in normal_values(10, seed=0)]
    assert all(r.z is None and not r.is_outlier for r in results)
    assert det.update(50.0).z is not None  # 11th point gets scored


def test_z_is_computed_before_adding_point():
    det = OnlineDetector(window_size=10, warmup=3)
    for v in [1.0, 2.0, 3.0]:
        det.update(v)
    d = det.update(2.5)
    # Stats of [1, 2, 3] only: mean 2, sample std 1  ->  z = 0.5
    assert d.center == pytest.approx(2.0)
    assert d.spread == pytest.approx(1.0)
    assert d.z == pytest.approx(0.5)


def test_outliers_do_not_pollute_window():
    det = OnlineDetector(window_size=100, warmup=30)
    for v in normal_values(100, seed=1):
        det.update(v)
    before = list(det.window)
    d = det.update(1000.0)
    assert d.is_outlier
    assert list(det.window) == before  # spike was kept out


def test_sustained_shift_stays_detected():
    # Because outliers are excluded, z stays high for the whole shift.
    det = OnlineDetector(window_size=100, warmup=30)
    for v in normal_values(100, seed=2):
        det.update(v)
    shifted = [det.update(v + 8.0) for v in normal_values(30, seed=3)]
    assert all(d.is_outlier for d in shifted)


def test_robust_mode_ignores_contaminated_window():
    # A window with 5% junk values: mean/std get distorted, median/MAD don't.
    data = normal_values(95, seed=4) + [500.0] * 5
    classic = OnlineDetector(window_size=100, warmup=100, method="zscore")
    robust = OnlineDetector(window_size=100, warmup=100, method="robust")
    for v in data:
        classic.update(v)
        robust.update(v)

    probe = 56.0  # 6 sigma above the true mean of 50
    assert not classic.update(probe).is_outlier  # std inflated by the junk -> missed
    assert robust.update(probe).is_outlier  # robust stats still catch it


def test_constant_signal_does_not_divide_by_zero():
    det = OnlineDetector(window_size=20, warmup=5)
    for _ in range(10):
        d = det.update(1.0)
    assert d.z == 0.0
    assert np.isfinite(det.update(1.1).z)


def test_bad_method_rejected():
    with pytest.raises(ValueError):
        OnlineDetector(method="magic")
