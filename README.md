# Vital Signs Monitor

A small IoMT prototype. It simulates vital signs for several patients, filters out noise and outliers at the edge, sorts each patient into a risk level (Normal / Warning / Critical), stores alerts in a database, and shows everything on a live dashboard.

> Status: step 1 is done (patient simulator plus live dashboard). The filtering and alert engine come next.

## Quick start

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the two parts in two terminals:

```bash
python -m monitor.simulator            # 3 patients, one reading every 2 s
streamlit run dashboard.py             # opens http://localhost:8501
```

Simulator options: `--patients N`, `--interval SECONDS`, `--seed N` (the same seed gives the same readings and the same events).

Run the tests:

```bash
python -m unittest
```

## Architecture

```
monitor/simulator.py  ──writes──▶  data/monitor.db (SQLite)  ◀──reads──  dashboard.py
        │                              readings, events
        └─ monitor/model.py  (fits the noise model from real data)
```

| Layer | File | Role |
|---|---|---|
| Simulation | `monitor/simulator.py`, `monitor/model.py` | One `Patient` per bedside node. Produces HR, SpO2 and SBP every 2 s. |
| Storage | `monitor/storage.py` | SQLite in WAL mode, so the dashboard can read while the simulator writes. |
| UI | `dashboard.py` | Streamlit. Refreshes every 2 s and shows the last 5 minutes. |
| Config | `monitor/config.py` | Normal ranges, paths and the sampling interval. |

The simulator and the dashboard are separate processes that share only the database. Either one can be restarted without touching the other.

## Simulation model

### Built from real data

Vital-sign behaviour is fitted from a real recording: **VitalDB case 1**. It is about 3 hours of HR, SpO2 and invasive arterial SBP, sampled every 2 s (`real_patient_data/`). The fit measures two things for each vital:

| Vital | `hold_prob` (reading did not change) | `step_sd` (size of a change) |
|---|---|---|
| HR | 0.74 | 1.48 BPM |
| SpO2 | 0.97 | 1.48 % |
| SBP | 0.17 | 2.97 mmHg |

`step_sd` uses the median absolute change × 1.4826, not the plain standard deviation. That way the 50 artefact jumps in the real SBP trace (arterial line flushes) don't make the simulated signal jumpy.

### How a reading is generated

Each vital is a **mean-reverting random walk** around the patient's own baseline:

```
with probability hold_prob:   keep the last value
otherwise:                    level += r * (baseline - level) + N(0, step_sd)
reading = round(level)        (monitors report whole numbers; SpO2 is capped at 100)
```

The pull `r` is set so that a stable patient's readings spread by a chosen SD (HR 4, SpO2 1, SBP 4) around baseline. This keeps them inside the task's normal ranges: **HR 60–100 BPM, SpO2 95–100 %, SBP 90–120 mmHg**.

### Injected events

| Kind | What happens | Duration | Should the system alarm? |
|---|---|---|---|
| `spo2_drop` | SpO2 falls 10–15 points (to about 85 %). HR rises by about 25 to compensate. | 60–120 s | Yes, critical |
| `arrhythmia` | HR jumps about 35 BPM and becomes irregular (±12 per sample) | 30–90 s | Yes |
| `spike` | One sample of one vital jumps (motion artefact) | 1 sample | No, filter it out |
| `dropout` | One vital sends nothing (probe off) | 2–5 samples | No |

Each patient averages one clinical event every 3 minutes and one artefact every 30 seconds. Every event is saved to the `events` table as ground truth. That table lets us measure false alarms and missed alarms later. The detector itself must never read it.

## Assumptions and design decisions

| Decision | Choice | Why |
|---|---|---|
| Source of realism | Fit step statistics from real data rather than replay it | Only one real case exists, and it is a surgical patient (HR up to 120, SBP up to 180). A fitted model keeps the real texture, stays in normal ranges, runs forever, and gives each patient a different baseline. |
| Transport between nodes and the server | Shared SQLite database | No broker to install, and real layer separation. The alerts in step 3 need a database anyway. MQTT would be the production choice. |
| Anomaly timing | Random with a fixed seed | Realistic, yet every run is identical, which is what the report and the demo need. |
| SpO2 drop also raises HR | Yes | Low oxygen makes the heart speed up. That also makes the task's "SpO2 < 90 and HR up = Critical" rule testable. |
| One event at a time per patient | Yes | Keeps the ground truth unambiguous. Can be relaxed later to test artefacts that land during a crisis. |
| Sampling interval | 2 s | Taken from the task statement. It is also the real monitor's (GE Solar 8000M) numeric output rate. |

## Data source

The real patient data comes from **VitalDB**, a de-identified database of surgical patients at Seoul National University Hospital, published under the CC BY 4.0 licence:

> Lee HC, Park Y, Yoon SB, Yang SM, Park D, Jung CW. *VitalDB, a high-fidelity multi-parameter vital signs database in surgical patients.* Scientific Data 9, 279 (2022). https://doi.org/10.1038/s41597-022-01411-5
> Dataset: https://vitaldb.net/dataset · https://physionet.org/content/vitaldb/1.0.0/

`real_patient_data/real_vitals_charts.py` downloads the case and exports the CSV. Running it needs `pip install vitaldb matplotlib`. It is not needed to run the project.
