"""Matplotlib (Agg) charts for the dashboard and docs. Returns PNG bytes."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

INK, MUTED, BLUE, ORANGE, GRID = "#1f2937", "#6b7280", "#2563eb", "#d97706", "#e5e7eb"
CHARTS = ("volume", "sla_by_priority", "by_category", "resolution_by_category")


def _style(ax, title: str) -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK, fontweight="bold")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def render(name: str, summary: dict) -> bytes:
    """Render one named chart from an :func:`helpdesk.analytics.summary` dict."""
    if name not in CHARTS:
        raise KeyError(name)
    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=110)
    if name == "volume":
        data = summary["volume_by_week"]
        ax.bar([d["week"][5:] for d in data], [d["tickets"] for d in data], color=BLUE)
        _style(ax, "Tickets created per week (week start, MM-DD)")
        ax.tick_params(axis="x", rotation=60)
    elif name == "sla_by_priority":
        data = summary["by_priority"]
        vals = [d["sla_compliance_pct"] or 0 for d in data]
        bars = ax.bar([d["priority"] for d in data], vals, color=BLUE)
        ax.set_ylim(0, 105)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}%", ha="center", fontsize=8, color=INK)
        _style(ax, "SLA compliance by priority (%, decided tickets)")
    elif name == "by_category":
        data = sorted(summary["by_category"], key=lambda d: d["tickets"])
        ax.barh([d["category"] for d in data], [d["tickets"] for d in data], color=BLUE)
        _style(ax, "Tickets by category")
        ax.grid(axis="x", color=GRID)
        ax.grid(axis="y", visible=False)
    else:
        data = summary["by_category"]
        ax.bar([d["category"] for d in data], [(d["mean_resolution_minutes"] or 0) / 60 for d in data], color=ORANGE)
        _style(ax, "Mean resolution time by category (hours)")
        ax.tick_params(axis="x", rotation=25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
