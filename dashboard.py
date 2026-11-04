"""Live dashboard. Reads what the pipeline writes to SQLite.

Run:  streamlit run dashboard.py
"""
import time
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from monitor import storage
from monitor.triage import ALARM_LEVELS, score
from monitor.config import NORMAL_RANGES, SAMPLE_INTERVAL_S

WINDOW_S = 5 * 60  # how much history the charts show
VITALS = {"hr": "Heart rate (BPM)", "spo2": "SpO2 (%)",
          "sbp": "Systolic BP (mmHg)"}
SHORT = {"hr": "HR (BPM)", "spo2": "SpO2 (%)", "sbp": "SBP (mmHg)"}
TABLE_HEIGHT = 250
LEVEL_COLORS = {"normal": "green", "low": "yellow", "low-medium": "orange",
                "medium": "orange", "high": "red"}
NEAR = 40  # a range limit further than this from the data stays off the axis

# Card animations, pure CSS. The rolling number animates a registered
# integer property shown through a CSS counter. All motion is skipped
# when the viewer's system asks for reduced motion.
CARD_CSS = r"""<style>
@property --n { syntax: '\3c integer\3e'; initial-value: 0; inherits: false; }
[data-testid="stMainBlockContainer"] { padding-top: 2.5rem; }
.vitals { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; }
.vital-label { font-size: 0.75rem; opacity: 0.8; }
.vital-value { font-size: 1.6rem; line-height: 1.2; }
.vital-value.out { color: #e5484d; font-weight: 600; display: inline-block;
                   transform-origin: left center; }
.vital-sub { font-size: 0.7rem; opacity: 0.65; }
.roll { --n: var(--to); counter-reset: n var(--n);
        font-variant-numeric: tabular-nums; }
.roll::after { content: counter(n); }
.heart { display: inline-block; color: #e5484d; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden;
           clip: rect(0 0 0 0); white-space: nowrap; }
@keyframes roll { from { --n: var(--from); } to { --n: var(--to); } }
@keyframes beat { 0%, 30%, 100% { transform: scale(1); }
                  15% { transform: scale(1.35); } }
@keyframes pulse { 0%, 100% { transform: scale(1); }
                   50% { transform: scale(1.18); } }
@keyframes flash { 50% { box-shadow: 0 0 0 4px rgba(229, 72, 77, 0.5); } }
@media (prefers-reduced-motion: no-preference) {
  .roll { animation: roll 0.8s ease-out; }
  .heart { animation: beat var(--beat) ease-in-out infinite; }
  .vital-value.out { animation: pulse 1s ease-in-out infinite; }
}
@media (prefers-reduced-motion: reduce) {
  [class*="st-key-card_"] { animation: none !important; }
}
</style>"""


def vital_chart(df, vital, domain):
    """Line per patient; the normal range is shaded, its limits dashed."""
    low, high = NORMAL_RANGES[vital]
    scale = alt.Scale(domain=domain)
    band = (alt.Chart(pd.DataFrame({"low": [low], "high": [high]}))
            .mark_rect(color="green", opacity=0.08)
            .encode(y=alt.Y("low:Q", scale=scale), y2="high:Q"))
    limits = (alt.Chart(pd.DataFrame({"y": [low, high]}))
              .mark_rule(color="#e5484d", strokeDash=[4, 4], opacity=0.8)
              .encode(y=alt.Y("y:Q", scale=scale)))
    lines = (alt.Chart(df).mark_line()
             .encode(x=alt.X("time:T", title=None,
                             axis=alt.Axis(format="%H:%M:%S")),
                     y=alt.Y(f"{vital}:Q", title=VITALS[vital], scale=scale),
                     color=alt.Color("patient_id:N", title=None,
                                     legend=alt.Legend(orient="top"))))
    return (band + limits + lines).properties(height=210)


