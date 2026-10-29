"""Live dashboard. Reads what the simulator writes to SQLite.

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


def vital_chart(df, vital):
    """Line per patient, with the normal range as a shaded band."""
    low, high = NORMAL_RANGES[vital]
    scale = alt.Scale(zero=False)
    band = (alt.Chart(pd.DataFrame({"low": [low], "high": [high]}))
            .mark_rect(color="green", opacity=0.08)
            .encode(y=alt.Y("low:Q", scale=scale), y2="high:Q"))
    lines = (alt.Chart(df).mark_line()
             .encode(x=alt.X("time:T", title=None,
                             axis=alt.Axis(format="%H:%M:%S")),
                     y=alt.Y(f"{vital}:Q", title=VITALS[vital], scale=scale),
                     color=alt.Color("patient_id:N", title="Patient")))
    return (band + lines).properties(height=220)


def show_latest(df):
    latest = (df.sort_values("ts").groupby("patient_id").tail(1)
              .sort_values("patient_id"))
    for col, (_, row) in zip(st.columns(len(latest)), latest.iterrows()):
        with col.container(border=True):
            st.subheader(row.patient_id)
            for vital, label in VITALS.items():
                # NaN means the sensor sent nothing (dropout)
                value = None if pd.isna(row[vital]) else int(row[vital])
                st.metric(label, value)


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


@st.fragment(run_every=SAMPLE_INTERVAL_S)
def live_view():
    conn = storage.connect()
    try:
        rows = storage.readings_since(conn, time.time() - WINDOW_S)
        events = storage.latest_events(conn)
    finally:
        conn.close()

    if not rows:
        st.info("No readings yet. Start the simulator: "
                "`python -m monitor.simulator`")
        return

    df = pd.DataFrame(rows)
    df["time"] = [datetime.fromtimestamp(t) for t in df.ts]
    show_latest(df)
    for vital in VITALS:
        st.altair_chart(vital_chart(df, vital), use_container_width=True)
    show_events(events)


st.set_page_config(page_title="Vital Signs Monitor", layout="wide")
st.title("Vital Signs Monitor")
live_view()
