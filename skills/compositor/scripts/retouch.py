#!/usr/bin/env python3
"""Retouch: click-to-mask, removal fill and proven paste-back.

The three slow steps of a customer revision, made repeatable:

  console   build a click page: click the area to change, copy the points
  mask      points -> mask PNG with SAM (Segment Anything), plus an overlay
  heal      remove what is under a mask (shirt text, a tag, a speck) with LaMa
  pasteback put an edited image back through a mask; outside pixels stay
            byte-identical to the approved file, and the command proves it

Models run on CPU with onnxruntime. No torch needed. They download once into
~/.cache/compositor/models (override with COMPOSITOR_MODELS):
  SAM ViT-B   Xenova/sam-vit-base (Apache-2.0), about 375 MB
  LaMa        Carve/LaMa-ONNX     (Apache-2.0), about 208 MB

Run `retouch.py <subcommand> --help` for arguments.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None

MODELS = {
    "sam-encoder": ("sam-vit-base-encoder.onnx",
                    "https://huggingface.co/Xenova/sam-vit-base/resolve/main/onnx/vision_encoder.onnx"),
    "sam-decoder": ("sam-vit-base-decoder.onnx",
                    "https://huggingface.co/Xenova/sam-vit-base/resolve/main/onnx/prompt_encoder_mask_decoder.onnx"),
    "lama": ("lama_fp32.onnx",
             "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"),
}
SAM_SIZE = 1024
LAMA_SIZE = 512
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def model_dir() -> Path:
    d = Path(os.environ.get("COMPOSITOR_MODELS",
                            Path.home() / ".cache" / "compositor" / "models"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_path(key: str) -> Path:
    name, url = MODELS[key]
    path = model_dir() / name
    if not path.exists():
        print(f"retouch: downloading {name} (one time) ...", file=sys.stderr)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
    return path


_SESSIONS: dict = {}


def session(key: str):
    if key not in _SESSIONS:
        try:
            import onnxruntime as ort
        except ImportError:
            raise SystemExit("retouch: needs onnxruntime (pip install onnxruntime)")
        _SESSIONS[key] = ort.InferenceSession(str(model_path(key)),
                                              providers=["CPUExecutionProvider"])
    return _SESSIONS[key]


def load_rgb(path) -> Image.Image:
    img = Image.open(path)
    img.load()
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    return img.convert("RGB")


def load_mask(path, size) -> Image.Image:
    m = Image.open(path)
    if m.mode in ("RGBA", "LA"):
        alpha = m.getchannel("A")
        # a mask saved as white-on-transparent: use alpha; otherwise luminance
        m = alpha if np.asarray(alpha).min() < 255 else m.convert("L")
    else:
        m = m.convert("L")
    if m.size != size:
        raise SystemExit(f"retouch: mask is {m.size[0]}x{m.size[1]}, "
                         f"image is {size[0]}x{size[1]}. They must match.")
    return m


def grow(mask: Image.Image, px: int) -> Image.Image:
    if px == 0:
        return mask
    size = abs(px) * 2 + 1
    return mask.filter(ImageFilter.MaxFilter(size) if px > 0 else ImageFilter.MinFilter(size))


def feather(mask: Image.Image, px: float) -> Image.Image:
    return mask.filter(ImageFilter.GaussianBlur(px)) if px > 0 else mask


def bbox(mask: Image.Image, threshold=0):
    a = np.asarray(mask) > threshold
    if not a.any():
        return None
    ys, xs = np.where(a)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def overlay(img: Image.Image, mask: Image.Image, points=None) -> Image.Image:
    """Mask tinted magenta over the image, with an outline and the clicks."""
    base = img.convert("RGBA")
    tint = Image.new("RGBA", img.size, (230, 0, 160, 0))
    tint.putalpha(mask.point(lambda v: int(v * 0.45)))
    out = Image.alpha_composite(base, tint)
    hard = mask.point(lambda v: 255 if v > 127 else 0)
    edge = Image.fromarray(
        (np.asarray(hard.filter(ImageFilter.MaxFilter(5))).astype(np.int16)
         - np.asarray(hard).astype(np.int16)).clip(0, 255).astype(np.uint8))
    line = Image.new("RGBA", img.size, (255, 255, 0, 255))
    line.putalpha(edge)
    out = Image.alpha_composite(out, line)
    if points:
        from PIL import ImageDraw
        d = ImageDraw.Draw(out)
        r = max(6, min(img.size) // 120)
        for x, y, lab in points:
            col = (40, 220, 90, 255) if lab == 1 else (240, 50, 50, 255)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(0, 0, 0, 255), width=2)
    return out.convert("RGB")


def parse_points(text: str):
    """'x,y,1;x,y,0' or a JSON list [[x,y,1],...] or a console JSON blob."""
    text = text.strip()
    if text.startswith("{"):
        text = json.dumps(json.loads(text)["points"])
    if text.startswith("["):
        pts = json.loads(text)
    else:
        pts = [p.split(",") for p in text.replace(" ", "").split(";") if p]
    out = []
    for p in pts:
        x, y = float(p[0]), float(p[1])
        lab = int(p[2]) if len(p) > 2 else 1
        out.append((x, y, 1 if lab else 0))
    if not out:
        raise SystemExit("retouch: no points given")
    return out


# ---------------------------------------------------------------------------
# SAM
# ---------------------------------------------------------------------------

def sam_embed(img: Image.Image, src_path: Path):
    """Image embeddings, cached by file content so repeat clicks are instant."""
    digest = hashlib.sha1(src_path.read_bytes()).hexdigest()[:16]
    cache = model_dir().parent / "embeddings" / f"{digest}.npz"
    if cache.exists():
        z = np.load(cache)
        return z["emb"], z["pos"], float(z["scale"])
    w, h = img.size
    scale = SAM_SIZE / max(w, h)
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
    arr = np.asarray(img.resize((nw, nh), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    pad = np.zeros((SAM_SIZE, SAM_SIZE, 3), dtype=np.float32)
    pad[:nh, :nw] = arr
    x = pad.transpose(2, 0, 1)[None]
    emb, pos = session("sam-encoder").run(None, {"pixel_values": x})
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, emb=emb, pos=pos, scale=scale)
    return emb, pos, scale


def sam_mask(img: Image.Image, src_path: Path, points, pick="best"):
    emb, pos, scale = sam_embed(img, src_path)
    pts = np.array([[[[x * scale, y * scale] for x, y, _ in points]]], dtype=np.float32)
    labs = np.array([[[lab for _, _, lab in points]]], dtype=np.int64)
    iou, low = session("sam-decoder").run(None, {
        "input_points": pts, "input_labels": labs,
        "image_embeddings": emb, "image_positional_embeddings": pos})
    scores = iou[0, 0]
    idx = int(np.argmax(scores)) if pick == "best" else int(pick)
    logits = low[0, 0, idx]
    w, h = img.size
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
    up = Image.fromarray(logits.astype(np.float32), "F").resize((SAM_SIZE, SAM_SIZE), Image.Resampling.BILINEAR)
    up = up.crop((0, 0, nw, nh)).resize((w, h), Image.Resampling.BILINEAR)
    m = (np.asarray(up) > 0).astype(np.uint8) * 255
    return Image.fromarray(m, "L"), [float(s) for s in scores], idx


# ---------------------------------------------------------------------------
# LaMa
# ---------------------------------------------------------------------------

def lama_fill(img: Image.Image, hole: Image.Image, pad: int):
    """Fill the hole with LaMa on a padded crop, at 512 px, then scale back."""
    box = bbox(hole)
    if box is None:
        raise SystemExit("retouch: the mask is empty, nothing to heal")
    x0, y0, x1, y1 = box
    side = max(x1 - x0, y1 - y0) + 2 * pad
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    W, H = img.size
    side = min(side, max(W, H))
    left = max(0, min(cx - side // 2, W - side)) if side <= W else 0
    top = max(0, min(cy - side // 2, H - side)) if side <= H else 0
    crop_box = (left, top, min(W, left + side), min(H, top + side))
    crop = img.crop(crop_box)
    cmask = hole.crop(crop_box)
    cw, ch = crop.size
    a = np.asarray(crop.resize((LAMA_SIZE, LAMA_SIZE), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    m = (np.asarray(cmask.resize((LAMA_SIZE, LAMA_SIZE), Image.Resampling.BILINEAR), dtype=np.float32) > 0).astype(np.float32)
    out, = session("lama").run(None, {"image": a.transpose(2, 0, 1)[None],
                                      "mask": m[None, None]})
    res = out[0].transpose(1, 2, 0)
    if res.max() <= 1.5:
        res = res * 255.0
    res = Image.fromarray(res.clip(0, 255).astype(np.uint8), "RGB").resize((cw, ch), Image.Resampling.BICUBIC)
    full = img.copy()
    full.paste(res, crop_box[:2])
    return full, crop_box


# ---------------------------------------------------------------------------
# paste-back with proof
# ---------------------------------------------------------------------------

def paste_through(base: Image.Image, edit: Image.Image, mask: Image.Image):
    """Composite edit over base through mask. Pixels where mask == 0 are
    copied from base exactly (no float round trip)."""
    b = np.asarray(base, dtype=np.uint8)
    e = np.asarray(edit, dtype=np.uint8)
    m = np.asarray(mask, dtype=np.float32)[..., None] / 255.0
    mix = (e.astype(np.float32) * m + b.astype(np.float32) * (1 - m) + 0.5).astype(np.uint8)
    out = np.where(m > 0, mix, b)
    return Image.fromarray(out, "RGB")


def proof_outside(base: Image.Image, out: Image.Image, mask: Image.Image) -> dict:
    b = np.asarray(base, dtype=np.int16)
    o = np.asarray(out, dtype=np.int16)
    outside = np.asarray(mask) == 0
    diff = np.abs(b - o).max(axis=2)
    changed_out = int((diff[outside] > 0).sum())
    inside = ~outside
    return {"outside_pixels": int(outside.sum()),
            "outside_changed": changed_out,
            "inside_pixels": int(inside.sum()),
            "inside_changed": int((diff[inside] > 0).sum()),
            "outside_identical": changed_out == 0}


def save(img: Image.Image, path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(p, quality=95)
    else:
        img.save(p)


def save_mask(mask: Image.Image, path: str):
    """Write a mask as PNG only: JPEG would smear the zero pixels."""
    p = Path(path)
    if p.suffix.lower() != ".png":
        raise SystemExit("retouch: masks are saved as .png only")
    p.parent.mkdir(parents=True, exist_ok=True)
    mask.save(p)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_mask(args):
    img = load_rgb(args.image)
    pts = parse_points(Path(args.points_file).read_text() if args.points_file else args.points)
    mask, scores, idx = sam_mask(img, Path(args.image), pts, args.pick)
    if args.add:
        mask = Image.fromarray(np.maximum(np.asarray(mask), np.asarray(load_mask(args.add, img.size))))
    mask = grow(mask, args.grow)
    hard = mask
    mask = feather(mask, args.feather)
    save_mask(mask, args.out)
    ov = args.overlay or str(Path(args.out).with_name(Path(args.out).stem + "-overlay.jpg"))
    save(overlay(img, mask, pts), ov)
    b = bbox(hard)
    print(json.dumps({"mask": str(Path(args.out).resolve()), "overlay": str(Path(ov).resolve()),
                      "size": list(img.size), "points": len(pts),
                      "scores": [round(s, 3) for s in scores], "picked": idx,
                      "coverage_pct": round(100 * (np.asarray(hard) > 127).mean(), 2),
                      "bbox": b}, indent=2))


def cmd_heal(args):
    img = load_rgb(args.image)
    hole = load_mask(args.mask, img.size).point(lambda v: 255 if v > 127 else 0)
    hole = grow(hole, args.grow)
    filled, crop_box = lama_fill(img, hole, args.pad)
    blend = feather(hole, args.feather)
    out = paste_through(img, filled, blend)
    save(out, args.out)
    rep = proof_outside(img, out, blend)
    rep.update({"out": str(Path(args.out).resolve()), "crop_box": list(crop_box),
                "model_input": f"{LAMA_SIZE}x{LAMA_SIZE}"})
    side = max(crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
    if side > LAMA_SIZE * 2:
        rep["warning"] = (f"crop is {side}px, filled at {LAMA_SIZE}px: the fill may look soft. "
                          "Use a tighter mask, or heal in smaller pieces.")
    print(json.dumps(rep, indent=2))
    if not rep["outside_identical"]:
        raise SystemExit("retouch: FAILED proof, pixels outside the mask changed")


def cmd_pasteback(args):
    base = load_rgb(args.base)
    edit = load_rgb(args.edit)
    if edit.size != base.size:
        if not args.resize:
            raise SystemExit(f"retouch: edit is {edit.size}, base is {base.size}. "
                             "Pass --resize to scale the edit to the base.")
        edit = edit.resize(base.size, Image.Resampling.LANCZOS)
    mask = load_mask(args.mask, base.size)
    mask = feather(grow(mask, args.grow), args.feather)
    out = paste_through(base, edit, mask)
    save(out, args.out)
    rep = proof_outside(base, out, mask)
    rep["out"] = str(Path(args.out).resolve())
    print(json.dumps(rep, indent=2))
    if not rep["outside_identical"]:
        raise SystemExit("retouch: FAILED proof, pixels outside the mask changed")


CONSOLE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mask clicks: __NAME__</title>
<style>
:root{--bg:#f6f5f2;--fg:#1c1b22;--muted:#686474;--line:#dcd8e2;--accent:#5b3fa8}
@media (prefers-color-scheme:dark){:root{--bg:#16141b;--fg:#ece9f2;--muted:#a19cae;--line:#34303f;--accent:#b49ff0;color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:12px}
h1{font-size:20px;margin:0}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:8px;padding:8px 14px;cursor:pointer}
button[aria-pressed=true]{border-color:var(--accent);outline:2px solid var(--accent)}
button.primary{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:600}
.stage{position:relative;display:inline-block;max-width:100%;cursor:crosshair;touch-action:manipulation}
.stage img{display:block;max-width:100%;height:auto;max-height:80vh}
.dot{position:absolute;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;border:2px solid #000;pointer-events:none}
.dot.add{background:#28dc5a}.dot.cut{background:#f03232}
pre{background:var(--line);padding:10px;border-radius:8px;white-space:pre-wrap;word-break:break-all;margin:0;font-size:12px}
.muted{color:var(--muted);font-size:13px}
</style></head><body><div class="wrap">
<h1>Click the area to change</h1>
<p class="muted">Green = part of the area. Red = not part of it. One click on the thing is often enough. Add a red click if the mask spills onto something else. Right-click also adds red.</p>
<div class="bar">
<button id="mAdd" aria-pressed="true">Green: include</button>
<button id="mCut" aria-pressed="false">Red: exclude</button>
<button id="undo">Undo</button><button id="clear">Clear</button>
<button class="primary" id="copy">Copy points</button><span class="muted" id="st"></span>
</div>
<div class="stage" id="stage"><img id="im" src="data:image/jpeg;base64,__B64__" alt="__NAME__"></div>
<pre id="out"></pre>
</div><script>
var W=__W__,H=__H__,mode=1,pts=[],im=document.getElementById('im'),stage=document.getElementById('stage'),out=document.getElementById('out');
function setMode(m){mode=m;document.getElementById('mAdd').setAttribute('aria-pressed',m==1);document.getElementById('mCut').setAttribute('aria-pressed',m==0)}
document.getElementById('mAdd').onclick=function(){setMode(1)};document.getElementById('mCut').onclick=function(){setMode(0)};
function draw(){stage.querySelectorAll('.dot').forEach(function(d){d.remove()});
pts.forEach(function(p){var d=document.createElement('div');d.className='dot '+(p[2]?'add':'cut');d.style.left=(p[0]/W*100)+'%';d.style.top=(p[1]/H*100)+'%';stage.appendChild(d)});
out.textContent=JSON.stringify({image:"__NAME__",size:[W,H],points:pts})}
function add(e,lab){var r=im.getBoundingClientRect();var x=Math.round((e.clientX-r.left)/r.width*W),y=Math.round((e.clientY-r.top)/r.height*H);if(x<0||y<0||x>W||y>H)return;pts.push([x,y,lab]);draw()}
stage.addEventListener('click',function(e){add(e,mode)});
stage.addEventListener('contextmenu',function(e){e.preventDefault();add(e,0)});
document.getElementById('undo').onclick=function(){pts.pop();draw()};document.getElementById('clear').onclick=function(){pts=[];draw()};
document.getElementById('copy').onclick=function(){var s=document.getElementById('st');navigator.clipboard.writeText(out.textContent).then(function(){s.textContent='Copied. Paste it in chat.'},function(){var r=document.createRange();r.selectNodeContents(out);var g=getSelection();g.removeAllRanges();g.addRange(r);s.textContent='Selected. Press Ctrl+C.'})};
draw();
</script></body></html>"""


