"""Shared constants: vital-sign names, normal ranges and file paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_CSV = ROOT / "real_patient_data" / "vitaldb_case1_HR_SpO2_SBP_2s.csv"
DB_PATH = ROOT / "data" / "monitor.db"

SAMPLE_INTERVAL_S = 2.0

# Normal ranges from the task statement.
NORMAL_RANGES = {
    "hr": (60, 100),    # heart rate, BPM
    "spo2": (95, 100),  # blood oxygen saturation, %
    "sbp": (90, 120),   # systolic blood pressure, mmHg
}

# Column in the real CSV that each vital is fitted from.
REAL_DATA_COLUMNS = {"hr": "HR", "spo2": "SpO2", "sbp": "SBP"}
