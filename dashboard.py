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
LEVEL_COLORS = {"normal": "green", "warning": "orange", "critical": "red"}

# Card animations, pure CSS. The rolling number animates a registered
# integer property shown through a CSS counter. All motion is skipped
# when the viewer's system asks for reduced motion.
CARD_CSS = r"""<style>
@property --n { syntax: '\3c integer\3e'; initial-value: 0; inherits: false; }
.vital-label { font-size: 0.875rem; opacity: 0.8; }
.vital-value { font-size: 2.25rem; line-height: 1.25; }
.vital-sub { font-size: 0.8rem; opacity: 0.65; margin-bottom: 0.75rem; }
.roll { --n: var(--to); counter-reset: n var(--n);
        font-variant-numeric: tabular-nums; }
.roll::after { content: counter(n); }
.heart { display: inline-block; color: #e5484d; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden;
           clip: rect(0 0 0 0); white-space: nowrap; }
@keyframes roll { from { --n: var(--from); } to { --n: var(--to); } }
@keyframes beat { 0%, 30%, 100% { transform: scale(1); }
                  15% { transform: scale(1.35); } }
@keyframes flash { 50% { box-shadow: 0 0 0 4px rgba(229, 72, 77, 0.5); } }
@media (prefers-reduced-motion: no-preference) {
  .roll { animation: roll 0.8s ease-out; }
  .heart { animation: beat var(--beat) ease-in-out infinite; }
}
@media (prefers-reduced-motion: reduce) {
  [class*="st-key-card_"] { animation: none !important; }
}
</style>"""


def vital_chart(df, vital, title, domain):
    """Line per patient with the normal range as a shaded band."""
    low, high = NORMAL_RANGES[vital]
    scale = alt.Scale(domain=domain)
    band = (alt.Chart(pd.DataFrame({"low": [low], "high": [high]}))
            .mark_rect(color="green", opacity=0.08)
            .encode(y=alt.Y("low:Q", scale=scale), y2="high:Q"))
    lines = (alt.Chart(df).mark_line()
             .encode(x=alt.X("time:T", title=None,
                             axis=alt.Axis(format="%H:%M:%S")),
                     y=alt.Y(f"{vital}:Q", title=VITALS[vital], scale=scale),
                     color=alt.Color("patient_id:N", title="Patient")))
    return (band + lines).properties(height=220, title=title)


def show_charts(raw, clean, vital):
    """Raw and clean side by side, on the same y scale so they compare."""
    low, high = NORMAL_RANGES[vital]
    domain = [min(raw[vital].min(), clean[vital].min(), low),
              max(raw[vital].max(), clean[vital].max(), high)]
    left, right = st.columns(2)
    left.altair_chart(vital_chart(raw, vital, "Raw sensor", domain),
                      use_container_width=True)
    right.altair_chart(vital_chart(clean, vital, "Cleaned by edge filter",
                                   domain), use_container_width=True)


def fmt(value, digits=1):
    return "—" if pd.isna(value) else f"{value:.{digits}f}"


def vital_html(patient_id, vital, label, row):
    """One vital on a card: label, rolling number, trend and 5-min stats."""
    value, trend = row[vital], row[f"{vital}_trend"]
    if pd.isna(value):
        number = "—"
    else:
        new = round(value)
        # roll from the value shown on the previous refresh
        old = st.session_state.get(f"{patient_id}_{vital}", new)
        st.session_state[f"{patient_id}_{vital}"] = new
        number = (f'<span class="roll" aria-hidden="true" '
                  f'style="--from:{old};--to:{new}"></span>'
                  f'<span class="sr-only">{new}</span>')
    heart = ""
    if vital == "hr" and not pd.isna(value) and value > 0:
        # one beat per heartbeat: 60 / BPM seconds
        heart = (f' <span class="heart" aria-hidden="true" '
                 f'style="--beat:{60 / value:.2f}s">♥</span>')
    trend_text = "" if pd.isna(trend) else f"{trend:+.1f}/min · "
    return (f'<div class="vital-label">{label}{heart}</div>'
            f'<div class="vital-value">{number}</div>'
            f'<div class="vital-sub">{trend_text}5 min: '
            f'{fmt(row[f"{vital}_mean"])} ± {fmt(row[f"{vital}_sd"])}</div>')


def show_latest(clean, levels):
    latest = (clean.sort_values("ts").groupby("patient_id").tail(1)
              .sort_values("patient_id"))
    critical = []
    for col, (_, row) in zip(st.columns(len(latest)), latest.iterrows()):
        level = levels.get(row.patient_id, "normal")  # no alert yet: normal
        if level == "critical":
            critical.append(f".st-key-card_{row.patient_id}")
        with col.container(border=True, key=f"card_{row.patient_id}"):
            st.subheader(f"{row.patient_id} "
                         f":{LEVEL_COLORS[level]}[● {level.title()}]")
            st.html("".join(vital_html(row.patient_id, v, label, row)
                            for v, label in VITALS.items()))
    if critical:
        st.html(f"<style>{', '.join(critical)} "
                "{border-color: #e5484d !important; "
                "animation: flash 1s ease-in-out infinite;}</style>")


def show_alerts(alerts):
    st.subheader("Latest alerts")
    if not alerts:
        st.caption("None yet.")
        return
    table = pd.DataFrame(alerts)
    table["time"] = [datetime.fromtimestamp(t).strftime("%H:%M:%S")
                     for t in table.ts]
    st.dataframe(table[["time", "patient_id", "level", "reason"]],
                 hide_index=True, use_container_width=True)


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
        alerts = storage.latest_alerts(conn)
        levels = storage.current_levels(conn)
    finally:
        conn.close()

    if not clean_rows:
        st.info("No readings yet. Start the pipeline: `python -m monitor.run`")
        return

    raw, clean = with_time(raw_rows), with_time(clean_rows)
    show_latest(clean, levels)
    show_alerts(alerts)
    for vital in VITALS:
        show_charts(raw, clean, vital)
    show_events(events)


st.set_page_config(page_title="Vital Signs Monitor", layout="wide")
st.html(CARD_CSS)
st.title("Vital Signs Monitor")
live_view()
