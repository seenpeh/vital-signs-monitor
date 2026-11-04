"""Shared constants: vital-sign names, normal ranges and file paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_CSV = ROOT / "real_patient_data" / "vitaldb_case1_HR_SpO2_SBP_2s.csv"
DB_PATH = ROOT / "data" / "monitor.db"

SAMPLE_INTERVAL_S = 2.0

# Normal ranges: the bands NEWS2 scores 0 (see monitor/triage.py).
NORMAL_RANGES = {
    "hr": (51, 90),     # heart rate, BPM
    "spo2": (96, 100),  # blood oxygen saturation, %
    "sbp": (111, 219),  # systolic blood pressure, mmHg
}

# Column in the real CSV that each vital is fitted from.
REAL_DATA_COLUMNS = {"hr": "HR", "spo2": "SpO2", "sbp": "SBP"}