def show_charts(raw, clean):
    """Raw row above cleaned row; each vital's column shares one y scale.

    The scale reaches the range limits, unless one is far from the data:
    SBP's 219 would flatten the chart.
    """
    domains = {}
    for v, (low, high) in NORMAL_RANGES.items():
        lo = min(raw[v].min(), clean[v].min())
        hi = max(raw[v].max(), clean[v].max())
        domains[v] = [min(lo, low) if lo - low < NEAR else lo,
                      max(hi, high) if high - hi < NEAR else hi]
    for title, df in (("Raw sensor", raw), ("Cleaned by edge filter", clean)):
        st.markdown(f"**{title}**")
        for col, vital in zip(st.columns(len(VITALS)), VITALS):
            col.altair_chart(vital_chart(df, vital, domains[vital]),
                             use_container_width=True)


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
    low, high = NORMAL_RANGES[vital]
    out = not pd.isna(value) and not low <= value <= high
    if out:  # pulses so the problem vital stands out
        number += '<span class="sr-only"> out of range</span>'
    trend_text = "—" if pd.isna(trend) else f"{trend:+.1f}/min"
    return (f'<div><div class="vital-label">{label}{heart}</div>'
            f'<div class="vital-value{" out" if out else ""}">{number}</div>'
            f'<div class="vital-sub">{trend_text}<br>5m: '
            f'{fmt(row[f"{vital}_mean"])} ± {fmt(row[f"{vital}_sd"])}</div>'
            '</div>')


def show_latest(clean, levels):
    latest = (clean.sort_values("ts").groupby("patient_id").tail(1)
              .sort_values("patient_id"))
    alarming = []
    for col, (_, row) in zip(st.columns(len(latest)), latest.iterrows()):
        level = levels.get(row.patient_id, "normal")  # no alert yet: normal
        if level in ALARM_LEVELS:
            alarming.append(f".st-key-card_{row.patient_id}")
        label = level.capitalize() + ("" if level == "normal" else " risk")
        with col.container(border=True, key=f"card_{row.patient_id}"):
            st.markdown(f"**{row.patient_id}** "
                        f":{LEVEL_COLORS[level]}[● {label}]")
            st.html('<div class="vitals">'
                    + "".join(vital_html(row.patient_id, v, label, row)
                              for v, label in SHORT.items()) + "</div>")
    if alarming:
        st.html(f"<style>{', '.join(alarming)} "
                "{border-color: #e5484d !important; "
                "animation: flash 1s ease-in-out infinite;}</style>")


def show_alerts(alerts):
    st.markdown("**Latest alerts**")
    if not alerts:
        st.caption("None yet.")
        return
    table = pd.DataFrame(alerts)
    table["time"] = [datetime.fromtimestamp(t).strftime("%H:%M:%S")
                     for t in table.ts]
    st.dataframe(table[["time", "patient_id", "level", "reason"]],
                 hide_index=True, use_container_width=True,
                 height=TABLE_HEIGHT)


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def show_events(events, title, columns=()):
    st.markdown(f"**{title}**")
    if not events:
        st.caption("None yet.")
        return
    table = pd.DataFrame(events)
    table["start"] = [clock(t) for t in table.start_ts]
    table["duration_s"] = ["ongoing" if pd.isna(end) else f"{end - start:.0f}"
                           for start, end in zip(table.start_ts, table.end_ts)]
    table["treated"] = ["" if pd.isna(t) else clock(t)
                        for t in table.treat_ts]
    st.dataframe(table[["start", "patient_id", "kind", "vital", "duration_s",
                        *columns]],
                 hide_index=True, use_container_width=True,
                 height=TABLE_HEIGHT)


TOAST_MAX = 5  # more new notices than this (a jump): one summary instead


def notify(injected):
    """Toast each injection and each start of treatment, once per viewer.

    The first refresh only marks what is already there as seen.
    """
    notices = {(e["patient_id"], e["kind"], e["start_ts"]):
               f"{e['patient_id']}: {e['kind']} injected at "
               f"{clock(e['start_ts'])}" for e in injected}
    notices.update({(e["patient_id"], "treated", e["treat_ts"]):
                    f"{e['patient_id']}: treatment of {e['kind']} started "
                    f"at {clock(e['treat_ts'])}"
                    for e in injected if e["treat_ts"] is not None})
    seen = st.session_state.get("seen")
    st.session_state.seen = set(notices)
    if seen is None:
        return
    new = [k for k in notices if k not in seen]
    if len(new) > TOAST_MAX:
        treated = sum(k[1] == "treated" for k in new)
        st.toast(f"{len(new) - treated} injections and {treated} "
                 "treatments since the last refresh", icon="⏩")
        return
    for k in sorted(new, key=lambda k: k[2]):
        st.toast(notices[k], icon="🩺" if k[1] == "treated" else "💉")


