"""
Real patient vital-sign charts: Heart Rate, SpO2, Systolic Blood Pressure
Sampling / transmission interval: 2 seconds (Rule: <= ~2 s)

DATA SOURCE (real, de-identified surgical patients, CC BY 4.0):
  VitalDB - Seoul National University Hospital
  Lee HC et al., "VitalDB, a high-fidelity multi-parameter vital signs
  database in surgical patients", Scientific Data 9, 279 (2022)
  https://vitaldb.net/dataset   |   https://physionet.org/content/vitaldb/1.0.0/
  Monitor: GE Solar 8000M -> numeric acquisition interval = 2 s

Tracks used:
  Solar8000/HR          heart rate (/min)
  Solar8000/PLETH_SPO2  SpO2 (%)
  Solar8000/ART_SBP     invasive arterial systolic pressure (mmHg)

Usage:
  pip install vitaldb pandas matplotlib
  python real_vitals_charts.py              # first case that has all 3 tracks
  python real_vitals_charts.py --case 12    # a specific case id
  python real_vitals_charts.py --minutes 30 # length of the window to plot
"""
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import vitaldb

TRACKS = ["Solar8000/HR", "Solar8000/PLETH_SPO2", "Solar8000/ART_SBP"]
LABELS = ["Heart rate (bpm)", "SpO2 (%)", "Systolic BP (mmHg)"]
COLORS = ["#2e7d32", "#1565c0", "#c62828"]
INTERVAL_S = 2  # the 2-second rule


def pick_case(case_id=None):
    if case_id is not None:
        return case_id
    cases = vitaldb.find_cases(TRACKS)  # cases containing all three tracks
    if len(cases) == 0:
        raise SystemExit("No case found with all three tracks.")
    return int(cases[0])


def load(case_id):
    vals = vitaldb.load_case(case_id, TRACKS, INTERVAL_S)  # one row every 2 s
    df = pd.DataFrame(vals, columns=["HR", "SpO2", "SBP"])
    df.insert(0, "time_s", np.arange(len(df)) * INTERVAL_S)
    # keep the monitored part of the case (drop leading/trailing empty rows)
    valid = df[["HR", "SpO2", "SBP"]].notna().all(axis=1)
    if valid.any():
        df = df.loc[valid.idxmax(): valid[::-1].idxmax()].reset_index(drop=True)
        df["time_s"] -= df["time_s"].iloc[0]
    # remove physiologically impossible artefacts (probe off, line flush)
    df.loc[(df.HR < 20) | (df.HR > 250), "HR"] = np.nan
    df.loc[(df.SpO2 < 50) | (df.SpO2 > 100), "SpO2"] = np.nan
    df.loc[(df.SBP < 30) | (df.SBP > 300), "SBP"] = np.nan
    return df


def check_rate(df):
    dt = np.diff(df["time_s"].values)
    print(f"Sample interval: min {dt.min()} s, max {dt.max()} s "
          f"-> rule '<= 2 s' {'OK' if dt.max() <= 2 else 'VIOLATED'}")
    for col in ["HR", "SpO2", "SBP"]:
        t = df.loc[df[col].notna(), "time_s"].values
        gap = np.diff(t).max() if len(t) > 1 else float("nan")
        print(f"  {col:5s}: {df[col].notna().mean()*100:5.1f}% filled, "
              f"largest gap between real readings = {gap:.0f} s")


def plot(df, case_id, minutes):
    w = df[df.time_s <= minutes * 60]
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    for ax, col, lab, c in zip(axes, ["HR", "SpO2", "SBP"], LABELS, COLORS):
        ax.plot(w.time_s / 60, w[col], color=c, lw=1)
        ax.set_ylabel(lab)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("Time from start of monitoring (min)")
    fig.suptitle(f"VitalDB case {case_id} - real patient data, 1 sample / {INTERVAL_S} s")
    fig.tight_layout()
    out = f"vitaldb_case{case_id}_charts.png"
    fig.savefig(out, dpi=150)
    print("Saved chart:", out)
    plt.show()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=60)
    a = ap.parse_args()

    cid = pick_case(a.case)
    data = load(cid)
    check_rate(data)
    csv = f"vitaldb_case{cid}_HR_SpO2_SBP_2s.csv"
    data.to_csv(csv, index=False)
    print("Saved data:", csv, f"({len(data)} rows)")
    plot(data, cid, a.minutes)
