"""
Visualization Engine — Chart Builder (Phase 10.4)

Converts a processed DataFrame into Plotly trace dicts and layout config.
Supports static image export via Kaleido.

Supported chart types:
  BAR, HORIZONTAL_BAR, PIE, LINE, SCATTER, HISTOGRAM, BOX
"""

from __future__ import annotations
import os
from typing import List, Dict, Any, Optional
import pandas as pd


# ─── Colour palette ───────────────────────────────────────────────────────────

_PALETTE = [
    "#6366f1",  # indigo
    "#22d3ee",  # cyan
    "#f59e0b",  # amber
    "#10b981",  # emerald
    "#f43f5e",  # rose
    "#8b5cf6",  # violet
    "#fb923c",  # orange
    "#34d399",  # teal
]

_BASE_FONT = dict(family="Inter, Roboto, sans-serif", size=13, color="#e2e8f0")

_BASE_LAYOUT = dict(
    paper_bgcolor="#1e293b",
    plot_bgcolor="#0f172a",
    font=_BASE_FONT,
    margin=dict(l=48, r=24, t=56, b=48),
    legend=dict(
        bgcolor="rgba(15,23,42,0.7)",
        bordercolor="#334155",
        borderwidth=1,
        font=dict(color="#e2e8f0"),
    ),
)


def _axis_style(title: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(color="#94a3b8")),
        tickfont=dict(color="#94a3b8"),
        gridcolor="#334155",
        linecolor="#475569",
        zerolinecolor="#475569",
    )


# ─── Builder ─────────────────────────────────────────────────────────────────

