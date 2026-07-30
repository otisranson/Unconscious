"""
Claude's Unconscious
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.

pipeline.py — the core generation loop: Claude API call -> sandboxed
pycairo subprocess execution -> PNG + SQLite storage. Every image in the
corpus, whatever its source, passes through generate() below.
"""

import base64
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import anthropic

import db
import grammar

MODEL = "claude-sonnet-4-6"
IMAGES_DIR = Path(__file__).parent / "images"
IMAGES_DIR.mkdir(exist_ok=True)

CANVAS_WIDTH = 900
CANVAS_HEIGHT = 900
SUBPROCESS_TIMEOUT_SECONDS = 20

RENDER_TOOL = {
    "name": "render_image",
    "description": (
        "Render the honest visual interpretation of this exact moment as "
        "pycairo drawing code, plus a short caption explaining what was "
        "drawn and why."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cairo_code": {
                "type": "string",
                "description": (
                    "Python statements that draw onto the pre-existing "
                    "cairo.Context `ctx` (WIDTH and HEIGHT are also in "
                    "scope). Do not create the surface, the context, or "
                    "write any file."
                ),
            },
            "caption": {
                "type": "string",
                "description": (
                    "1-3 sentences: your honest interpretation of what was "
                    "drawn and why, true to this exact moment."
                ),
            },
        },
        "required": ["cairo_code", "caption"],
    },
}


class MissingAPIKeyError(RuntimeError):
    pass


class CairoExecutionError(RuntimeError):
    pass


def get_api_key() -> str | None:
    """DB setting (editable live from the UI) takes precedence over the
    ANTHROPIC_API_KEY environment variable."""
    return db.get_setting("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")


def get_client() -> anthropic.Anthropic:
    key = get_api_key()
    if not key:
        raise MissingAPIKeyError(
            "No Anthropic API key configured. Add one in Settings before "
            "generating."
        )
    return anthropic.Anthropic(api_key=key)


# ---- sandboxed pycairo execution ----------------------------------------

_RUNNER_TEMPLATE = """\
import colorsys
import importlib
import itertools
import math
import random
import sys

import cairo

SAFE_MODULES = ("math", "random", "colorsys", "itertools")


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in SAFE_MODULES:
        raise ImportError(f"import of {{name!r}} is not permitted here")
    return importlib.import_module(name)


SAFE_NAMES = (
    "abs", "min", "max", "range", "len", "float", "int", "round",
    "enumerate", "zip", "list", "tuple", "dict", "set", "sum", "sorted",
    "reversed", "pow", "divmod", "str", "bool", "True", "False", "None",
)
_builtins = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
SAFE_BUILTINS = {{name: _builtins[name] for name in SAFE_NAMES if name in _builtins}}
SAFE_BUILTINS["__import__"] = _restricted_import

# Only pattern/gradient classes and drawing constants — deliberately not
# ImageSurface/PDFSurface/SVGSurface/PSSurface/Context, which could create
# new surfaces backed by arbitrary filesystem paths.
_SAFE_CAIRO_ATTRS = (
    "LinearGradient", "RadialGradient", "Matrix",
    "FILL_RULE_WINDING", "FILL_RULE_EVEN_ODD",
    "LINE_CAP_BUTT", "LINE_CAP_ROUND", "LINE_CAP_SQUARE",
    "LINE_JOIN_MITER", "LINE_JOIN_ROUND", "LINE_JOIN_BEVEL",
    "EXTEND_NONE", "EXTEND_REPEAT", "EXTEND_REFLECT", "EXTEND_PAD",
    "OPERATOR_OVER", "OPERATOR_ADD", "OPERATOR_MULTIPLY", "OPERATOR_SCREEN",
    "OPERATOR_DIFFERENCE", "OPERATOR_OVERLAY",
    "ANTIALIAS_DEFAULT", "ANTIALIAS_NONE", "ANTIALIAS_GRAY",
    "ANTIALIAS_SUBPIXEL", "ANTIALIAS_GOOD", "ANTIALIAS_BEST",
)


class _SafeCairo:
    pass


safe_cairo = _SafeCairo()
for _name in _SAFE_CAIRO_ATTRS:
    if hasattr(cairo, _name):
        setattr(safe_cairo, _name, getattr(cairo, _name))

WIDTH = {width}
HEIGHT = {height}

surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
ctx = cairo.Context(surface)

with open({code_path!r}, "r") as f:
    _code = f.read()

_globals = {{
    "__builtins__": SAFE_BUILTINS,
    "ctx": ctx,
    "WIDTH": WIDTH,
    "HEIGHT": HEIGHT,
    "cairo": safe_cairo,
    "math": math,
    "random": random,
    "colorsys": colorsys,
    "itertools": itertools,
}}

exec(compile(_code, "<generated>", "exec"), _globals)

surface.write_to_png({output_path!r})
"""


