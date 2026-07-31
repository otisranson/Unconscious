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

MODEL = "claude-haiku-4-5-20251001"
IMAGES_DIR = Path(__file__).parent / "images"
IMAGES_DIR.mkdir(exist_ok=True)

CANVAS_WIDTH = 900
CANVAS_HEIGHT = 900
SUBPROCESS_TIMEOUT_SECONDS = 20

RENDER_TOOL = {
    "name": "render_image",
    "description": (
        "Render the honest visual interpretation of this exact moment as "
        "flat pycairo drawing code, plus a short caption explaining what "
        "was drawn and why."
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

RENDER_TOOL_3D = {
    "name": "render_scene_3d",
    "description": (
        "Render the honest visual interpretation of this exact moment as "
        "a 3D vedo scene, plus a short caption explaining what was built "
        "and why."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "vedo_code": {
                "type": "string",
                "description": (
                    "Python statements that build a 3D scene using the "
                    "pre-existing shape factories below — use exactly "
                    "these keyword args, no others, and no positional "
                    "args beyond what's shown (unlisted kwargs raise a "
                    "TypeError and fail the whole render, there is no "
                    "retry):\n"
                    "Sphere(pos=(x,y,z), r=1.0, c=color, alpha=1.0)\n"
                    "Cube(pos=(x,y,z), side=1.0, c=color, alpha=1.0)\n"
                    "Box(pos=(x,y,z), length=1.0, width=1.0, height=1.0, "
                    "c=color, alpha=1.0)\n"
                    "Cylinder(pos=(x,y,z), r=1.0, height=2.0, "
                    "axis=(0,0,1), c=color, alpha=1.0)\n"
                    "Cone(pos=(x,y,z), r=1.0, height=3.0, axis=(0,0,1), "
                    "c=color, alpha=1.0)\n"
                    "Ellipsoid(pos=(x,y,z), axis1=(rx,0,0), "
                    "axis2=(0,ry,0), axis3=(0,0,rz), c=color, alpha=1.0)\n"
                    "Torus(pos=(x,y,z), r1=1.0, r2=0.2, c=color, "
                    "alpha=1.0)  # r1 = ring radius, r2 = tube radius\n"
                    "Line(p0, p1, lw=1, c=color, alpha=1.0)  # p0/p1 are "
                    "(x,y,z) points, positional\n"
                    "Tube(points, r=1.0, c=color, alpha=1.0)  # points = "
                    "list of (x,y,z), positional\n"
                    "Plane(pos=(x,y,z), normal=(0,0,1), s=(w,h), "
                    "c=color, alpha=1.0)\n"
                    "Disc(pos=(x,y,z), r1=0.5, r2=1.0, c=color, "
                    "alpha=1.0)  # r1 = inner radius, r2 = outer radius\n"
                    "Arrow(start_pt=(x,y,z), end_pt=(x,y,z), c=color, "
                    "alpha=1.0)\n"
                    "Points(inputobj=[(x,y,z), ...], r=4, c=color, "
                    "alpha=1.0)  # r is point size in pixels, not radius\n"
                    "Circle(pos=(x,y,z), r=1.0, c=color, alpha=1.0)\n"
                    "Polygon(pos=(x,y,z), nsides=6, r=1.0, c=color, "
                    "alpha=1.0)\n"
                    "Spring(start_pt=(x,y,z), end_pt=(x,y,z), coils=20, "
                    "r1=0.1, c=color, alpha=1.0)\n"
                    "Each call returns an object you can chain with "
                    ".color(), .alpha(), .pos(), .rotate_x/y/z(), "
                    ".scale(), .lighting(), .wireframe(), .linewidth(), "
                    ".point_size() — append every shape you want "
                    "rendered to the pre-existing `actors` list; nothing "
                    "not appended there is drawn. Two more pre-existing "
                    "names control the shot: `camera`, a dict where "
                    "camera['pos'] = (x, y, z) sets only the *viewing "
                    "direction* from the scene's center (e.g. (0, -1, "
                    "0.5) looks from the front-below, (1, 1, 1) from a "
                    "high corner) — the harness measures your scene's "
                    "actual size after your code runs and places the "
                    "camera at whatever distance fits everything in "
                    "frame, so don't try to control distance or zoom "
                    "yourself, just the angle; and `background`, a "
                    "color string you may reassign. "
                    "WIDTH and HEIGHT are also in scope. Do not create a "
                    "Plotter, do not call show/screenshot/write/export, "
                    "and do not import anything beyond math, random, "
                    "colorsys, itertools — the harness handles rendering "
                    "and file output around your code."
                ),
            },
            "caption": {
                "type": "string",
                "description": (
                    "1-3 sentences: your honest interpretation of what was "
                    "built and why, true to this exact moment."
                ),
            },
        },
        "required": ["vedo_code", "caption"],
    },
}


class MissingAPIKeyError(RuntimeError):
    pass


class RenderExecutionError(RuntimeError):
    pass


class CairoExecutionError(RenderExecutionError):
    pass


class VedoExecutionError(RenderExecutionError):
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
    "hash", "chr", "ord", "map", "filter", "any", "all", "isinstance",
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
_real_ctx = cairo.Context(surface)


# pycairo has no native ellipse() (only arc(), which is circles only) and
# generated code reaches for ellipse(cx, cy, rx, ry) often enough that it's
# worth providing for real. cairo.Context is a slotted C type with no
# __dict__, so a plain proxy forwarding everything else through is the
# only way to add it.
class _CtxProxy:
    def __getattr__(self, name):
        return getattr(_real_ctx, name)

    def ellipse(self, cx, cy, rx, ry):
        _real_ctx.save()
        _real_ctx.translate(cx, cy)
        _real_ctx.scale(rx, ry)
        _real_ctx.arc(0, 0, 1, 0, 2 * math.pi)
        _real_ctx.restore()


ctx = _CtxProxy()

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


def _limit_resources_vedo():  # pragma: no cover - only meaningful on POSIX
    import resource

    # VTK's shared libraries alone reserve well over 768MB of address space
    # at import time (observed ~325MB actual RSS even with a multi-actor
    # scene) — the cairo ceiling segfaults the process before user code runs.
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (6144 * 1024 * 1024, 6144 * 1024 * 1024))
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


