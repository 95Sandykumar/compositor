#!/usr/bin/env python3
"""Compositor: a headless, layered image editor.

Everything that happens AFTER generation: text placement, layering, masking,
layer effects, adjustments, crop, canvas resize, export.

A document is a JSON project. Rendering the same project twice gives the same
pixels. Quick subcommands build a project in memory and go through the same
renderer, so there is exactly one code path and no drift between them.

Run `compositor.py <subcommand> --help` for arguments.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

Image.MAX_IMAGE_PIXELS = None
LANCZOS = Image.Resampling.LANCZOS
EPS = 1e-6

# ---------------------------------------------------------------------------
# colour
# ---------------------------------------------------------------------------

NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#d02020", "green": "#1f8a3c",
    "blue": "#1f4fd8", "gold": "#c8a04d", "cream": "#f5f0e6", "charcoal": "#1a1a1a",
    "ivory": "#fffff0", "navy": "#0b1d3a", "crimson": "#8b0000", "silver": "#c0c0c0",
    "grey": "#808080", "gray": "#808080", "transparent": "#00000000",
    "none": "#00000000",
}


def parse_color(value, default=(0, 0, 0, 255)):
    """'#rgb' / '#rrggbb' / '#rrggbbaa' / name / [r,g,b(,a)] -> RGBA tuple."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        vals = [int(v) for v in value]
        if len(vals) == 3:
            vals.append(255)
        if len(vals) != 4:
            raise ValueError(f"colour list needs 3 or 4 values, got {value!r}")
        return tuple(max(0, min(255, v)) for v in vals)
    text = str(value).strip().lower()
    text = NAMED.get(text, text).lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) == 4:
        text = "".join(c * 2 for c in text)
    if len(text) == 6:
        text += "ff"
    if len(text) != 8 or not re.fullmatch(r"[0-9a-f]{8}", text):
        raise ValueError(f"cannot parse colour {value!r}")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------

FONT_DIRS = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", "") or "/nonexistent") / "Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
    Path.home() / ".fonts",
    Path.home() / "Library/Fonts",
]

FONT_ALIASES = {
    "arial": "arial.ttf", "arial-bold": "arialbd.ttf", "arial-italic": "ariali.ttf",
    "arial-black": "ariblk.ttf", "arial-narrow": "arialn.ttf",
    "georgia": "georgia.ttf", "georgia-bold": "georgiab.ttf",
    "georgia-italic": "georgiai.ttf",
    "times": "times.ttf", "times-bold": "timesbd.ttf", "times-italic": "timesi.ttf",
    "garamond": "gara.ttf", "garamond-bold": "garabd.ttf",
    "palatino": "pala.ttf", "palatino-bold": "palab.ttf",
    "bookman": "bookos.ttf", "bookman-bold": "bookosb.ttf",
    "baskerville": "baskvill.ttf",
    "verdana": "verdana.ttf", "verdana-bold": "verdanab.ttf",
    "tahoma": "tahoma.ttf", "tahoma-bold": "tahomabd.ttf",
    "calibri": "calibri.ttf", "calibri-bold": "calibrib.ttf",
    "segoe": "segoeui.ttf", "segoe-bold": "segoeuib.ttf",
    "segoe-semibold": "seguisb.ttf", "segoe-black": "seguibl.ttf",
    "trebuchet": "trebuc.ttf", "trebuchet-bold": "trebucbd.ttf",
    "courier": "cour.ttf", "courier-bold": "courbd.ttf",
    "impact": "impact.ttf",
    "constantia": "constan.ttf", "constantia-bold": "constanb.ttf",
    "cambria": "cambria.ttc", "cambria-bold": "cambriab.ttf",
    "candara": "candara.ttf", "corbel": "corbel.ttf",
    "sylfaen": "sylfaen.ttf",
    "playfair": "playfairdisplay-regular.ttf",
    "cinzel": "cinzel-regular.ttf",
}

_FONT_INDEX = None


def font_index():
    """filename.lower() -> Path, for every font file on the machine."""
    global _FONT_INDEX
    if _FONT_INDEX is None:
        idx = {}
        for d in FONT_DIRS:
            try:
                if not d.is_dir():
                    continue
            except OSError:
                continue
            for p in d.rglob("*"):
                if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    idx.setdefault(p.name.lower(), p)
        _FONT_INDEX = idx
    return _FONT_INDEX


def resolve_font(name, size):
    """Path, alias, or bare filename -> ImageFont. Any inexact match is
    reported on stderr; a wrong face must never pass silently."""
    size = max(1, int(round(size)))
    if not name:
        name = "arial"
    direct = Path(str(name)).expanduser()
    if direct.suffix and direct.exists():
        return ImageFont.truetype(str(direct), size)

    key = str(name).strip().lower()
    idx = font_index()
    tries = []
    if key in FONT_ALIASES:
        tries.append(FONT_ALIASES[key])
    if key.endswith((".ttf", ".otf", ".ttc")):
        tries.append(key)
    else:
        for suffix in (".ttf", ".otf", ".ttc"):
            tries.append(key + suffix)
            tries.append(key.replace(" ", "") + suffix)
            tries.append(key.replace(" ", "-") + suffix)
    for t in tries:
        if t in idx:
            return ImageFont.truetype(str(idx[t]), size)

    stem = re.sub(r"[^a-z0-9]", "", key)
    if stem:
        norm = {re.sub(r"[^a-z0-9]", "", Path(f).stem): f for f in sorted(idx)}
        if stem in norm:
            return ImageFont.truetype(str(idx[norm[stem]]), size)
        for n, fname in norm.items():
            if stem in n:
                print(f"compositor: font {name!r} not found exactly, using {fname}",
                      file=sys.stderr)
                return ImageFont.truetype(str(idx[fname]), size)
    raise SystemExit(
        f"compositor: font {name!r} not found. "
        f"Run: compositor.py fonts {key[:12]}"
    )


