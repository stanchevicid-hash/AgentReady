"""Shields-style SVG badge: 'agent-ready | 85'."""

from __future__ import annotations


def badge_svg(score: int | None, label: str = "agent-ready") -> str:
    value = "n/a" if score is None else str(score)
    color = "#9f9f9f"
    if score is not None:
        color = "#2ea44f" if score >= 75 else "#dfb317" if score >= 50 else "#e05d44"
    lw = 6 * len(label) + 12
    vw = 7 * len(value) + 14
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/><rect width="{w}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw / 2}" y="14">{label}</text><text x="{lw + vw / 2}" y="14">{value}</text></g></svg>"""