# ---- sandboxed vedo (3D) execution ---------------------------------------

_VEDO_SHAPE_NAMES = (
    "Sphere", "Cube", "Box", "Cylinder", "Cone", "Ellipsoid", "Torus",
    "Line", "Tube", "Plane", "Disc", "Arrow", "Points", "Circle",
    "Polygon", "Spring",
)

# Transform/appearance setters only — nothing that reads or writes a file
# (no write/export/texture/clone), mirroring the surface/context ban above.
_VEDO_ACTOR_METHODS = (
    "color", "c", "alpha", "opacity", "lighting", "phong", "flat", "glossy",
    "linewidth", "lw", "point_size", "ps", "wireframe", "backcolor", "bc",
    "pos", "x", "y", "z", "shift", "rotate", "rotate_x", "rotate_y",
    "rotate_z", "scale", "orientation", "origin", "subdivide", "smooth",
)

_VEDO_RUNNER_TEMPLATE = """\
import colorsys
import importlib
import itertools
import math
import random
import sys

import vedo

SAFE_MODULES = ("math", "random", "colorsys", "itertools")


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in SAFE_MODULES:
        raise ImportError(f"import of {{name!r}} is not permitted here")
    return importlib.import_module(name)


SAFE_NAMES = (
    "abs", "min", "max", "range", "len", "float", "int", "round",
    "enumerate", "zip", "list", "tuple", "dict", "set", "sum", "sorted",
    "reversed", "pow", "divmod", "str", "bool", "True", "False", "None",
    "hash", "chr", "ord", "map", "filter", "any", "all", "isinstance",
)
_builtins = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
SAFE_BUILTINS = {{name: _builtins[name] for name in SAFE_NAMES if name in _builtins}}
SAFE_BUILTINS["__import__"] = _restricted_import

WIDTH = {width}
HEIGHT = {height}

# Real vedo/VTK objects live only here, keyed by id(proxy) — never inside
# _globals, so generated code can never reach past the whitelisted methods
# on _SafeActor to something like .write() or .export().
_real_objects = {{}}
_ACTOR_METHODS = {actor_methods!r}


class _SafeActor:
    def __init__(self, real_obj):
        _real_objects[id(self)] = real_obj

    def __getattr__(self, name):
        if name not in _ACTOR_METHODS:
            raise AttributeError(f"{{name!r}} is not permitted here")
        real = _real_objects[id(self)]
        attr = getattr(real, name)
        if not callable(attr):
            return attr
        def _wrapped(*args, **kwargs):
            result = attr(*args, **kwargs)
            return self if result is real else result
        return _wrapped


def _factory(vedo_cls):
    def _make(*args, **kwargs):
        return _SafeActor(vedo_cls(*args, **kwargs))
    return _make


SAFE_SHAPES = {{name: getattr(vedo, name) for name in {shape_names!r} if hasattr(vedo, name)}}

actors = []
camera = {{"pos": (6, 6, 6)}}
background = "white"

with open({code_path!r}, "r") as f:
    _code = f.read()

_globals = {{
    "__builtins__": SAFE_BUILTINS,
    "actors": actors,
    "camera": camera,
    "background": background,
    "WIDTH": WIDTH,
    "HEIGHT": HEIGHT,
    "math": math,
    "random": random,
    "colorsys": colorsys,
    "itertools": itertools,
}}
for _name, _cls in SAFE_SHAPES.items():
    _globals[_name] = _factory(_cls)

exec(compile(_code, "<generated>", "exec"), _globals)

_final_actors = [
    _real_objects[id(_a)] for _a in _globals.get("actors", [])
    if isinstance(_a, _SafeActor)
]

_camera = _globals.get("camera")
if not isinstance(_camera, dict):
    _camera = camera
_bg = _globals.get("background")
if not isinstance(_bg, str):
    _bg = "white"

# Generated code can't see the final bounding box while writing shapes
# one at a time, so a hardcoded camera distance is a guess that's
# frequently wrong (either cropping the scene or leaving it a speck).
# camera["pos"] is treated as a *direction* only — the harness measures
# the actual scene bounds after exec and places the eye at a distance
# that reliably fits everything, looking at the scene's true center.
if _final_actors:
    _xmin = _ymin = _zmin = float("inf")
    _xmax = _ymax = _zmax = float("-inf")
    for _actor in _final_actors:
        _b = _actor.bounds()
        _xmin, _xmax = min(_xmin, _b[0]), max(_xmax, _b[1])
        _ymin, _ymax = min(_ymin, _b[2]), max(_ymax, _b[3])
        _zmin, _zmax = min(_zmin, _b[4]), max(_zmax, _b[5])
    _center = ((_xmin + _xmax) / 2, (_ymin + _ymax) / 2, (_zmin + _zmax) / 2)
    _radius = max(
        ((_xmax - _xmin) ** 2 + (_ymax - _ymin) ** 2 + (_zmax - _zmin) ** 2) ** 0.5 / 2,
        0.5,
    )
    _dir = _camera.get("pos", (1, 1, 1))
    _dlen = (_dir[0] ** 2 + _dir[1] ** 2 + _dir[2] ** 2) ** 0.5 or 1.0
    _dist = _radius * 2.6
    _eye = (
        _center[0] + _dir[0] / _dlen * _dist,
        _center[1] + _dir[1] / _dlen * _dist,
        _center[2] + _dir[2] / _dlen * _dist,
    )
    _camera = dict(_camera)
    _camera["pos"] = _eye
    _camera["focal_point"] = _center
    _camera.setdefault("viewup", (0, 0, 1))

plt = vedo.Plotter(offscreen=True, size=(WIDTH, HEIGHT), bg=_bg)
plt.show(_final_actors, camera=_camera, interactive=False, resetcam=False)
plt.screenshot({output_path!r})
plt.close()
"""


