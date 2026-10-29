"""Plot real VitalDB data next to a simulated patient, to check realism.

Run:  python -m monitor.compare     (saves docs/real_vs_sim.png)

Columns: real trace | simulated trace | distribution of 2-second changes.
Absolute levels differ on purpose (the real case is a surgical patient);
what should match is the texture: how often values hold, how big a move is.
"""
from collections import Counter

import matplotlib.pyplot as plt

from monitor.config import (REAL_DATA_COLUMNS, REAL_DATA_CSV, ROOT,
                            SAMPLE_INTERVAL_S)
from monitor.model import read_column
from monitor.simulator import make_patients

WINDOW_MIN = 10
REAL_START_MIN = 20  # a calm stretch of the real case, after surgical events
LABELS = {"hr": "HR (BPM)", "spo2": "SpO2 (%)", "sbp": "SBP (mmHg)"}
OUT = ROOT / "docs" / "real_vs_sim.png"


def steps(values):
    return [b - a for a, b in zip(values, values[1:])
            if a is not None and b is not None]


def main():
    n = int(WINDOW_MIN * 60 / SAMPLE_INTERVAL_S)
    start = int(REAL_START_MIN * 60 / SAMPLE_INTERVAL_S)
    real = {v: read_column(REAL_DATA_CSV, col)
            for v, col in REAL_DATA_COLUMNS.items()}

    patient = make_patients(1, seed=42)[0]
    patient.p_clinical = patient.p_artifact = 0  # compare the quiet behaviour
    sim_rows = [patient.step()[0] for _ in range(len(real["hr"]))]
    sim = {v: [r[v] for r in sim_rows] for v in LABELS}

    minutes = [i * SAMPLE_INTERVAL_S / 60 for i in range(n)]
    fig, axes = plt.subplots(3, 3, figsize=(15, 9),
                             gridspec_kw={"width_ratios": [2, 2, 1]})
    for row, vital in zip(axes, LABELS):
        row[0].plot(minutes, real[vital][start:start + n], color="tab:red")
        row[1].plot(minutes, sim[vital][:n], color="tab:blue")
        row[0].set_ylabel(LABELS[vital])

        # step histograms use the full series, not just the 10-min window
        for series, color, name in ((real[vital], "tab:red", "real"),
                                    (sim[vital], "tab:blue", "simulated")):
            counts = Counter(s for s in steps(series) if abs(s) <= 10)
            total = sum(counts.values())
            xs = sorted(counts)
            row[2].plot(xs, [counts[x] / total for x in xs], "o-",
                        color=color, label=name, markersize=3)
        row[2].set_yscale("log")
        for ax in row:
            ax.grid(alpha=0.3)

    axes[0][0].set_title(f"Real (VitalDB case 1, min {REAL_START_MIN}-"
                         f"{REAL_START_MIN + WINDOW_MIN})")
    axes[0][1].set_title("Simulated (seed 42, no events)")
    axes[0][2].set_title("2-s change distribution")
    axes[0][2].legend()
    for ax in axes[2][:2]:
        ax.set_xlabel("minutes")
    axes[2][2].set_xlabel("change between samples")
    fig.tight_layout()
    OUT.parent.mkdir(exist_ok=True)
    fig.savefig(OUT, dpi=120)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
