"""Fit the per-vital fluctuation model from a real VitalDB recording.

A bedside monitor reports integer values that often repeat, then move by a
small step. We measure both behaviours on the real 2-second data:

* hold_prob: share of samples where the reading did not change.
* step_sd:   spread of the non-zero changes. Estimated with the median
             absolute value (x 1.4826) so that artefact jumps in the real
             data (e.g. arterial line flushes) do not inflate it.
"""
import csv
import statistics
from dataclasses import dataclass

MAD_TO_SD = 1.4826  # converts a median absolute deviation to a normal SD


@dataclass(frozen=True)
class VitalModel:
    hold_prob: float
    step_sd: float


def read_column(csv_path, column):
    """Return a column of the CSV as floats, with None for missing values."""
    with open(csv_path, newline="") as f:
        return [float(row[column]) if row[column] else None
                for row in csv.DictReader(f)]


def fit(values):
    """Fit a VitalModel from consecutive 2-second readings."""
    steps = [b - a for a, b in zip(values, values[1:])
             if a is not None and b is not None]
    if not steps:
        raise ValueError("need at least two consecutive readings to fit")
    moves = [abs(s) for s in steps if s != 0]
    hold_prob = 1 - len(moves) / len(steps)
    step_sd = MAD_TO_SD * statistics.median(moves) if moves else 0.0
    return VitalModel(hold_prob=hold_prob, step_sd=step_sd)


def fit_from_csv(csv_path, columns):
    """Fit one model per vital. `columns` maps vital name -> CSV column."""
    return {vital: fit(read_column(csv_path, col))
            for vital, col in columns.items()}