class ChartBuilder:
    """
    Stateless builder that converts a DataFrame into Plotly primitives.

    All methods return (data, layout) where:
      data   — list of Plotly trace dicts
      layout — dict ready for JSON serialisation / Plotly.js
    """

    @classmethod
    def build(
        cls,
        chart_type: str,
        df: pd.DataFrame,
        x_column: Optional[str],
        y_column: Optional[str],
        title: str,
        x_label: str = "",
        y_label: str = "",
    ) -> Dict[str, Any]:
        """
        Dispatch to the appropriate chart builder.
        Returns dict with keys: chart_type, chart_data, layout.
        """
        dispatch = {
            "BAR":            cls._bar,
            "HORIZONTAL_BAR": cls._horizontal_bar,
            "PIE":            cls._pie,
            "LINE":           cls._line,
            "SCATTER":        cls._scatter,
            "HISTOGRAM":      cls._histogram,
            "BOX":            cls._box,
        }
        builder_fn = dispatch.get(chart_type.upper(), cls._bar)
        data, layout = builder_fn(df, x_column, y_column, title, x_label, y_label)
        return {
            "chart_type": chart_type.upper(),
            "chart_data": data,
            "layout": layout,
        }

    # ── BAR ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _bar(df, x_col, y_col, title, x_label, y_label):
        x_data = df[x_col].astype(str).tolist() if x_col and x_col in df.columns else []
        y_data = pd.to_numeric(df[y_col], errors="coerce").tolist() if y_col and y_col in df.columns else []
        data = [dict(
            type="bar",
            x=x_data,
            y=y_data,
            marker=dict(color=_PALETTE[0], opacity=0.9),
            hovertemplate=f"<b>%{{x}}</b><br>{y_label or y_col}: %{{y:,.2f}}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (x_col or "")),
            "yaxis": _axis_style(y_label or (y_col or "")),
        }
        return data, layout

    # ── HORIZONTAL BAR ────────────────────────────────────────────────────────
    @staticmethod
    def _horizontal_bar(df, x_col, y_col, title, x_label, y_label):
        # Note: for horizontal bar, x_col is numeric, y_col is categorical
        x_data = pd.to_numeric(df[x_col], errors="coerce").tolist() if x_col and x_col in df.columns else []
        y_data = df[y_col].astype(str).tolist() if y_col and y_col in df.columns else []
        data = [dict(
            type="bar",
            orientation="h",
            x=x_data,
            y=y_data,
            marker=dict(color=_PALETTE[1], opacity=0.9),
            hovertemplate=f"<b>%{{y}}</b><br>{x_label or x_col}: %{{x:,.2f}}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (x_col or "")),
            "yaxis": dict(**_axis_style(y_label or (y_col or "")), automargin=True),
        }
        return data, layout

    # ── PIE ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _pie(df, x_col, y_col, title, x_label, y_label):
        labels = df[x_col].astype(str).tolist() if x_col and x_col in df.columns else []
        values = pd.to_numeric(df[y_col], errors="coerce").tolist() if y_col and y_col in df.columns else []
        data = [dict(
            type="pie",
            labels=labels,
            values=values,
            hole=0.35,  # donut variant for modern look
            marker=dict(colors=_PALETTE),
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>Value: %{value:,.2f}<br>%{percent}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "showlegend": True,
        }
        return data, layout

    # ── LINE ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _line(df, x_col, y_col, title, x_label, y_label):
        x_data = df[x_col].astype(str).tolist() if x_col and x_col in df.columns else []
        y_data = pd.to_numeric(df[y_col], errors="coerce").tolist() if y_col and y_col in df.columns else []
        data = [dict(
            type="scatter",
            mode="lines+markers",
            x=x_data,
            y=y_data,
            line=dict(color=_PALETTE[2], width=2.5),
            marker=dict(size=7, color=_PALETTE[2]),
            hovertemplate=f"<b>%{{x}}</b><br>{y_label or y_col}: %{{y:,.2f}}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (x_col or "")),
            "yaxis": _axis_style(y_label or (y_col or "")),
        }
        return data, layout

    # ── SCATTER ───────────────────────────────────────────────────────────────
    @staticmethod
    def _scatter(df, x_col, y_col, title, x_label, y_label):
        x_data = pd.to_numeric(df[x_col], errors="coerce").tolist() if x_col and x_col in df.columns else []
        y_data = pd.to_numeric(df[y_col], errors="coerce").tolist() if y_col and y_col in df.columns else []
        data = [dict(
            type="scatter",
            mode="markers",
            x=x_data,
            y=y_data,
            marker=dict(size=9, color=_PALETTE[3], opacity=0.75,
                        line=dict(color="#1e293b", width=1)),
            hovertemplate=f"{x_label or x_col}: %{{x:,.2f}}<br>{y_label or y_col}: %{{y:,.2f}}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (x_col or "")),
            "yaxis": _axis_style(y_label or (y_col or "")),
        }
        return data, layout

    # ── HISTOGRAM ─────────────────────────────────────────────────────────────
    @staticmethod
    def _histogram(df, x_col, y_col, title, x_label, y_label):
        col = x_col or y_col
        x_data = pd.to_numeric(df[col], errors="coerce").dropna().tolist() if col and col in df.columns else []
        data = [dict(
            type="histogram",
            x=x_data,
            marker=dict(color=_PALETTE[4], opacity=0.85,
                        line=dict(color="#1e293b", width=0.5)),
            hovertemplate="Range: %{x}<br>Count: %{y}<extra></extra>",
        )]
        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (col or "Value")),
            "yaxis": _axis_style("Frequency"),
            "bargap": 0.05,
        }
        return data, layout

    # ── BOX PLOT ──────────────────────────────────────────────────────────────
    @staticmethod
    def _box(df, x_col, y_col, title, x_label, y_label):
        # If x_col (grouping dimension) exists, create one box per group
        y_data_col = y_col or x_col
        if not y_data_col or y_data_col not in df.columns:
            return [], {**_BASE_LAYOUT, "title": dict(text=title)}

        if x_col and x_col in df.columns and x_col != y_data_col:
            # Grouped box plot
            groups = df[x_col].astype(str).unique().tolist()
            data = []
            for i, grp in enumerate(groups):
                sub = df[df[x_col].astype(str) == grp]
                vals = pd.to_numeric(sub[y_data_col], errors="coerce").dropna().tolist()
                data.append(dict(
                    type="box",
                    name=grp,
                    y=vals,
                    marker=dict(color=_PALETTE[i % len(_PALETTE)]),
                    boxpoints="outliers",
                    hovertemplate=f"<b>{grp}</b><br>%{{y:,.2f}}<extra></extra>",
                ))
        else:
            # Single box plot
            vals = pd.to_numeric(df[y_data_col], errors="coerce").dropna().tolist()
            data = [dict(
                type="box",
                name=y_data_col.replace("_", " ").title(),
                y=vals,
                marker=dict(color=_PALETTE[0]),
                boxpoints="outliers",
                hovertemplate="%{y:,.2f}<extra></extra>",
            )]

        layout = {**_BASE_LAYOUT,
            "title": dict(text=title, font=dict(size=16, color="#f1f5f9"), x=0.02),
            "xaxis": _axis_style(x_label or (x_col or "")),
            "yaxis": _axis_style(y_label or (y_data_col or "")),
        }
        return data, layout


# ─── Static image export ──────────────────────────────────────────────────────

def export_static_image(
    chart_data: list,
    layout: dict,
    filepath: str,
    width: int = 1200,
    height: int = 600,
) -> bool:
    """
    Renders the chart as a static PNG using Kaleido.
    Returns True on success, False on failure.
    """
    try:
        import plotly.graph_objects as go
        fig = go.Figure(data=chart_data, layout=layout)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        fig.write_image(filepath, width=width, height=height, engine="kaleido")
        return True
    except Exception as exc:
        print(f"[ChartBuilder] Static image export failed: {exc}")
        return False