def show_score(events, alerts, now):
    """How well the alert & triage engine matches the injected crises."""
    s = score(events, alerts, now)
    st.markdown("**Alert & triage accuracy** (vs. simulator ground truth)")
    done = s["caught"] + s["covered"] + s["missed"]
    delay = s["median_delay_s"]
    cols = st.columns(6)
    cols[0].metric("Crises", s["crises"],
                   help="SpO2 drops, arrhythmias and worsenings during "
                        "treatment, injected so far")
    cols[1].metric("Detected", f"{(s['caught'] + s['covered']) / done:.0%}"
                   if done else "—",
                   help="New alarm (the level rises to low-medium or "
                        "above), or monitor already alarming when the "
                        "crisis started. Ongoing crises excluded.")
    cols[2].metric("Missed", s["missed"])
    cols[3].metric("False alarms", s["false_alarms"],
                   help="Alarms with no crisis behind them")
    cols[4].metric("Time to alarm",
                   "—" if delay is None else f"{delay:.0f} s")
    cols[5].metric("Artefacts & spikes", s["artefacts"],
                   help="Sensor glitches the engine must not alarm on")


def with_time(rows):
    df = pd.DataFrame(rows)
    df["time"] = [datetime.fromtimestamp(t) for t in df.ts]
    return df


def jump_hour():
    conn = storage.connect()
    try:
        storage.request_jump(conn)
    finally:
        conn.close()
    st.toast("Jumping 1 hour ahead…", icon="⏩")


def toggle_pause():
    conn = storage.connect()
    try:
        storage.set_paused(conn, not storage.is_paused(conn))
    finally:
        conn.close()


# Everything lives in the fragment, pause button included: a button outside
# it would rerun the whole page, which jumps the scroll back to the top.
@st.fragment(run_every=SAMPLE_INTERVAL_S)
def live_view():
    conn = storage.connect()
    try:
        paused = storage.is_paused(conn)
        now = storage.latest_ts(conn) or time.time()
        # anchored at the newest reading: a paused sim's clock lags real time
        since = now - WINDOW_S
        raw_rows = storage.readings_since(conn, since)
        clean_rows = storage.processed_since(conn, since)
        events = storage.latest_events(conn, limit=-1)  # all, for the score
        alerts = storage.latest_alerts(conn, limit=-1)
        levels = storage.current_levels(conn)
    finally:
        conn.close()

    title, jump, button = st.columns([4, 1, 1], vertical_alignment="center")
    title.title("Vital Signs Monitor" + (" — paused" if paused else ""))
    jump.button("⏩ +1 hour", on_click=jump_hour,
                help="Run the next hour of the simulation at once",
                use_container_width=True)
    button.button("▶ Resume" if paused else "⏸ Pause",
                  on_click=toggle_pause, shortcut="space",
                  use_container_width=True)
    if not clean_rows:
        st.info("No readings yet. Start the pipeline: `python -m monitor.run`")
        return

    raw, clean = with_time(raw_rows), with_time(clean_rows)
    show_latest(clean, levels)
    show_charts(raw, clean)
    show_score(events, alerts, now)
    spikes = [e for e in events if e["kind"] == "spike"]
    injected = [e for e in events if e["kind"] != "spike"]
    notify(injected)
    left, mid, right = st.columns(3)
    with left:
        show_alerts(alerts[:50])
    with mid:
        show_events(injected[:50], "Injected events (ground truth)",
                    ["treated"])
    with right:
        show_events(spikes[:50], "Real-data spikes")


st.set_page_config(page_title="Vital Signs Monitor", layout="wide")
st.html(CARD_CSS)
live_view()