# ---------------------------------------------------------------------------
# image helpers
# ---------------------------------------------------------------------------

def load_image(src):
    p = Path(str(src)).expanduser()
    if not p.exists():
        raise SystemExit(f"compositor: source image not found: {p}")
    img = Image.open(p)
    img.load()
    return img.convert("RGBA")


def place(canvas, img, x, y):
    """alpha_composite `img` onto `canvas` at (x, y), clipping anything that
    falls outside. Pillow's alpha_composite rejects negative offsets and
    paste(mask=img) squares the alpha on a transparent canvas, so neither is
    safe on its own for a layer that bleeds off the edge."""
    x, y = int(round(x)), int(round(y))
    cw, ch = canvas.size
    left, top = max(0, -x), max(0, -y)
    right = min(img.width, cw - x)
    bottom = min(img.height, ch - y)
    if right <= left or bottom <= top:
        return canvas
    if (left, top, right, bottom) != (0, 0, img.width, img.height):
        img = img.crop((left, top, right, bottom))
    canvas.alpha_composite(img, (x + left, y + top))
    return canvas


def fit_image(img, box_w, box_h, mode="contain"):
    """Resize into box_w x box_h. 'cover' centre-crops to exactly fill."""
    iw, ih = img.size
    if not box_w and not box_h:
        return img
    if box_w and not box_h:
        box_h = max(1, round(ih * box_w / iw))
    if box_h and not box_w:
        box_w = max(1, round(iw * box_h / ih))
    box_w, box_h = max(1, int(box_w)), max(1, int(box_h))
    if mode == "none":
        return img
    if mode == "stretch":
        return img.resize((box_w, box_h), LANCZOS)
    scale = max(box_w / iw, box_h / ih) if mode == "cover" else min(box_w / iw, box_h / ih)
    resized = img.resize((max(1, round(iw * scale)), max(1, round(ih * scale))), LANCZOS)
    if mode == "cover":
        left = (resized.width - box_w) // 2
        top = (resized.height - box_h) // 2
        resized = resized.crop((left, top, left + box_w, top + box_h))
    return resized


# ---------------------------------------------------------------------------
# adjustments
# ---------------------------------------------------------------------------

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _hue_rotate(rgb, degrees):
    """Rotate hue about the luma axis. Keeps perceived brightness."""
    a = math.radians(float(degrees))
    c, s = math.cos(a), math.sin(a)
    m = np.array([
        [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
        [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
        [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072],
    ], dtype=np.float32)
    return rgb @ m.T


def apply_adjust(img, adj):
    """Pixel adjustments on an RGBA image. Alpha is always preserved."""
    if not adj:
        return img
    arr = np.asarray(img).astype(np.float32) / 255.0
    rgb, alpha = arr[..., :3].copy(), arr[..., 3:]

    if adj.get("invert"):
        rgb = 1.0 - rgb
    if adj.get("grayscale"):
        rgb = np.repeat((rgb @ LUMA)[..., None], 3, axis=2)

    lv = adj.get("levels")
    if lv:
        in_b = float(lv.get("in_black", 0)) / 255.0
        in_w = float(lv.get("in_white", 255)) / 255.0
        gamma = float(lv.get("gamma", 1.0)) or 1.0
        out_b = float(lv.get("out_black", 0)) / 255.0
        out_w = float(lv.get("out_white", 255)) / 255.0
        rgb = np.clip((rgb - in_b) / max(in_w - in_b, EPS), 0.0, 1.0) ** (1.0 / gamma)
        rgb = out_b + rgb * (out_w - out_b)

    if "exposure" in adj:
        rgb = rgb * (2.0 ** float(adj["exposure"]))
    if "brightness" in adj:
        rgb = rgb * float(adj["brightness"])
    if "contrast" in adj:
        rgb = (rgb - 0.5) * float(adj["contrast"]) + 0.5
    if "saturation" in adj:
        lum = (rgb @ LUMA)[..., None]
        rgb = lum + (rgb - lum) * float(adj["saturation"])
    if "hue" in adj:
        rgb = _hue_rotate(rgb, adj["hue"])
    if "temperature" in adj:
        t = float(adj["temperature"]) / 100.0
        rgb = rgb * np.array([1.0 + 0.18 * t, 1.0, 1.0 - 0.18 * t], dtype=np.float32)

    rgb = np.clip(rgb, 0.0, 1.0)

    if adj.get("grain"):
        rng = np.random.default_rng(int(adj.get("grain_seed", 7)))
        sigma = float(adj["grain"]) / 100.0 * 0.25
        rgb = np.clip(rgb + rng.normal(0.0, sigma, rgb.shape[:2])[..., None], 0.0, 1.0)

    out = Image.fromarray(
        (np.concatenate([rgb, alpha], axis=2) * 255.0 + 0.5).astype(np.uint8), "RGBA")

    if adj.get("blur"):
        out = out.filter(ImageFilter.GaussianBlur(float(adj["blur"])))
    if adj.get("sharpen"):
        pct = int(max(0.0, float(adj["sharpen"])) * 100)
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=pct, threshold=3))
    return out


# ---------------------------------------------------------------------------
# blend modes
# ---------------------------------------------------------------------------

def _soft_light(b, s):
    d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(np.maximum(b, 0.0)))
    return np.where(s <= 0.5,
                    b - (1 - 2 * s) * b * (1 - b),
                    b + (2 * s - 1) * (d - b))


def _hard_light(b, s):
    return np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))