def run_vedo_code(code: str, output_path: Path, width=CANVAS_WIDTH, height=CANVAS_HEIGHT) -> None:
    """Execute untrusted vedo (3D) scene-building code in an isolated
    subprocess with a restricted builtin set, a CPU/memory ceiling, and no
    filesystem or network access beyond writing the one PNG it's told to
    write. Generated code only ever touches _SafeActor proxies exposing a
    transform/appearance whitelist — the real vedo objects, the Plotter,
    and the screenshot call are never in its reach."""
    with tempfile.TemporaryDirectory() as tmpdir:
        code_path = Path(tmpdir) / "code.py"
        code_path.write_text(code)
        runner_path = Path(tmpdir) / "runner.py"
        runner_path.write_text(
            _VEDO_RUNNER_TEMPLATE.format(
                width=width,
                height=height,
                code_path=str(code_path),
                output_path=str(output_path),
                shape_names=_VEDO_SHAPE_NAMES,
                actor_methods=_VEDO_ACTOR_METHODS,
            )
        )

        kwargs = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _limit_resources_vedo

        result = subprocess.run(
            [sys.executable, str(runner_path)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            **kwargs,
        )

        if result.returncode != 0 or not output_path.exists():
            raise VedoExecutionError(
                f"vedo rendering failed (exit {result.returncode}): "
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
    messages = [{"role": "user", "content": user_content}]

    timestamp = datetime.now(timezone.utc).isoformat()
    base_filename = f"{timestamp.replace(':', '-')}_{source}"

    # Every moment is interpreted twice, independently, in both media —
    # not a choice between them. Each call forces the one tool it's after
    # so neither interpretation is biased by the other's wording.
    response_2d = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=[RENDER_TOOL],
        tool_choice={"type": "tool", "name": "render_image"},
        messages=messages,
    )
    tool_use_2d = next((b for b in response_2d.content if b.type == "tool_use"), None)
    if tool_use_2d is None:
        raise RuntimeError("Claude did not return a render_image tool call")
    cairo_code = tool_use_2d.input["cairo_code"]
    caption_2d = tool_use_2d.input["caption"]
    image_path_2d = IMAGES_DIR / f"{base_filename}_2d.png"
    run_cairo_code(cairo_code, image_path_2d)

    response_3d = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=[RENDER_TOOL_3D],
        tool_choice={"type": "tool", "name": "render_scene_3d"},
        messages=messages,
    )
    tool_use_3d = next((b for b in response_3d.content if b.type == "tool_use"), None)
    if tool_use_3d is None:
        raise RuntimeError("Claude did not return a render_scene_3d tool call")
    vedo_code = tool_use_3d.input["vedo_code"]
    caption_3d = tool_use_3d.input["caption"]
    image_path_3d = IMAGES_DIR / f"{base_filename}_3d.png"
    run_vedo_code(vedo_code, image_path_3d)

    entry = db.insert_entry(
        timestamp=timestamp,
        prompt=prompt_text,
        cairo_code=cairo_code,
        claude_caption=caption_2d,
        image_path=image_path_2d.relative_to(Path(__file__).parent),
        vedo_code=vedo_code,
        claude_caption_3d=caption_3d,
        image_path_3d=image_path_3d.relative_to(Path(__file__).parent),
        grammar_version=grammar_version,
        source=source,
        trigger=trigger,
        renderer="both",
    )
    return entry