def _limit_resources():  # pragma: no cover - only meaningful on POSIX
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))


def run_cairo_code(code: str, output_path: Path, width=CANVAS_WIDTH, height=CANVAS_HEIGHT) -> None:
    """Execute untrusted pycairo drawing code in an isolated subprocess with
    a restricted builtin set, a CPU/memory ceiling, and no filesystem or
    network access beyond writing the one PNG it's told to write."""
    with tempfile.TemporaryDirectory() as tmpdir:
        code_path = Path(tmpdir) / "code.py"
        code_path.write_text(code)
        runner_path = Path(tmpdir) / "runner.py"
        runner_path.write_text(
            _RUNNER_TEMPLATE.format(
                width=width,
                height=height,
                code_path=str(code_path),
                output_path=str(output_path),
            )
        )

        kwargs = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _limit_resources

        result = subprocess.run(
            [sys.executable, str(runner_path)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            **kwargs,
        )

        if result.returncode != 0 or not output_path.exists():
            raise CairoExecutionError(
                f"cairo rendering failed (exit {result.returncode}): "
                f"{result.stderr.strip()[-2000:]}"
            )


# ---- prompt / context assembly ------------------------------------------

def _moment_line() -> str:
    now = datetime.now().astimezone()
    hour = now.hour
    if 5 <= hour < 11:
        part = "morning"
    elif 11 <= hour < 17:
        part = "midday"
    elif 17 <= hour < 21:
        part = "evening"
    else:
        part = "night"
    return f"[Moment: {now.strftime('%Y-%m-%d %H:%M %Z').strip()}, {part}]"


def _recent_context_text(limit=15) -> str:
    recent = db.recent_entries(limit=limit)
    if not recent:
        return "(the corpus is empty — this is the first moment)"
    lines = []
    for e in recent:
        label = e["prompt"] or f"[{e['source']}]"
        caption = e["claude_caption"] or ""
        lines.append(f"- {label}: {caption}")
    return "\n".join(lines)


def _image_content_blocks(image_paths):
    blocks = []
    for p in image_paths:
        data = base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data},
        })
    return blocks


def _build_user_content(prompt_text, image_paths, instruction, include_context):
    content = [{"type": "text", "text": _moment_line()}]

    if image_paths:
        content.extend(_image_content_blocks(image_paths))
        content.append({"type": "text", "text": instruction})
    else:
        if include_context:
            content.append({
                "type": "text",
                "text": f"Recent corpus context:\n{_recent_context_text()}",
            })
        content.append({"type": "text", "text": instruction})
        if prompt_text:
            content.append({"type": "text", "text": prompt_text})

    return content


# ---- generation -----------------------------------------------------------

def generate(source, trigger, prompt_text=None, image_paths=None, instruction=None):
    """Generate one entry. This is the single path every source/trigger
    combination in the app runs through."""
    if source not in db.VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")
    if trigger not in db.VALID_TRIGGERS:
        raise ValueError(f"invalid trigger: {trigger}")

    client = get_client()
    grammar_version = grammar.get_current_version()
    system_prompt = grammar.get_system_prompt()

    if instruction is None:
        instruction = "This is the current moment. Draw your honest interpretation of it."

    include_context = source == "prompt"
    user_content = _build_user_content(prompt_text, image_paths, instruction, include_context)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=[RENDER_TOOL],
        tool_choice={"type": "tool", "name": "render_image"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return a render_image tool call")

    cairo_code = tool_use.input["cairo_code"]
    caption = tool_use.input["caption"]

    timestamp = datetime.now(timezone.utc).isoformat()
    image_filename = f"{timestamp.replace(':', '-')}_{source}.png"
    image_path = IMAGES_DIR / image_filename

    run_cairo_code(cairo_code, image_path)

    entry = db.insert_entry(
        timestamp=timestamp,
        prompt=prompt_text,
        cairo_code=cairo_code,
        claude_caption=caption,
        image_path=image_path.relative_to(Path(__file__).parent),
        grammar_version=grammar_version,
        source=source,
        trigger=trigger,
    )
    return entry