BLENDS = {
    "normal": lambda b, s: s,
    "multiply": lambda b, s: b * s,
    "screen": lambda b, s: 1 - (1 - b) * (1 - s),
    "overlay": lambda b, s: _hard_light(s, b),
    "hard_light": _hard_light,
    "soft_light": _soft_light,
    "darken": np.minimum,
    "lighten": np.maximum,
    "difference": lambda b, s: np.abs(b - s),
    "exclusion": lambda b, s: b + s - 2 * b * s,
    "add": lambda b, s: np.clip(b + s, 0, 1),
    "linear_dodge": lambda b, s: np.clip(b + s, 0, 1),
    "subtract": lambda b, s: np.clip(b - s, 0, 1),
    "linear_burn": lambda b, s: np.clip(b + s - 1, 0, 1),
    "color_dodge": lambda b, s: np.where(s >= 1, 1.0, np.clip(b / np.maximum(1 - s, EPS), 0, 1)),
    "color_burn": lambda b, s: np.where(s <= 0, 0.0, 1 - np.clip((1 - b) / np.maximum(s, EPS), 0, 1)),
}


def composite(base, layer, mode="normal", opacity=1.0):
    """W3C source-over compositing with a separable blend function.
    base and layer are equally sized RGBA images."""
    if mode in (None, "", "normal") and opacity >= 1.0:
        out = base.copy()
        out.alpha_composite(layer)
        return out
    fn = BLENDS.get(mode or "normal")
    if fn is None:
        raise SystemExit(f"compositor: unknown blend mode {mode!r}. "
                         f"Known: {', '.join(sorted(BLENDS))}")
    b = np.asarray(base).astype(np.float32) / 255.0
    s = np.asarray(layer).astype(np.float32) / 255.0
    ba, sa = b[..., 3:], s[..., 3:] * float(opacity)
    blended = np.clip(fn(b[..., :3], s[..., :3]), 0.0, 1.0)
    src = (1 - ba) * s[..., :3] + ba * blended
    out_a = sa + ba * (1 - sa)
    out_rgb = (sa * src + ba * b[..., :3] * (1 - sa)) / np.maximum(out_a, EPS)
    stacked = np.concatenate([np.clip(out_rgb, 0, 1), np.clip(out_a, 0, 1)], axis=2)
    return Image.fromarray((stacked * 255.0 + 0.5).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# masks
# ---------------------------------------------------------------------------

def build_mask(spec, size):
    """Mask spec -> 'L' image the size of the canvas. White keeps, black hides."""
    if not spec:
        return None
    w, h = size
    mask = None
    if isinstance(spec, str):
        spec = {"src": spec}

    if spec.get("src"):
        src = load_image(spec["src"])
        channel = spec.get("channel", "luminance")
        if channel == "alpha":
            mask = src.getchannel("A")
        else:
            mask = src.convert("L")
        if mask.size != (w, h):
            mask = mask.resize((w, h), LANCZOS)
    elif spec.get("shape"):
        shape = spec["shape"]
        mask = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(mask)
        box = [int(v) for v in shape.get("box", [0, 0, w, h])]
        xy = [box[0], box[1], box[0] + box[2], box[1] + box[3]]
        if shape.get("type") == "ellipse":
            d.ellipse(xy, fill=255)
        elif shape.get("type") == "polygon":
            d.polygon([tuple(p) for p in shape["points"]], fill=255)
        else:
            d.rounded_rectangle(xy, radius=int(shape.get("radius", 0)), fill=255)
    elif spec.get("gradient"):
        g = dict(spec["gradient"])
        g.setdefault("stops", [[0.0, "#000000"], [1.0, "#ffffff"]])
        mask = render_gradient(g, (w, h)).convert("L")
    else:
        raise SystemExit("compositor: mask needs one of src / shape / gradient")

    if spec.get("feather"):
        mask = mask.filter(ImageFilter.GaussianBlur(float(spec["feather"])))
    if spec.get("invert"):
        mask = Image.eval(mask, lambda v: 255 - v)
    return mask


def apply_mask(buf, mask):
    if mask is None:
        return buf
    alpha = buf.getchannel("A")
    out = buf.copy()
    out.putalpha(Image.fromarray(
        (np.asarray(alpha).astype(np.float32) * np.asarray(mask).astype(np.float32)
         / 255.0 + 0.5).astype(np.uint8), "L"))
    return out


# ---------------------------------------------------------------------------
# gradients
# ---------------------------------------------------------------------------

def render_gradient(spec, size):
    """Linear or radial gradient as an RGBA image of `size`."""
    w, h = size
    stops = spec.get("stops")
    if not stops:
        stops = [[0.0, spec.get("from", "#000000")], [1.0, spec.get("to", "#ffffff")]]
    stops = sorted(([float(p), parse_color(c)] for p, c in stops), key=lambda s: s[0])

    # A gradient LAYER already carries type="gradient", so the shape lives in
    # `gradient_type`. A gradient MASK spec is free to use `type` directly.
    kind = spec.get("gradient_type")
    if kind is None:
        kind = spec["type"] if spec.get("type") in ("linear", "radial") else "linear"

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == "radial":
        cx = float(spec.get("cx", 0.5)) * w
        cy = float(spec.get("cy", 0.5)) * h
        radius = float(spec.get("radius", 0.5)) * max(w, h)
        t = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(radius, EPS)
    else:
        angle = math.radians(float(spec.get("angle", 90)))
        dx, dy = math.cos(angle), math.sin(angle)
        proj = xx * dx + yy * dy
        lo, hi = proj.min(), proj.max()
        t = (proj - lo) / max(hi - lo, EPS)
    t = np.clip(t, 0.0, 1.0)

    positions = np.array([s[0] for s in stops], dtype=np.float32)
    colours = np.array([s[1] for s in stops], dtype=np.float32)
    out = np.empty((h, w, 4), dtype=np.float32)
    for ch in range(4):
        out[..., ch] = np.interp(t, positions, colours[:, ch])
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

def _wrap(text, font, tracking, max_width):
    """Greedy wrap honouring explicit newlines. Returns a list of lines."""
    lines = []
    for para in str(text).split("\n"):
        if not max_width:
            lines.append(para)
            continue
        words, current = para.split(" "), ""
        for word in words:
            trial = f"{current} {word}".strip()
            if text_width(trial, font, tracking) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def text_width(line, font, tracking):
    if not line:
        return 0.0
    if not tracking:
        return font.getlength(line)
    return sum(font.getlength(c) for c in line) + tracking * (len(line) - 1)


def draw_text_layer(spec, size):
    """Render a text layer onto a canvas-sized RGBA buffer.

    Geometry is explicit and measured, never guessed: `anchor` is two chars,
    horizontal l/m/r and vertical t/m/b, applied to the whole text block.
    """
    w, h = size
    buf = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(buf)

    text = str(spec.get("text", ""))
    if spec.get("uppercase"):
        text = text.upper()
    font = resolve_font(spec.get("font", "arial"), spec.get("size", 48))
    colour = parse_color(spec.get("color", "#000000"))
    tracking = float(spec.get("tracking", 0))
    align = spec.get("align", "left")
    anchor = str(spec.get("anchor", "lt")).lower()

    box = spec.get("box")
    if box:
        bx, by, bw, bh = [float(v) for v in box]
        max_width = spec.get("max_width", bw)
    else:
        bx, by, bw, bh = float(spec.get("x", 0)), float(spec.get("y", 0)), None, None
        max_width = spec.get("max_width")

    lines = _wrap(text, font, tracking, max_width)
    ascent, descent = font.getmetrics()
    natural = ascent + descent
    line_h = float(spec.get("line_height", 1.2)) * font.size
    block_w = max((text_width(l, font, tracking) for l in lines), default=0.0)
    block_h = line_h * (len(lines) - 1) + natural

    if box:
        ha = {"left": "l", "center": "m", "centre": "m", "right": "r"}.get(align, "l")
        va = str(spec.get("valign", "t")).lower()[0]
        origin_x = {"l": bx, "m": bx + bw / 2, "r": bx + bw}[ha]
        origin_y = {"t": by, "m": by + bh / 2 - block_h / 2, "b": by + bh - block_h}[va]
        anchor_h = ha
    else:
        anchor_h = anchor[0] if anchor and anchor[0] in "lmr" else "l"
        anchor_v = anchor[1] if len(anchor) > 1 and anchor[1] in "tmb" else "t"
        origin_x = {"l": bx, "m": bx, "r": bx}[anchor_h]
        origin_y = {"t": by, "m": by - block_h / 2, "b": by - block_h}[anchor_v]

    for i, line in enumerate(lines):
        lw = text_width(line, font, tracking)
        if box:
            lx = {"l": origin_x, "m": origin_x - lw / 2, "r": origin_x - lw}[anchor_h]
        else:
            just = {"left": "l", "center": "m", "centre": "m", "right": "r"}.get(
                align, anchor_h)
            base = {"l": origin_x, "m": origin_x, "r": origin_x}[anchor_h]
            if anchor_h == "l":
                lx = {"l": base, "m": base + (block_w - lw) / 2, "r": base + block_w - lw}[just]
            elif anchor_h == "m":
                lx = base - lw / 2
            else:
                lx = base - lw
        ly = origin_y + i * line_h
        if tracking:
            cx = lx
            for ch in line:
                draw.text((cx, ly), ch, font=font, fill=colour, anchor="la")
                cx += font.getlength(ch) + tracking
        else:
            draw.text((lx, ly), line, font=font, fill=colour, anchor="la")

    return buf, {
        "lines": lines,
        "block_width": round(block_w, 2),
        "block_height": round(block_h, 2),
        "origin": [round(origin_x, 2), round(origin_y, 2)],
        "font_size": font.size,
    }


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------

def draw_shape_layer(spec, size):
    buf = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(buf)
    kind = spec["type"]
    fill = parse_color(spec["fill"]) if spec.get("fill") else None
    outline = parse_color(spec["stroke"]) if spec.get("stroke") else None
    width = int(spec.get("stroke_width", 0) or 0)

    if kind in ("rect", "rectangle", "ellipse"):
        x, y, w, h = [float(v) for v in spec["box"]]
        xy = [x, y, x + w, y + h]
        if kind == "ellipse":
            draw.ellipse(xy, fill=fill, outline=outline, width=width)
        else:
            draw.rounded_rectangle(xy, radius=float(spec.get("radius", 0)),
                                   fill=fill, outline=outline, width=width)
    elif kind == "line":
        pts = [tuple(p) for p in spec["points"]]
        draw.line(pts, fill=outline or fill or (0, 0, 0, 255),
                  width=max(1, width or int(spec.get("width", 1))), joint="curve")
    elif kind == "polygon":
        draw.polygon([tuple(p) for p in spec["points"]], fill=fill, outline=outline)
    else:
        raise SystemExit(f"compositor: unknown shape type {kind!r}")
    return buf


# ---------------------------------------------------------------------------
# layer effects
# ---------------------------------------------------------------------------

def _colorize(alpha_img, colour):
    out = Image.new("RGBA", alpha_img.size, tuple(colour[:3]) + (0,))
    scaled = np.asarray(alpha_img).astype(np.float32) * (colour[3] / 255.0)
    out.putalpha(Image.fromarray((scaled + 0.5).astype(np.uint8), "L"))
    return out


def _offset_alpha(alpha_img, dx, dy):
    if not dx and not dy:
        return alpha_img
    out = Image.new("L", alpha_img.size, 0)
    out.paste(alpha_img, (int(round(dx)), int(round(dy))))
    return out


def apply_effects(buf, effects):
    """Layer effects, Photoshop order: shadows/glows behind, layer, then
    strokes and overlays. Returns a new canvas-sized buffer."""
    if not effects:
        return buf
    alpha = buf.getchannel("A")
    beneath, above = [], []
    body = buf

    for fx in effects:
        kind = fx.get("type")
        if kind in ("shadow", "drop_shadow"):
            a = _offset_alpha(alpha, fx.get("dx", 0), fx.get("dy", 4))
            if fx.get("spread"):
                a = a.filter(ImageFilter.MaxFilter(_odd(fx["spread"])))
            a = a.filter(ImageFilter.GaussianBlur(float(fx.get("blur", 12))))
            beneath.append(_colorize(a, parse_color(fx.get("color", "#00000080"))))
        elif kind in ("glow", "outer_glow"):
            a = alpha
            if fx.get("spread"):
                a = a.filter(ImageFilter.MaxFilter(_odd(fx["spread"])))
            a = a.filter(ImageFilter.GaussianBlur(float(fx.get("blur", 16))))
            beneath.append(_colorize(a, parse_color(fx.get("color", "#ffffff99"))))
        elif kind == "stroke":
            width = max(1, int(fx.get("width", 2)))
            grown = alpha.filter(ImageFilter.MaxFilter(_odd(width)))
            ring = Image.fromarray(
                np.clip(np.asarray(grown).astype(np.int16)
                        - np.asarray(alpha).astype(np.int16), 0, 255).astype(np.uint8), "L")
            beneath.append(_colorize(grown if fx.get("solid") else ring,
                                     parse_color(fx.get("color", "#ffffff"))))
        elif kind == "inner_shadow":
            inv = Image.eval(alpha, lambda v: 255 - v)
            inv = _offset_alpha(inv, fx.get("dx", 0), fx.get("dy", 3))
            inv = inv.filter(ImageFilter.GaussianBlur(float(fx.get("blur", 8))))
            clipped = Image.fromarray(
                (np.asarray(inv).astype(np.float32) * np.asarray(alpha).astype(np.float32)
                 / 255.0 + 0.5).astype(np.uint8), "L")
            above.append(_colorize(clipped, parse_color(fx.get("color", "#00000099"))))
        elif kind == "color_overlay":
            col = parse_color(fx.get("color", "#000000"))
            op = float(fx.get("opacity", 1.0))
            fill = _colorize(alpha, (col[0], col[1], col[2], int(col[3] * op)))
            above.append(fill)
        else:
            raise SystemExit(f"compositor: unknown effect type {kind!r}")

    out = Image.new("RGBA", buf.size, (0, 0, 0, 0))
    for layer in beneath:
        out.alpha_composite(layer)
    out.alpha_composite(body)
    for layer in above:
        out.alpha_composite(layer)
    return out


def _odd(value):
    n = max(1, int(round(float(value))))
    return n if n % 2 == 1 else n + 1


# ---------------------------------------------------------------------------
# layer rendering
# ---------------------------------------------------------------------------

def render_layer(layer, size):
    """One layer -> a canvas-sized RGBA buffer, before blend and opacity."""
    kind = layer.get("type", "image")
    info = {}

    if kind == "group":
        buf = Image.new("RGBA", size, (0, 0, 0, 0))
        for child in layer.get("layers", []):
            if child.get("hidden"):
                continue
            child_buf, _ = render_layer(child, size)
            buf = composite(buf, child_buf, child.get("blend", "normal"),
                            float(child.get("opacity", 1.0)))
    elif kind == "image":
        img = load_image(layer["src"])
        img = fit_image(img, layer.get("width"), layer.get("height"),
                        layer.get("fit", "contain"))
        if layer.get("flip") in ("h", "horizontal"):
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        elif layer.get("flip") in ("v", "vertical"):
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if layer.get("rotate"):
            img = img.rotate(float(layer["rotate"]), resample=Image.BICUBIC,
                             expand=True)
        img = apply_adjust(img, layer.get("adjust"))
        buf = Image.new("RGBA", size, (0, 0, 0, 0))
        x, y = int(layer.get("x", 0)), int(layer.get("y", 0))
        if str(layer.get("anchor", "")).lower() in ("center", "centre", "mm"):
            x -= img.width // 2
            y -= img.height // 2
        place(buf, img, x, y)
        info = {"placed": [x, y], "source_size": list(img.size)}
    elif kind == "text":
        buf, info = draw_text_layer(layer, size)
    elif kind == "gradient":
        g = dict(layer)
        buf = render_gradient(g, size)
        if g.get("box"):
            x, y, w, h = [int(v) for v in g["box"]]
            patch = render_gradient(g, (max(1, w), max(1, h)))
            buf = place(Image.new("RGBA", size, (0, 0, 0, 0)), patch, x, y)
    elif kind == "fill":
        buf = Image.new("RGBA", size, parse_color(layer.get("color", "#ffffff")))
    elif kind in ("rect", "rectangle", "ellipse", "line", "polygon"):
        buf = draw_shape_layer(layer, size)
    else:
        raise SystemExit(f"compositor: unknown layer type {kind!r}")

    if layer.get("effects"):
        buf = apply_effects(buf, layer["effects"])
    if kind != "image" and layer.get("adjust"):
        buf = apply_adjust(buf, layer["adjust"])
    if layer.get("mask"):
        buf = apply_mask(buf, build_mask(layer["mask"], size))
    return buf, info


def render_project(project, verbose=False):
    canvas = project.get("canvas", {})
    size = (int(canvas.get("width", 1024)), int(canvas.get("height", 1024)))
    out = Image.new("RGBA", size, parse_color(canvas.get("background", "#00000000")))
    report = []
    for i, layer in enumerate(project.get("layers", [])):
        if layer.get("hidden"):
            report.append({"index": i, "name": layer.get("name"), "skipped": "hidden"})
            continue
        buf, info = render_layer(layer, size)
        out = composite(out, buf, layer.get("blend", "normal"),
                        float(layer.get("opacity", 1.0)))
        entry = {"index": i, "name": layer.get("name") or layer.get("type"),
                 "type": layer.get("type", "image")}
        entry.update(info)
        report.append(entry)
        if verbose:
            print(f"  layer {i} {entry['name']}: {entry.get('type')} {info}",
                  file=sys.stderr)
    if project.get("crop"):
        x, y, w, h = [int(v) for v in project["crop"]]
        out = out.crop((x, y, x + w, y + h))
    return out, report


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def save_image(img, path, dpi=None, quality=95, flatten="#ffffff"):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    params = {}
    if dpi:
        params["dpi"] = (int(dpi), int(dpi))
    if suffix in (".jpg", ".jpeg"):
        base = Image.new("RGB", img.size, parse_color(flatten)[:3])
        base.paste(img, (0, 0), img)
        base.save(path, quality=int(quality), subsampling=0, optimize=True, **params)
    elif suffix == ".webp":
        img.save(path, quality=int(quality), method=6, **params)
    else:
        img.save(path, **params)
    return path


def describe(path):
    p = Path(path)
    size_kb = p.stat().st_size / 1024
    with Image.open(p) as im:
        return f"{p}  {im.width}x{im.height} {im.mode} {size_kb:,.1f} KB"


# ---------------------------------------------------------------------------
# proof sheet (the verification gate)
# ---------------------------------------------------------------------------

def checkerboard(size, square=16):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    pattern = (((xx // square) + (yy // square)) % 2).astype(np.uint8)
    grey = np.where(pattern == 0, 235, 205).astype(np.uint8)
    return Image.fromarray(np.dstack([grey, grey, grey,
                                      np.full((h, w), 255, np.uint8)]), "RGBA")


def grid_overlay(img, step=100, color="#ff008099"):
    out = img.convert("RGBA").copy()
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = parse_color(color)
    font = resolve_font("arial", max(11, out.width // 90))
    for x in range(0, out.width, step):
        d.line([(x, 0), (x, out.height)], fill=col, width=1)
        d.text((x + 3, 3), str(x), font=font, fill=col, anchor="la")
    for y in range(0, out.height, step):
        d.line([(0, y), (out.width, y)], fill=col, width=1)
        if y:
            d.text((3, y + 3), str(y), font=font, fill=col, anchor="la")
    out.alpha_composite(layer)
    return out


def contact_sheet(paths, cell=900, columns=None, grid=0):
    images, labels = [], []
    for p in paths:
        img = load_image(p)
        labels.append(f"{Path(p).name}  {img.width}x{img.height}")
        if grid:
            img = grid_overlay(img, step=grid)
        scale = min(cell / img.width, cell / img.height, 1.0)
        images.append(img.resize((max(1, round(img.width * scale)),
                                  max(1, round(img.height * scale))), LANCZOS))
    columns = columns or min(len(images), 2)
    rows = math.ceil(len(images) / columns)
    pad, bar = 24, 34
    cw = max(i.width for i in images) + pad * 2
    ch = max(i.height for i in images) + pad * 2 + bar
    sheet = Image.new("RGBA", (cw * columns, ch * rows), (24, 24, 27, 255))
    draw = ImageDraw.Draw(sheet)
    font = resolve_font("arial", 19)
    for i, img in enumerate(images):
        cx, cy = (i % columns) * cw, (i // columns) * ch
        ox = cx + (cw - img.width) // 2
        oy = cy + bar + (ch - bar - img.height) // 2
        tile = checkerboard(img.size)
        tile.alpha_composite(img)
        sheet.paste(tile, (ox, oy))
        draw.rectangle([ox - 1, oy - 1, ox + img.width, oy + img.height],
                       outline=(90, 90, 96, 255), width=1)
        draw.text((cx + pad, cy + 9), labels[i], font=font, fill=(235, 235, 240, 255))
    return sheet


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_project(path):
    text = Path(path).expanduser().read_text(encoding="utf-8")
    return json.loads(text)


def out_and_report(img, args, report=None):
    path = save_image(img, args.out, dpi=getattr(args, "dpi", None),
                      quality=getattr(args, "quality", 95),
                      flatten=getattr(args, "flatten", "#ffffff"))
    print(describe(path))
    if report and getattr(args, "report", False):
        print(json.dumps(report, indent=2))
    return path


def cmd_render(args):
    project = load_project(args.project)
    if args.set:
        for assignment in args.set:
            key, _, value = assignment.partition("=")
            apply_override(project, key.strip(), value.strip())
    img, report = render_project(project, verbose=args.verbose)
    out_and_report(img, args, report)


def apply_override(project, dotted, value):
    """--set layers.2.text='MILO' style override, so one project file can be
    reused for a batch without editing it."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    node = project
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    last = parts[-1]
    if last.isdigit():
        node[int(last)] = parsed
    else:
        node[last] = parsed


def cmd_new(args):
    w, h = [int(v) for v in args.size.lower().split("x")]
    project = {
        "canvas": {"width": w, "height": h, "background": args.background,
                   "dpi": args.dpi or 300},
        "layers": [],
    }
    if args.image:
        project["layers"].append({
            "name": "base", "type": "image", "src": str(Path(args.image).resolve()),
            "x": 0, "y": 0, "width": w, "height": h, "fit": "cover"})
    path = Path(args.out).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project, indent=2), encoding="utf-8")
    print(f"{path}  project {w}x{h}")


def cmd_text(args):
    base = load_image(args.image)
    layer = {
        "type": "text", "text": args.text, "font": args.font, "size": args.size,
        "color": args.color, "x": args.x, "y": args.y, "anchor": args.anchor,
        "align": args.align, "tracking": args.tracking,
        "line_height": args.line_height, "uppercase": args.uppercase,
    }
    if args.max_width:
        layer["max_width"] = args.max_width
    if args.box:
        layer["box"] = [float(v) for v in args.box.split(",")]
        layer["valign"] = args.valign
    effects = []
    if args.shadow:
        effects.append({"type": "shadow", "dx": 0, "dy": max(2, args.size // 14),
                        "blur": max(4, args.size // 5), "color": args.shadow_color})
    if args.stroke_width:
        effects.append({"type": "stroke", "width": args.stroke_width,
                        "color": args.stroke_color})
    if effects:
        layer["effects"] = effects
    project = {"canvas": {"width": base.width, "height": base.height,
                          "background": "#00000000"},
               "layers": [{"type": "image", "src": str(Path(args.image).resolve()),
                           "x": 0, "y": 0}, layer]}
    img, report = render_project(project, verbose=args.verbose)
    out_and_report(img, args, report)
    print(json.dumps(report[-1], indent=2))


def cmd_composite(args):
    base = load_image(args.base)
    layer = {"type": "image", "src": str(Path(args.overlay).resolve()),
             "x": args.x, "y": args.y, "opacity": args.opacity,
             "blend": args.blend, "fit": args.fit}
    if args.width:
        layer["width"] = args.width
    if args.height:
        layer["height"] = args.height
    if args.rotate:
        layer["rotate"] = args.rotate
    if args.anchor:
        layer["anchor"] = args.anchor
    if args.mask:
        layer["mask"] = {"src": str(Path(args.mask).resolve()),
                         "feather": args.feather, "invert": args.invert_mask}
    if args.shadow:
        layer["effects"] = [{"type": "shadow", "dx": 0, "dy": 10, "blur": 28,
                             "color": "#00000066"}]
    project = {"canvas": {"width": base.width, "height": base.height,
                          "background": "#00000000"},
               "layers": [{"type": "image", "src": str(Path(args.base).resolve()),
                           "x": 0, "y": 0}, layer]}
    img, report = render_project(project, verbose=args.verbose)
    out_and_report(img, args, report)


def cmd_resize(args):
    img = load_image(args.image)
    w = args.width or 0
    h = args.height or 0
    img = fit_image(img, w, h, args.fit)
    out_and_report(img, args)


def cmd_crop(args):
    img = load_image(args.image)
    x, y, w, h = [int(v) for v in args.box.split(",")]
    out_and_report(img.crop((x, y, x + w, y + h)), args)


def cmd_canvas(args):
    img = load_image(args.image)
    w, h = [int(v) for v in args.size.lower().split("x")]
    out = Image.new("RGBA", (w, h), parse_color(args.background))
    pos = {"center": ((w - img.width) // 2, (h - img.height) // 2),
           "topleft": (0, 0)}[args.anchor]
    place(out, img, *pos)
    out_and_report(out, args)


def cmd_adjust(args):
    img = load_image(args.image)
    adj = json.loads(args.adjust) if args.adjust else {}
    for key in ("brightness", "contrast", "saturation", "exposure", "hue",
                "temperature", "blur", "sharpen", "grain"):
        val = getattr(args, key, None)
        if val is not None:
            adj[key] = val
    if args.grayscale:
        adj["grayscale"] = True
    out_and_report(apply_adjust(img, adj), args)


def cmd_proof(args):
    sheet = contact_sheet(args.images, cell=args.cell, columns=args.columns,
                          grid=args.grid)
    path = save_image(sheet, args.out)
    print(describe(path))
    print("Open each source at FULL SIZE before grading. Tiles hide defects:")
    for p in args.images:
        print(f"  {Path(p).resolve()}")


def cmd_inspect(args):
    for target in args.images:
        p = Path(target).expanduser()
        if p.suffix.lower() == ".json":
            project = load_project(p)
            canvas = project.get("canvas", {})
            print(f"{p}  project {canvas.get('width')}x{canvas.get('height')} "
                  f"dpi={canvas.get('dpi')} layers={len(project.get('layers', []))}")
            for i, layer in enumerate(project.get("layers", [])):
                print(f"  [{i}] {layer.get('name') or ''} type={layer.get('type','image')} "
                      f"blend={layer.get('blend','normal')} "
                      f"opacity={layer.get('opacity',1)}"
                      f"{' HIDDEN' if layer.get('hidden') else ''}")
            continue
        with Image.open(p) as im:
            info = im.info
            has_alpha = im.mode in ("RGBA", "LA") or "transparency" in info
            print(f"{p}")
            print(f"  {im.width}x{im.height}  mode={im.mode}  alpha={has_alpha}  "
                  f"dpi={info.get('dpi')}  {p.stat().st_size / 1024:,.1f} KB")
            if args.alpha_stats and has_alpha:
                a = np.asarray(im.convert("RGBA").getchannel("A"))
                print(f"  alpha: min={a.min()} max={a.max()} "
                      f"opaque%={100 * (a == 255).mean():.1f}")


def cmd_fonts(args):
    idx = font_index()
    query = (args.query or "").lower()
    hits = sorted(f for f in idx if query in f)
    print(f"{len(hits)} font files matching {query!r} of {len(idx)} installed")
    for f in hits[:args.limit]:
        print(f"  {Path(f).stem:<32} {idx[f]}")
    if query:
        aliases = sorted(a for a in FONT_ALIASES if query in a)
        if aliases:
            print("aliases: " + ", ".join(aliases))


def add_output_args(p, need_out=True):
    p.add_argument("-o", "--out", required=need_out, help="output image path")
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--quality", type=int, default=95, help="jpg/webp quality")
    p.add_argument("--flatten", default="#ffffff",
                   help="background colour when exporting jpg")
    p.add_argument("--report", action="store_true", help="print the layer report")
    p.add_argument("-v", "--verbose", action="store_true")


def build_parser():
    p = argparse.ArgumentParser(prog="compositor", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="render a JSON project")
    r.add_argument("project")
    r.add_argument("--set", action="append", metavar="path.key=value",
                   help="override a project value, e.g. --set layers.1.text='MILO'")
    add_output_args(r)
    r.set_defaults(func=cmd_render)

    n = sub.add_parser("new", help="scaffold a project JSON")
    n.add_argument("-o", "--out", required=True)
    n.add_argument("--size", required=True, metavar="WxH")
    n.add_argument("--background", default="#ffffff")
    n.add_argument("--image", help="optional base image layer")
    n.add_argument("--dpi", type=int, default=300)
    n.set_defaults(func=cmd_new)

    t = sub.add_parser("text", help="place text on an image")
    t.add_argument("image")
    t.add_argument("--text", required=True)
    t.add_argument("--font", default="georgia")
    t.add_argument("--size", type=int, default=64)
    t.add_argument("--color", default="#ffffff")
    t.add_argument("--x", type=float, default=0)
    t.add_argument("--y", type=float, default=0)
    t.add_argument("--anchor", default="lt", help="h=l|m|r  v=t|m|b, e.g. mm")
    t.add_argument("--align", default="left", choices=["left", "center", "right"])
    t.add_argument("--tracking", type=float, default=0, help="letter spacing px")
    t.add_argument("--line-height", type=float, default=1.2, dest="line_height")
    t.add_argument("--max-width", type=float, default=None, dest="max_width")
    t.add_argument("--box", help="x,y,w,h paragraph box")
    t.add_argument("--valign", default="t", choices=["t", "m", "b"])
    t.add_argument("--uppercase", action="store_true")
    t.add_argument("--shadow", action="store_true")
    t.add_argument("--shadow-color", default="#00000099", dest="shadow_color")
    t.add_argument("--stroke-width", type=int, default=0, dest="stroke_width")
    t.add_argument("--stroke-color", default="#000000", dest="stroke_color")
    add_output_args(t)
    t.set_defaults(func=cmd_text)

    c = sub.add_parser("composite", help="place one image on another")
    c.add_argument("base")
    c.add_argument("overlay")
    c.add_argument("--x", type=float, default=0)
    c.add_argument("--y", type=float, default=0)
    c.add_argument("--width", type=int, default=None)
    c.add_argument("--height", type=int, default=None)
    c.add_argument("--fit", default="contain",
                   choices=["contain", "cover", "stretch", "none"])
    c.add_argument("--opacity", type=float, default=1.0)
    c.add_argument("--blend", default="normal", choices=sorted(BLENDS))
    c.add_argument("--rotate", type=float, default=0)
    c.add_argument("--anchor", default=None, help="'center' to treat x,y as the centre")
    c.add_argument("--mask", default=None)
    c.add_argument("--feather", type=float, default=0)
    c.add_argument("--invert-mask", action="store_true", dest="invert_mask")
    c.add_argument("--shadow", action="store_true")
    add_output_args(c)
    c.set_defaults(func=cmd_composite)

    rs = sub.add_parser("resize", help="resize an image")
    rs.add_argument("image")
    rs.add_argument("--width", type=int, default=None)
    rs.add_argument("--height", type=int, default=None)
    rs.add_argument("--fit", default="contain",
                    choices=["contain", "cover", "stretch"])
    add_output_args(rs)
    rs.set_defaults(func=cmd_resize)

    cr = sub.add_parser("crop", help="crop to x,y,w,h")
    cr.add_argument("image")
    cr.add_argument("--box", required=True, metavar="x,y,w,h")
    add_output_args(cr)
    cr.set_defaults(func=cmd_crop)

    cv = sub.add_parser("canvas", help="change canvas size around an image")
    cv.add_argument("image")
    cv.add_argument("--size", required=True, metavar="WxH")
    cv.add_argument("--background", default="#00000000")
    cv.add_argument("--anchor", default="center", choices=["center", "topleft"])
    add_output_args(cv)
    cv.set_defaults(func=cmd_canvas)

    aj = sub.add_parser("adjust", help="tone and colour adjustments")
    aj.add_argument("image")
    aj.add_argument("--adjust", help="raw JSON adjustment object")
    for key in ("brightness", "contrast", "saturation", "exposure", "hue",
                "temperature", "blur", "sharpen", "grain"):
        aj.add_argument(f"--{key}", type=float, default=None)
    aj.add_argument("--grayscale", action="store_true")
    add_output_args(aj)
    aj.set_defaults(func=cmd_adjust)

    pr = sub.add_parser("proof", help="contact sheet for visual verification")
    pr.add_argument("images", nargs="+")
    pr.add_argument("-o", "--out", required=True)
    pr.add_argument("--cell", type=int, default=900)
    pr.add_argument("--columns", type=int, default=None)
    pr.add_argument("--grid", type=int, default=0,
                    help="overlay a coordinate grid every N px")
    pr.set_defaults(func=cmd_proof)

    ins = sub.add_parser("inspect", help="dimensions, mode, alpha, dpi, layers")
    ins.add_argument("images", nargs="+")
    ins.add_argument("--alpha-stats", action="store_true", dest="alpha_stats")
    ins.set_defaults(func=cmd_inspect)

    fo = sub.add_parser("fonts", help="list resolvable fonts")
    fo.add_argument("query", nargs="?", default="")
    fo.add_argument("--limit", type=int, default=60)
    fo.set_defaults(func=cmd_fonts)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
