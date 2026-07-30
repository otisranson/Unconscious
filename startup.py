"""
Unconscious
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.

startup.py — the startup ritual. Runs exactly once, synchronously, when
the app initializes. There is no persistent background scheduler and no
always-on service: session start is the heartbeat, and opening the app is
the circadian trigger. After this completes, the app sits quietly and
waits for the user.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

import db
import pipeline
import pressure

DEFAULT_NEWS_RSS = "http://feeds.bbci.co.uk/news/world/rss.xml"
DREAM_HOURS = 8
SYNTHESIS_HOURS = 48
PROJECT_ROOT = Path(__file__).parent


def _resolve_image_paths(entries):
    return [str(PROJECT_ROOT / e["image_path"]) for e in entries]


def _system_pulse_text() -> str:
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    try:
        load1, load5, load15 = psutil.getloadavg()
        load_text = f"{load1:.2f}/{load5:.2f}/{load15:.2f}"
    except (AttributeError, OSError):
        load_text = "unavailable"
    n_procs = len(psutil.pids())

    return (
        f"System pulse: CPU {cpu:.1f}% utilized. Memory {mem.percent:.1f}% "
        f"used ({mem.used / 1e9:.1f}GB of {mem.total / 1e9:.1f}GB). "
        f"{n_procs} processes running. Load average (1/5/15m): {load_text}."
    )


def weather_text() -> str | None:
    api_key = db.get_setting("openweather_api_key")
    city = db.get_setting("weather_city")
    if not api_key or not city:
        return None
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "metric"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        return (
            f"Weather in {city}: {desc}, {temp:.1f}°C (feels like "
            f"{feels:.1f}°C), {humidity}% humidity."
        )
    except Exception as exc:  # network/API failures are not fatal to startup
        return f"Weather signal unavailable ({exc})."


def news_text(limit=5) -> str | None:
    rss_url = db.get_setting("news_rss_url") or DEFAULT_NEWS_RSS
    try:
        resp = requests.get(rss_url, timeout=6)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = [el.text for el in root.findall(".//item/title")][:limit]
        titles = [t for t in titles if t]
        if not titles:
            return None
        return "Top headlines: " + " | ".join(titles)
    except Exception as exc:
        return f"News signal unavailable ({exc})."


def _environment_text() -> str:
    parts = [p for p in (weather_text(), news_text()) if p]
    if not parts:
        return "No external environmental signal was reachable this moment."
    return "\n".join(parts)


def run_startup_ritual() -> dict:
    """Runs the full ritual once. Returns a summary dict for the app to
    hold onto and expose to the frontend."""
    now_iso = datetime.now(timezone.utc).isoformat()
    db.log_session_start(now_iso)
    previous_session = db.get_previous_session_start()

    entries = []

    if not pipeline.get_api_key():
        return {
            "ran": False,
            "entries": [],
            "message": (
                "The unconscious stayed asleep — no Anthropic API key is "
                "configured yet. Add one in Settings, then restart to run "
                "the startup ritual."
            ),
        }

    steps = []

    # 1. System pulse
    try:
        entry = pipeline.generate(
            source="system",
            trigger="startup",
            prompt_text=_system_pulse_text(),
            instruction=(
                "This is a system pulse: the machine's physiological state "
                "at this moment. Encode it honestly."
            ),
        )
        entries.append(entry)
        steps.append("system pulse")
    except Exception as exc:
        steps.append(f"system pulse failed ({exc})")

    # 2. Environment pull
    try:
        entry = pipeline.generate(
            source="environment",
            trigger="startup",
            prompt_text=_environment_text(),
            instruction=(
                "This is an environmental signal: weather and news from "
                "outside this system, at this moment. Encode it honestly."
            ),
        )
        entries.append(entry)
        steps.append("environment pull")
    except Exception as exc:
        steps.append(f"environment pull failed ({exc})")

    # 3. Corpus pressure check
    try:
        result = pressure.evaluate()
        steps.append(f"pressure check: {result['reasoning']}")
        if result["crossed"]:
            recent = db.recent_entries(limit=20)
            if recent:
                entry = pipeline.generate(
                    source="pressure",
                    trigger="startup",
                    image_paths=_resolve_image_paths(recent),
                    instruction=(
                        "This is your corpus. What is unresolved. What is "
                        "accumulating. What needs processing."
                    ),
                )
                entries.append(entry)
                steps.append("pressure release generated")
    except Exception as exc:
        steps.append(f"pressure check failed ({exc})")

    # 4. Dream / synthesis check
    try:
        if previous_session:
            prev_dt = datetime.fromisoformat(previous_session)
            now_dt = datetime.fromisoformat(now_iso)
            hours = (now_dt - prev_dt).total_seconds() / 3600

            if hours > SYNTHESIS_HOURS and db.count_entries() >= 5:
                sample = db.random_entries(limit=20)
                entry = pipeline.generate(
                    source="synthesis",
                    trigger="startup",
                    image_paths=_resolve_image_paths(sample),
                    instruction=(
                        "More than two days have passed. This is a random "
                        "sample across your full history. Generate from "
                        "visual input alone — what does the whole corpus, "
                        "seen at once, need to say?"
                    ),
                )
                entries.append(entry)
                steps.append(f"synthesis ({hours:.1f}h since last session)")
            elif hours > DREAM_HOURS and db.count_entries() >= 3:
                recent = db.recent_entries(limit=30)
                entry = pipeline.generate(
                    source="dream",
                    trigger="startup",
                    image_paths=_resolve_image_paths(recent),
                    instruction=(
                        "More than eight hours have passed since the last "
                        "session. This is your corpus. Generate from "
                        "visual input alone — the unconscious speaks "
                        "without the conscious narrating first."
                    ),
                )
                entries.append(entry)
                steps.append(f"dream ({hours:.1f}h since last session)")
            else:
                steps.append(f"no dream/synthesis needed ({hours:.1f}h since last session)")
        else:
            steps.append("no prior session — dream/synthesis skipped")
    except Exception as exc:
        steps.append(f"dream/synthesis check failed ({exc})")

    return {"ran": True, "entries": entries, "message": " · ".join(steps)}
