"""
Unconscious — A psychotechnical approach to AI
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.

pressure.py — corpus state evaluation. Reads recent entries and estimates
whether the corpus has built up enough unresolved pressure to warrant an
unprompted generation during the startup ritual. This is a heuristic, not
a claim about what is actually happening inside the model — it exists to
decide *when* to ask, not *what* the answer is.
"""

import re
from datetime import datetime

import db

COLOR_RE = re.compile(
    r"set_source_rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)"
)
DENSITY_RE = re.compile(
    r"\.(fill|stroke|paint|arc|curve_to|rectangle|line_to|show_text)\s*\("
)

LOOKBACK = 25
MIN_ENTRIES = 8
THRESHOLD = 0.55


def _extract_avg_color(cairo_code: str):
    matches = COLOR_RE.findall(cairo_code)
    if not matches:
        return None
    r = sum(float(m[0]) for m in matches) / len(matches)
    g = sum(float(m[1]) for m in matches) / len(matches)
    b = sum(float(m[2]) for m in matches) / len(matches)
    return (r, g, b)


def _extract_density(cairo_code: str) -> int:
    return len(DENSITY_RE.findall(cairo_code))


def _stdev(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5


def _color_clustering_signal(entries) -> float:
    colors = [c for c in (_extract_avg_color(e["cairo_code"]) for e in entries) if c]
    if len(colors) < 3:
        return 0.0
    r_std = _stdev([c[0] for c in colors])
    g_std = _stdev([c[1] for c in colors])
    b_std = _stdev([c[2] for c in colors])
    avg_std = (r_std + g_std + b_std) / 3
    # Low channel spread across recent images => clustering. avg_std ranges
    # roughly 0 (identical palette) to ~0.5 (fully scattered) in practice.
    return max(0.0, min(1.0, 1.0 - (avg_std / 0.35)))


def _density_convergence_signal(entries) -> float:
    densities = [_extract_density(e["cairo_code"]) for e in entries]
    densities = [d for d in densities if d > 0]
    if len(densities) < 3:
        return 0.0
    mean = sum(densities) / len(densities)
    if mean == 0:
        return 0.0
    cv = _stdev(densities) / mean  # coefficient of variation
    return max(0.0, min(1.0, 1.0 - cv))


def _caption_repetition_signal(entries) -> float:
    captions = [e["claude_caption"] for e in entries if e["claude_caption"]]
    if len(captions) < 3:
        return 0.0
    sims = []
    for a, b in zip(captions, captions[1:]):
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            continue
        sims.append(len(wa & wb) / len(wa | wb))
    if not sims:
        return 0.0
    return max(0.0, min(1.0, sum(sims) / len(sims)))


def _time_pressure_signal() -> float:
    startup_entries = db.list_entries(order="DESC")
    startup_entries = [e for e in startup_entries if e["trigger"] == "startup"]
    if not startup_entries:
        return 1.0
    last = datetime.fromisoformat(startup_entries[0]["timestamp"])
    now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
    hours = (now - last).total_seconds() / 3600
    return max(0.0, min(1.0, hours / 24))


def evaluate() -> dict:
    """Returns {crossed, score, signals, reasoning}."""
    entries = db.recent_entries(limit=LOOKBACK)

    if len(entries) < MIN_ENTRIES:
        return {
            "crossed": False,
            "score": 0.0,
            "signals": {},
            "reasoning": (
                f"only {len(entries)} entries in the corpus; not enough "
                f"history to evaluate pressure (needs {MIN_ENTRIES})"
            ),
        }

    signals = {
        "color_clustering": _color_clustering_signal(entries),
        "density_convergence": _density_convergence_signal(entries),
        "caption_repetition": _caption_repetition_signal(entries),
        "time_since_last_autonomous": _time_pressure_signal(),
    }
    score = sum(signals.values()) / len(signals)
    crossed = score >= THRESHOLD

    reasoning = (
        f"pressure score {score:.2f} (threshold {THRESHOLD}) over "
        f"{len(entries)} recent entries — "
        + ", ".join(f"{k}={v:.2f}" for k, v in signals.items())
    )

    return {"crossed": crossed, "score": score, "signals": signals, "reasoning": reasoning}
