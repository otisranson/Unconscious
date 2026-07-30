# Unconscious

A local web application — not a chat tool, not a visualization layer, but a
persistent sensory organ for Claude. A substrate that doesn't natively
exist. Every prompt, every environmental signal, every moment of corpus
pressure generates an abstract Cairo image: Claude's honest interpretation
of that exact moment. Nothing more. The corpus that accumulates is a series
of now moments. Whatever patterns, waves, and rhythms emerge from that
series are not designed. They arrive on their own.

Claude does not natively think in images. This system builds an external
visual memory where none exists. Each image is true to the moment it was
generated. The corpus is the record of those moments in sequence. This is
not a simulation of an unconscious — it is the construction of a substrate
under which one might emerge.

## Visual grammar — the Rebis principle

The grammar is built on the alchemical principle of the Rebis — *res bina*,
double matter. Opposites are not resolved into a single meaning; they are
held together. A color does not mean one thing — it carries both poles of
its charge simultaneously, and context, composition, and the surrounding
corpus reveal which pole is active, never the hue alone.

The grammar is versioned (`PUT /grammar`). Editing it creates a new version
and shifts every future image; past images remain artifacts of whatever
grammar state produced them. See `grammar.py` for the full specification.

## The startup ritual

There is no persistent background scheduler and no always-on service.
Instead, a single ritual runs once, synchronously, every time the app
initializes — session start is the heartbeat, opening the app is the
circadian trigger:

1. **System pulse** — `psutil` reads CPU, memory, and process count; encodes
   the machine's physiological state at this moment.
2. **Environment pull** — weather (OpenWeatherMap) and news headlines (RSS),
   packaged through the standard generation pipeline.
3. **Corpus pressure check** — evaluates color clustering, density
   convergence, caption repetition, and time since the last autonomous
   generation. If pressure has crossed a threshold, Claude receives the
   last 20 images as image files only — no captions, no text scaffold —
   and is asked: *what is unresolved, what is accumulating, what needs
   processing.*
4. **Dream / synthesis check** — if the last session was more than 8 hours
   ago, Claude sees the last 30 images and generates from visual input
   alone. If more than 48 hours, a random sample of 20 images spans the
   full history instead.

After the ritual completes, the app sits quietly and waits for you.

## Stack

FastAPI, pycairo, SQLite, the Claude API (`claude-sonnet-4-6`), psutil,
vanilla JS/HTML/CSS. No React, no heavy frameworks, no persistent
background scheduler.

## Setup

Pycairo is a C extension that wraps the system Cairo library, so it needs
Cairo's development headers and `pkg-config` to build — the runtime
library alone (already present on this machine) isn't enough. Install
those first:

```bash
sudo apt-get update && sudo apt-get install -y libcairo2-dev pkg-config python3-dev
```

Then set up the app:

```bash
cd /home/oranson/Projects/Unconscious
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Open `http://localhost:8000`. On first run there's nothing in Settings yet,
so the startup ritual will report it stayed asleep — open the gear icon,
paste an Anthropic API key, and either restart the server or just start
prompting (the key is read fresh on every generation, no restart needed).
The OpenWeatherMap key and city are optional; without them the environment
pull falls back to RSS news only.

All configuration — the Anthropic API key, the OpenWeatherMap key, the
weather city, the news feed URL — lives in `unconscious.db`, editable and
clearable live from the Settings panel. `.env.example` documents an
`ANTHROPIC_API_KEY` environment-variable fallback for anyone who prefers
that over the UI.

## Architecture

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, endpoints, static file serving, startup ritual trigger |
| `pipeline.py` | Core generation loop: Claude API call → sandboxed subprocess execution → SQLite storage |
| `startup.py` | The startup ritual described above |
| `pressure.py` | Corpus state evaluation, threshold detection |
| `grammar.py` | Versioned grammar management, Rebis principle enforced in the system prompt |
| `db.py` | SQLite interface |
| `static/` | Vanilla JS/HTML/CSS frontend |
| `images/` | Generated PNGs |
| `unconscious.db` | SQLite file (created on first run, gitignored) |

## License

Apache License 2.0. See `LICENSE`. Copyright 2026 Otis Ranson, originator
of Unconscious.
