"""Fit per-vital noise from a real VitalDB recording.

Looking at the real signals, each one is

    reading = slow level + small jitter + occasional spikes

We fit the two parts that a single recording measures reliably:

* spikes: samples far from their neighbours' median. We count how often
  they happen and keep their real sizes, to replay them in the simulator.
* jitter: fresh noise on every sample. With spikes removed, it shows up as
  neighbouring changes cancelling out (+2 then -2):

      cov(d[t], d[t+1]) = -jitter_sd^2    where d = change per sample

The slow level is not fitted: in this surgical case it is driven by the
operation (SBP 180 -> 98), not by how a stable patient drifts. The
simulator sets it as an explicit assumption instead.
"""
import csv
import statistics
from dataclasses import dataclass

SPIKE_WINDOW = 3     # neighbours on each side for the local median (14 s)
SPIKE_MIN_SIZE = 5   # deviation from that median that counts as a spike
# Changes larger than this are level jumps (e.g. arterial line connected),
# not jitter.
MAX_JITTER_STEP = 10


@dataclass(frozen=True)
class VitalModel:
    jitter_sd: float
    spike_rate: float    # chance that a sample is a spike
    spike_sizes: tuple   # real spike sizes, sampled at random


def read_column(csv_path, column):
    """Return a column of the CSV as floats, with None for missing values."""
    with open(csv_path, newline="") as f:
        return [float(row[column]) if row[column] else None
                for row in csv.DictReader(f)]


def split_spikes(values):
    """Return (values with spikes replaced by None, list of spike sizes)."""
    clean, sizes = list(values), []
    for i in range(SPIKE_WINDOW, len(values) - SPIKE_WINDOW):
        window = [v for v in values[i - SPIKE_WINDOW:i + SPIKE_WINDOW + 1]
                  if v is not None]
        if values[i] is None or len(window) < SPIKE_WINDOW + 2:
            continue
        deviation = values[i] - statistics.median(window)
        if abs(deviation) > SPIKE_MIN_SIZE:
            clean[i] = None
            sizes.append(deviation)
    return clean, sizes


def jitter_sd(values):
    """SD of per-sample noise, from how much neighbouring changes cancel."""
    changes = [b - a if a is not None and b is not None
               and abs(b - a) <= MAX_JITTER_STEP else None
               for a, b in zip(values, values[1:])]
    pairs = [(a, b) for a, b in zip(changes, changes[1:])
             if a is not None and b is not None]
    if len(pairs) < 2:
        raise ValueError("need more consecutive readings to fit")
    mean = statistics.fmean(d for d in changes if d is not None)
    cov = statistics.fmean([(a - mean) * (b - mean) for a, b in pairs])
    return max(-cov, 0.0) ** 0.5  # positive cov: the monitor smooths


def fit(values):
    """Fit a VitalModel from consecutive 2-second readings."""
    clean, sizes = split_spikes(values)
    n_readings = sum(v is not None for v in values)
    return VitalModel(jitter_sd=jitter_sd(clean),
                      spike_rate=len(sizes) / n_readings,
                      spike_sizes=tuple(sizes))


def fit_from_csv(csv_path, columns):
    """Fit one model per vital. `columns` maps vital name -> CSV column."""
    return {vital: fit(read_column(csv_path, col))
            for vital, col in columns.items()}
