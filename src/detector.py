"""
Online (streaming) outlier detector for ONE sensor.

"Online" means we see each value once, in order, and must decide immediately.
We can't look at the whole dataset, only at what we have seen so far.

Idea: keep the last N "normal" values in a rolling window. For a new value x,
ask: "how many standard deviations is x away from what is normal right now?"

    z = (x - center) / spread

    * classic mode: center = mean,   spread = standard deviation
    * robust  mode: center = median, spread = 1.4826 * MAD
      (MAD = median absolute deviation. Unlike mean/std, median/MAD barely
       move when a few extreme values sneak into the window. The 1.4826 factor
       makes MAD comparable to std for normally distributed data.)

Three important details:
  1. The z-score is computed BEFORE x is added to the window. Otherwise x
     would be compared against statistics it already influenced, which
     shrinks its own z-score.
  2. Warmup: with only a few points the statistics are meaningless, so we
     just collect data until we have `warmup` points.
  3. Outliers are NOT added to the window. If a 20-point anomaly were added,
     the window would "learn" the anomaly as the new normal: the z-scores
     would shrink and the alert would end while the problem is still there.
     Trade-off: a *permanent* level change stays flagged until someone looks
     at it. For an alerting system that is usually what you want.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

# 1.4826 * MAD estimates the standard deviation for Gaussian data.
MAD_TO_STD = 1.4826


@dataclass(frozen=True)
class Detection:
    """Result of checking one value."""

    value: float
    z: float | None  # None while warming up (we can't judge yet)
    is_outlier: bool
    center: float | None = None  # mean or median used for the z-score
    spread: float | None = None  # std or scaled MAD used for the z-score


class OnlineDetector:
    def __init__(
        self,
        window_size: int = 100,
        warmup: int = 30,
        threshold: float = 3.0,
        method: str = "zscore",
        min_spread: float = 1e-9,
    ):
        if method not in ("zscore", "robust"):
            raise ValueError(f"method must be 'zscore' or 'robust', got {method!r}")
        if not 2 <= warmup <= window_size:
            raise ValueError("warmup must be between 2 and window_size")

        self.window_size = window_size
        self.warmup = warmup
        self.threshold = threshold
        self.method = method
        # Guard against division by zero when all values in the window are equal.
        self.min_spread = min_spread
        # A deque with maxlen drops the oldest item automatically: a rolling window.
        self.window: deque[float] = deque(maxlen=window_size)

    @property
    def is_warm(self) -> bool:
        return len(self.window) >= self.warmup

    def _stats(self) -> tuple[float, float]:
        """Return (center, spread) of the current window."""
        data = np.asarray(self.window)
        if self.method == "robust":
            center = float(np.median(data))
            spread = MAD_TO_STD * float(np.median(np.abs(data - center)))
        else:
            center = float(data.mean())
            spread = float(data.std(ddof=1))  # ddof=1: sample standard deviation
        return center, max(spread, self.min_spread)

    def update(self, value: float) -> Detection:
        """Score `value` against the window, then (maybe) add it to the window."""
        # Warmup: just collect data, no verdict yet.
        if not self.is_warm:
            self.window.append(value)
            return Detection(value=value, z=None, is_outlier=False)

        # 1) Score against the window as it is NOW (before adding the value).
        center, spread = self._stats()
        z = (value - center) / spread
        is_outlier = abs(z) > self.threshold

        # 2) Only normal points are allowed to shape what "normal" means.
        if not is_outlier:
            self.window.append(value)

        return Detection(value=value, z=z, is_outlier=is_outlier, center=center, spread=spread)
