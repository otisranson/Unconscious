"""
Claude's Unconscious
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.

main.py — FastAPI app: endpoints, static file serving, and the one-time
startup ritual trigger. Run with: uvicorn main:app --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import grammar
import pipeline
import startup

PROJECT_ROOT = Path(__file__).parent
STATIC_DIR = PROJECT_ROOT / "static"

SETTINGS_KEYS = ("anthropic_api_key", "openweather_api_key", "weather_city", "news_rss_url")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    grammar.ensure_initialized()
    app.state.startup_result = startup.run_startup_ritual()
    yield


app = FastAPI(title="Claude's Unconscious", lifespan=lifespan)


# ---- request bodies -------------------------------------------------------

class PromptRequest(BaseModel):
    prompt: str


class AnnotateRequest(BaseModel):
    annotation: str


class GrammarRequest(BaseModel):
    system_prompt: str


class SettingsRequest(BaseModel):
    anthropic_api_key: Optional[str] = None
    openweather_api_key: Optional[str] = None
    weather_city: Optional[str] = None
    news_rss_url: Optional[str] = None


# ---- helpers ----------------------------------------------------------

def _entry_to_json(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "timestamp": entry["timestamp"],
        "prompt": entry["prompt"],
        "claude_caption": entry["claude_caption"],
        "user_annotation": entry["user_annotation"],
        "grammar_version": entry["grammar_version"],
        "source": entry["source"],
        "trigger": entry["trigger"],
        "image_url": f"/images/{entry['id']}",
    }


# ---- generation endpoints -----------------------------------------------

@app.post("/prompt")
def create_prompt(body: PromptRequest):
    try:
        entry = pipeline.generate(source="prompt", trigger="user", prompt_text=body.prompt)
    except pipeline.MissingAPIKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except pipeline.CairoExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except anthropic.AuthenticationError as exc:
        raise HTTPException(
            status_code=401, detail="The Anthropic API key was rejected — check it in Settings."
        ) from exc
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
    return _entry_to_json(entry)


@app.post("/annotate/{entry_id}")
def annotate_entry(entry_id: int, body: AnnotateRequest):
    entry = db.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    updated = db.set_annotation(entry_id, body.annotation)
    return _entry_to_json(updated)


@app.delete("/entry/{entry_id}")
def delete_entry(entry_id: int):
    deleted = db.delete_entry(entry_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="entry not found")
    image_path = PROJECT_ROOT / deleted["image_path"]
    if image_path.exists():
        image_path.unlink()
    return {"deleted": entry_id}


@app.get("/images/{entry_id}")
def get_image(entry_id: int):
    entry = db.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    image_path = PROJECT_ROOT / entry["image_path"]
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(image_path, media_type="image/png")


@app.get("/history")
def get_history(source: Optional[str] = None):
    if source is not None and source not in db.VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"invalid source: {source}")
    entries = db.list_entries(source=source, order="ASC")
    return [_entry_to_json(e) for e in entries]


@app.get("/startup")
def get_startup_result():
    result = getattr(app.state, "startup_result", {"ran": False, "entries": [], "message": ""})
    return {**result, "entries": [_entry_to_json(e) for e in result["entries"]]}


# ---- grammar endpoints ----------------------------------------------------

@app.get("/grammar")
def get_grammar():
    return {
        "version": grammar.get_current_version(),
        "system_prompt": grammar.get_current_grammar_text(),
    }


@app.put("/grammar")
def put_grammar(body: GrammarRequest):
    version = grammar.update_grammar(body.system_prompt)
    return {"version": version, "system_prompt": body.system_prompt}


# ---- settings endpoints (live-editable API keys) --------------------------
#
# GET returns the raw current values so the UI can populate its text boxes
# directly. PUT always overwrites: a non-empty value sets it, an empty
# value clears it — the field you send is the field's new state, full stop.

@app.get("/settings")
def get_settings():
    return {key: db.get_setting(key, "") for key in SETTINGS_KEYS}


@app.put("/settings")
def put_settings(body: SettingsRequest):
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value:
            db.set_setting(key, value)
        else:
            db.delete_setting(key)
    return get_settings()


@app.get("/settings/test")
def test_environment_signals():
    """Live-checks the weather API and RSS feed directly — no Claude call,
    no cost — so a bad key/city/URL can be caught right after saving it."""
    weather = startup.weather_text()
    news = startup.news_text()
    return {
        "weather": weather or "Not configured — add an OpenWeatherMap key and city above.",
        "news": news or "No headlines reachable.",
    }


# ---- static frontend -------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
