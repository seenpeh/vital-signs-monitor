"""Edge processing: clean each vital, then summarise the last 5 minutes.

Filter: rolling median of the last FILTER_WINDOW readings. A glitch shorter
than half the window can never be the middle value, so it disappears; a
real change is accepted once it fills more than half the window (4 samples
= 8 s), close to the 10-15 s alarm delay real monitors use.

It only looks at past samples, so it can run live as readings arrive.
"""
import statistics
from collections import deque

from monitor.config import NORMAL_RANGES, SAMPLE_INTERVAL_S

FILTER_WINDOW = 7                               # samples (14 s)
MIN_PRESENT = FILTER_WINDOW // 2 + 1            # majority must be real
STATS_WINDOW = int(5 * 60 / SAMPLE_INTERVAL_S)  # samples (5 min)
MIN_TREND_SAMPLES = int(60 / SAMPLE_INTERVAL_S)  # samples (1 min)


def rolling_median(values):
    """Median of a full window, or None if it can't be trusted yet.

    Before the window is full (start-up) or when most readings are missing
    (probe off), a short glitch could become the middle value, so we
    report "no reliable value" instead.
    """
    present = [v for v in values if v is not None]
    if len(values) < FILTER_WINDOW or len(present) < MIN_PRESENT:
        return None
    return statistics.median(present)


def window_stats(values):
    """Return (mean, sd, trend per minute) of the non-missing values.

    The trend is the slope of a straight line fitted through the window.
    Each part is None when there are too few values to compute it; a slope
    over a few seconds swings wildly, so the trend needs a minute of data.
    """
    points = [(i * SAMPLE_INTERVAL_S / 60, v)
              for i, v in enumerate(values) if v is not None]
    if not points:
        return None, None, None
    minutes, ys = zip(*points)
    mean = statistics.fmean(ys)
    sd = statistics.stdev(ys) if len(ys) >= 2 else None
    if len(ys) < MIN_TREND_SAMPLES:
        return mean, sd, None
    return mean, sd, statistics.linear_regression(minutes, ys).slope


class EdgeProcessor:
    """Keeps one patient's recent history and processes each new reading."""

    def __init__(self):
        self.recent = {v: deque(maxlen=FILTER_WINDOW) for v in NORMAL_RANGES}
        self.cleaned = {v: deque(maxlen=STATS_WINDOW) for v in NORMAL_RANGES}

    def process(self, reading):
        """Raw reading in; cleaned values and 5-minute stats out."""
        out = {}
        for vital in NORMAL_RANGES:
            self.recent[vital].append(reading[vital])
            clean = rolling_median(self.recent[vital])
            self.cleaned[vital].append(clean)
            mean, sd, trend = window_stats(self.cleaned[vital])
            out[vital] = clean
            out[f"{vital}_mean"] = mean
            out[f"{vital}_sd"] = sd
            out[f"{vital}_trend"] = trend
        return out
