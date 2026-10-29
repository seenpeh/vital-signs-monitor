"""Live dashboard. Reads what the pipeline writes to SQLite.

Run:  streamlit run dashboard.py
"""
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from monitor import storage
from monitor.config import NORMAL_RANGES, SAMPLE_INTERVAL_S

WINDOW_S = 5 * 60  # how much history the charts show
VITALS = {"hr": "Heart rate (BPM)", "spo2": "SpO2 (%)",
          "sbp": "Systolic BP (mmHg)"}


def vital_chart(raw, clean, vital):
    """Per patient: faint dashed raw line, solid cleaned line, normal band."""
    low, high = NORMAL_RANGES[vital]
    scale = alt.Scale(zero=False)
    x = alt.X("time:T", title=None, axis=alt.Axis(format="%H:%M:%S"))
    color = alt.Color("patient_id:N", title="Patient")
    band = (alt.Chart(pd.DataFrame({"low": [low], "high": [high]}))
            .mark_rect(color="green", opacity=0.08)
            .encode(y=alt.Y("low:Q", scale=scale), y2="high:Q"))
    raw_lines = (alt.Chart(raw)
                 .mark_line(opacity=0.35, strokeDash=[3, 3], strokeWidth=1)
                 .encode(x=x, y=alt.Y(f"{vital}:Q", scale=scale), color=color))
    clean_lines = (alt.Chart(clean).mark_line(strokeWidth=2.5)
                   .encode(x=x, color=color,
                           y=alt.Y(f"{vital}:Q", title=VITALS[vital],
                                   scale=scale)))
    return (band + raw_lines + clean_lines).properties(height=220)


def fmt(value, digits=1):
    return "—" if pd.isna(value) else f"{value:.{digits}f}"


def show_latest(clean):
    latest = (clean.sort_values("ts").groupby("patient_id").tail(1)
              .sort_values("patient_id"))
    for col, (_, row) in zip(st.columns(len(latest)), latest.iterrows()):
        with col.container(border=True):
            st.subheader(row.patient_id)
            for vital, label in VITALS.items():
                trend = row[f"{vital}_trend"]
                st.metric(label, fmt(row[vital], 0),
                          delta=(None if pd.isna(trend)
                                 else f"{trend:+.1f}/min"),
                          delta_color="off")
                st.caption(f"5 min: {fmt(row[f'{vital}_mean'])} "
                           f"± {fmt(row[f'{vital}_sd'])}")


def show_events(events):
    st.subheader("Injected events (simulator ground truth)")
    if not events:
        st.caption("None yet.")
        return
    table = pd.DataFrame(events)
    table["start"] = [datetime.fromtimestamp(t).strftime("%H:%M:%S")
                      for t in table.start_ts]
    table["duration_s"] = (table.end_ts - table.start_ts).round().astype(int)
    st.dataframe(table[["start", "patient_id", "kind", "vital", "duration_s"]],
                 hide_index=True, use_container_width=True)


def with_time(rows):
    df = pd.DataFrame(rows)
    df["time"] = [datetime.fromtimestamp(t) for t in df.ts]
    return df


@st.fragment(run_every=SAMPLE_INTERVAL_S)
def live_view():
    conn = storage.connect()
    try:
        since = time.time() - WINDOW_S
        raw_rows = storage.readings_since(conn, since)
        clean_rows = storage.processed_since(conn, since)
        events = storage.latest_events(conn)
    finally:
        conn.close()

    if not clean_rows:
        st.info("No readings yet. Start the pipeline: `python -m monitor.run`")
        return

    raw, clean = with_time(raw_rows), with_time(clean_rows)
    show_latest(clean)
    st.caption("Solid line: cleaned by the edge filter. "
               "Dashed: raw sensor reading.")
    for vital in VITALS:
        st.altair_chart(vital_chart(raw, clean, vital),
                        use_container_width=True)
    show_events(events)


st.set_page_config(page_title="Vital Signs Monitor", layout="wide")
st.title("Vital Signs Monitor")
live_view()