def cmd_console(args):
    img = load_rgb(args.image)
    W, H = img.size
    view = img.copy()
    view.thumbnail((args.max_side, args.max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    view.save(buf, "JPEG", quality=88)
    name = Path(args.image).name
    html = (CONSOLE.replace("__B64__", base64.b64encode(buf.getvalue()).decode())
            .replace("__NAME__", name.replace('"', "")).replace("__W__", str(W)).replace("__H__", str(H)))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(html, encoding="utf-8")
    print(json.dumps({"console": str(Path(args.out).resolve()), "image_size": [W, H],
                      "next": f'retouch.py mask "{args.image}" --points \'<pasted JSON>\' -o mask.png'}, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="retouch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("console", help="HTML page to click the area to change")
    c.add_argument("image")
    c.add_argument("-o", "--out", required=True, help="output .html")
    c.add_argument("--max-side", type=int, default=1600, dest="max_side")
    c.set_defaults(func=cmd_console)

    m = sub.add_parser("mask", help="clicks -> mask PNG (SAM)")
    m.add_argument("image")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--points", help="'x,y,1;x,y,0', a JSON list, or the console JSON")
    g.add_argument("--points-file", dest="points_file")
    m.add_argument("-o", "--out", required=True, help="mask .png")
    m.add_argument("--overlay", help="overlay .jpg (default: <out>-overlay.jpg)")
    m.add_argument("--grow", type=int, default=0, help="px to grow (+) or shrink (-)")
    m.add_argument("--feather", type=float, default=0, help="edge blur px")
    m.add_argument("--pick", default="best", choices=["best", "0", "1", "2"],
                   help="SAM gives 3 sizes of mask; 0 small, 2 large")
    m.add_argument("--add", help="union with an existing mask .png")
    m.set_defaults(func=cmd_mask)

    h = sub.add_parser("heal", help="remove what is under the mask (LaMa)")
    h.add_argument("image")
    h.add_argument("--mask", required=True)
    h.add_argument("-o", "--out", required=True)
    h.add_argument("--grow", type=int, default=6, help="grow the hole px (catch edges)")
    h.add_argument("--feather", type=float, default=2)
    h.add_argument("--pad", type=int, default=96, help="context px around the hole")
    h.set_defaults(func=cmd_heal)

    pb = sub.add_parser("pasteback", help="edit -> base through a mask, with proof")
    pb.add_argument("base", help="the file the customer approved")
    pb.add_argument("edit", help="the model's edited version")
    pb.add_argument("--mask", required=True)
    pb.add_argument("-o", "--out", required=True)
    pb.add_argument("--grow", type=int, default=0)
    pb.add_argument("--feather", type=float, default=3)
    pb.add_argument("--resize", action="store_true", help="scale edit to base size")
    pb.set_defaults(func=cmd_pasteback)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
