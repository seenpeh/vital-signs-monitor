"""Draw the README figures (besides docs/real_vs_sim.png, see compare.py).

Run:  python -m monitor.figures

* docs/crisis.png         two simulated crises, through filter and triage
* docs/median_filter.gif  rolling median vs moving average, on one signal
* docs/news2_bands.png    the NEWS2 points per vital
"""
import statistics

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Rectangle

from monitor.config import ROOT
from monitor.processing import EdgeProcessor
from monitor.run import tick
from monitor.simulator import make_patients
from monitor.triage import BANDS, LEVELS, Triage

OUT = ROOT / "docs"
LEVEL_COLORS = {"normal": "#2e9d5b", "low": "#e8c547", "low-medium": "#f6a530",
                "medium": "#d9480f", "high": "#c62828"}
POINT_COLORS = ["#2e9d5b", "#e8c547", "#f08a24", "#c62828"]


def record(seed, ticks):
    """Run one patient through the closed loop; keep every step."""
    p = make_patients(1, seed)[0]
    proc, tri = EdgeProcessor(), Triage()
    rows = []
    for i in range(ticks):
        raw, clean, started, _, _ = tick([p], {p.id: proc}, {p.id: tri},
                                         i * 2.0, 2.0)
        ev = p.event
        rows.append({"t": i * 2.0, "raw": raw[0], "clean": clean[0],
                     "level": tri.level,
                     "started": [e["kind"] for e in started],
                     "event": ev.kind if ev else None,
                     "treat": ev.treat_tick if ev and ev.shift else None,
                     "worsen": ev.worsen if ev and ev.shift else None})
    return rows


def find_crisis(rows, kind, want_worsen):
    """(start, end) index of the first crisis of `kind` that fits."""
    i = 0
    while i < len(rows):
        if kind in rows[i]["started"]:
            j = i
            while j < len(rows) and rows[j]["event"] == kind:
                j += 1
            calm = all(r["level"] == "normal"
                       for r in rows[max(i - 30, 0):i])
            if (calm and j < len(rows)
                    and bool(rows[j - 1]["worsen"]) == want_worsen):
                return i, j
            i = j
        i += 1
    raise LookupError(kind)


def crisis_column(axes, rows, start, end, title):
    lo, hi = max(start - 30, 0), min(end + 30, len(rows))
    part = rows[lo:hi]
    t = [(r["t"] - rows[start]["t"]) for r in part]
    for ax, vital, label, limit in ((axes[0], "spo2", "SpO2 (%)", 96),
                                    (axes[1], "hr", "HR (BPM)", 90)):
        ax.plot(t, [r["raw"][vital] for r in part], color="#9a9a9a", lw=1,
                label="raw sensor")
        ax.plot(t, [r["clean"][vital] for r in part], color="#1f5fbf", lw=2,
                label="edge-filtered")
        ax.axhline(limit, ls="--", color="#c62828", lw=1)
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    # level strip
    for k, r in enumerate(part):
        axes[2].add_patch(Rectangle((t[k], 0), 2, 1, lw=0,
                                    color=LEVEL_COLORS[r["level"]]))
    axes[2].set_xlim(t[0], t[-1] + 2)
    axes[2].set_yticks([])
    axes[2].set_ylabel("level")
    axes[2].set_xlabel("seconds from crisis start")

    marks = {"crisis starts": rows[start]["t"]}
    alarm = next(r["t"] for r in rows[start:] if r["level"] not in
                 ("normal", "low"))
    marks["alarm"] = alarm
    treat = rows[end - 1]["treat"]
    marks["nurse treats"] = treat * 2.0
    worsen = rows[end - 1]["worsen"]
    if worsen:
        marks["complication"] = worsen[0] * 2.0
    marks["recovered"] = rows[end]["t"]
    for name, ts in marks.items():
        x = ts - rows[start]["t"]
        for ax in axes:
            ax.axvline(x, color="black", lw=0.8, alpha=0.6)
        axes[0].annotate(name, (x, 1), xycoords=("data", "axes fraction"),
                         rotation=90, va="top", ha="right", fontsize=8)
    for r in part:
        if "spo2_dip" in r["started"]:
            axes[0].annotate("artefact", (r["t"] - rows[start]["t"],
                                          r["raw"]["spo2"]),
                             fontsize=8, color="#666", ha="center",
                             va="top", xytext=(0, -4),
                             textcoords="offset points")
    axes[0].set_title(title)


def crisis_figure():
    rows = record(seed=7, ticks=10000)
    fig, axes = plt.subplots(3, 2, figsize=(14, 7), sharex="col",
                             gridspec_kw={"height_ratios": [3, 3, 0.6]})
    s, e = find_crisis(rows, "spo2_drop", want_worsen=False)
    crisis_column(axes[:, 0], rows, s, e, "SpO2 drop")
    s, e = find_crisis(rows, "arrhythmia", want_worsen=True)
    crisis_column(axes[:, 1], rows, s, e,
                  "Arrhythmia, with a complication during treatment")
    axes[1][0].legend(loc="lower right", fontsize=8)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in LEVEL_COLORS.values()]
    fig.legend(handles, [l.capitalize() for l in LEVELS], loc="lower center",
               ncol=5, frameon=False)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT / "crisis.png", dpi=110)


def median_gif():
    # a calm SpO2 trace, a 3-sample motion glitch, then a real drop
    sig = [97, 98, 97, 97, 98, 97, 97, 98, 97, 97,
           80, 78, 81, 97, 98, 97, 97, 98, 97, 97,
           97, 98, 97, 97, 98, 96, 93, 90, 88, 87,
           86, 86, 87, 86, 86, 87, 86, 86, 86, 87]
    w = 7
    med = [statistics.median(sig[i - w + 1:i + 1]) if i >= w - 1 else None
           for i in range(len(sig))]
    avg = [statistics.fmean(sig[i - w + 1:i + 1]) if i >= w - 1 else None
           for i in range(len(sig))]
    fig, ax = plt.subplots(figsize=(8, 3.6))

    def draw(i):
        ax.clear()
        ax.axhline(91, ls="--", color="#c62828", lw=1)
        ax.text(0, 91.3, "alarm limit (NEWS2: SpO2 ≤ 91 scores 3)",
                fontsize=8, color="#c62828")
        ax.plot(range(len(sig)), sig, "o", color="#bbbbbb", ms=4,
                label="raw")
        ax.plot(range(i + 1), sig[:i + 1], "o", color="#555", ms=4)
        if i >= w - 1:
            ax.add_patch(Rectangle((i - w + 1 - 0.4, 75), w - 0.2, 26,
                                   color="#1f5fbf", alpha=0.08))
        xs = range(w - 1, i + 1)
        ax.plot(xs, avg[w - 1:i + 1], color="#f08a24", lw=2,
                label="moving average (7)")
        ax.plot(xs, med[w - 1:i + 1], color="#1f5fbf", lw=2.5,
                label="rolling median (7)")
        ax.annotate("motion glitch\n(3 samples)", (11, 77.5), ha="center",
                    fontsize=8)
        ax.annotate("real drop", (30, 84), ha="center", fontsize=8)
        if i >= 12:
            ax.annotate("average: false alarm", (12, avg[12]),
                        xytext=(15, 80), fontsize=8, color="#f08a24",
                        arrowprops={"arrowstyle": "->", "color": "#f08a24"})
        ax.set_ylim(75, 101)
        ax.set_xlim(-1, len(sig))
        ax.set_xlabel("sample (2 s each)")
        ax.set_ylabel("SpO2 (%)")
        ax.legend(loc="lower left", fontsize=8)
        fig.tight_layout()

    frames = list(range(len(sig))) + [len(sig) - 1] * 8  # hold the end
    FuncAnimation(fig, draw, frames=frames).save(
        OUT / "median_filter.gif", writer=PillowWriter(fps=4), dpi=90)


def news2_figure():
    spans = {"hr": (25, 170, "HR (BPM)"), "spo2": (85, 100.5, "SpO2 (%)"),
             "sbp": (70, 240, "SBP (mmHg)")}
    fig, axes = plt.subplots(3, 1, figsize=(9, 3.4))
    for ax, (vital, (lo, hi, label)) in zip(axes, spans.items()):
        edges = [lo] + [b for b, _ in BANDS[vital]] + [hi]
        pts = [3] + [p for _, p in BANDS[vital]]
        for a, b, p in zip(edges, edges[1:], pts):
            if b <= a:
                continue
            ax.add_patch(Rectangle((a, 0), b - a, 1, color=POINT_COLORS[p]))
            ax.text((a + b) / 2, 0.5, str(p), ha="center", va="center",
                    color="white", fontweight="bold")
        ax.set_xlim(lo, hi)
        ax.set_yticks([])
        ax.set_ylabel(label, rotation=0, ha="right", va="center")
        ax.set_xticks(edges[1:-1])
        ax.tick_params(labelsize=8)
        for s in ax.spines.values():
            s.set_visible(False)
    axes[0].set_title("NEWS2 points per vital (0 = normal, 3 = red flag)",
                      fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "news2_bands.png", dpi=110)


def main():
    OUT.mkdir(exist_ok=True)
    crisis_figure()
    median_gif()
    news2_figure()
    print(f"Saved figures in {OUT}")


if __name__ == "__main__":
    main()
