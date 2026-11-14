---
name: compositor
description: Use for all image editing, compositing and text placement, everything beyond generation. Use whenever text, a name, a nameplate, a caption, a headline, a price, a logo, a watermark or a badge goes onto an image; whenever two or more images are combined, layered, masked or framed; and for crop, resize, canvas resize, rotate, flip, blend modes, drop shadows, strokes, glows, gradients, borders, background colour, tone/colour adjustment, and export to PNG/JPG/WebP. Also use for customer revision edits: making a mask by clicking the area (Segment Anything), removing text, tags or specks from an approved image (LaMa heal), and pasting a model's edit back through a mask with proof that nothing else changed. Also use to inspect an image or to build a proof sheet before calling any visual work done. New imagery from a prompt is nano-banana's job. Generate first, then edit here.
allowed-tools: Bash(python:*), Read, Write, Edit, Glob, Grep
---

# Compositor

A headless layered image editor. Deterministic, scriptable, repeatable: the
same project JSON always renders the same pixels.

`compositor.py` sits in `scripts/` next to this SKILL.md. Resolve it once, in
either layout (plugin install or manual install), then reuse `$SCRIPT`:

```bash
for C in "$CLAUDE_PLUGIN_ROOT/skills/compositor/scripts/compositor.py" \
         "$HOME/.claude/skills/compositor/scripts/compositor.py"; do
  [ -f "$C" ] && SCRIPT="$C" && break
done
python "$SCRIPT" --help
```

Requires Python 3.10+ with Pillow and numpy (`pip install pillow numpy`).

## The hard rule: generate, then edit. Never both at once.

| Step | Tool | What it owns |
|------|------|--------------|
| 1. Generate | `nano-banana` skill, or Nano Banana Pro via Higgsfield | Imagery only. Subject, style, lighting, composition |
| 1b. Masked model edit | The project's chosen image edit model that accepts a mask (check the project's own rules for which one) | New pixels the image does not have: a changed face, a new pose, an added subject, extending past an edge |
| 2. Edit | **this skill** (`compositor.py` + `retouch.py`) | Text, layout, layering, masks, removal, effects, crop, colour, export |

A model edit comes back here: `retouch.py pasteback` puts it through the mask
so every pixel outside is identical to the approved file.

## Pixels first (decide before any paid model call)

A customer revision goes to a model only when it needs pixels the image does
not have. Go down this table and stop at the first row that fits.

| The ask | Do this | Model? |
|---------|---------|--------|
| A name, a date, a line of text, a plate | `text` or a project text layer | No |
| Move, resize, flip, rotate, crop, fit to a print size | project layer numbers, `crop`, `canvas` | No |
| Brighter, darker, warmer, more colour, sharper, black and white | `adjust` on the whole image or through a mask | No |
| Background colour, halo, glow, vignette, gradient | click a background mask, then the recipes in [references/recipes.md](references/recipes.md) | No |
| Remove something small: shirt text, a tag, a speck, a leash bit | `retouch.py mask`, then `retouch.py heal` | No |
| Swap in a part that exists in another of the customer's pictures | `retouch.py mask` on both, `composite` with feather | No |
| Change a face, eyes, mouth, pose, body size; add a new subject or object | `retouch.py mask`, model edit, then `retouch.py pasteback` | Yes |

If a no-model row fails the full-size look, then move down to a model edit.
Say which row you used.

**Never ask an image model to render text.** It will misspell it, and a
reference image's own text overrides the prompt (a Crown & Canvas reference
image made every nameplate read "MILO" regardless of the pet's name). Generate
the artwork with clean empty space where the text goes, then place the text
here where the glyphs are exact and the position is a number you chose.

**Never re-prompt to fix a placement problem.** Re-generating to move a name
2cm costs a credit, changes the whole image, and is not reproducible. Move the
number in the project JSON and re-render.

If a request mixes both ("make a poster of a dog with the name BELLA"), split
it: generate the dog, then composite the name. Say that you split it.

## Workflow

1. **Inspect the inputs.** `inspect` gives exact dimensions. Never assume a
   size, and never compute a coordinate from an assumed one.
2. **Build a project JSON** for anything with more than one layer, or anything
   that will be repeated across a batch. One-off single changes can use a
   quick subcommand.
3. **Render.**
4. **Prove it.** `proof` the output, then Read the full-size file. See below.
5. Report the output path and its dimensions.

Keep the project JSON next to the deliverable. It is the editable source; the
PNG is a build artifact. A revision is an edit to the JSON, not a new render
from scratch.

## Verification gate (not optional)

An exit code of 0 means the file was written, not that it looks right. Text can
land off-canvas, a mask can erase the subject, a blend can go black, and all of
those exit 0.

```bash
python "$SCRIPT" proof out.png -o proof.png --grid 200   # --grid overlays px coordinates
```

Then **Read the full-size output**, not only the proof tile. Contact-sheet
tiles hide soft edges, banding and clipped glyphs. The `--grid` overlay is how
you check that a coordinate actually landed where you intended.

## Commands

| Need | Command |
|------|---------|
| Multi-layer composition | `render project.json -o out.png` |
| Scaffold a project | `new -o project.json --size 1200x1500 --image base.jpg` |
| Text onto one image | `text in.jpg --text "AUGUSTA" --x 600 --y 1240 --anchor mm -o out.png` |
| Two images combined | `composite base.png overlay.png --x 100 --y 80 -o out.png` |
| Resize / crop / canvas | `resize`, `crop --box x,y,w,h`, `canvas --size WxH` |
| Tone and colour | `adjust in.jpg --saturation 1.1 --contrast 1.05 -o out.png` |
| Dimensions, alpha, dpi | `inspect img.png --alpha-stats` |
| Which fonts exist | `fonts georgia` |
| Proof sheet | `proof a.png b.png -o proof.png --grid 200` |

## Retouch: click-to-mask, heal, paste-back

`retouch.py` sits beside `compositor.py`. It needs `onnxruntime`
(`pip install onnxruntime`), not torch. Models download once into
`~/.cache/compositor/models` (SAM ViT-B about 375 MB, LaMa about 208 MB, both
Apache-2.0). Resolve it the same way: `RETOUCH="$(dirname "$SCRIPT")/retouch.py"`.

| Step | Command | Notes |
|------|---------|-------|
| 1. Click page | `console approved.png -o clicks.html` | Open it, click the area (green), right-click what to leave out (red), Copy points. Send it to whoever must say where |
| 2. Mask | `mask approved.png --points '<pasted JSON>' -o mask.png --grow 4` | Also takes `"x,y,1;x,y,0"`. Writes `mask-overlay.jpg`. First run on an image takes about 8 s, later clicks under 1 s (cached) |
| 3a. Remove | `heal approved.png --mask mask.png -o healed.png` | LaMa fill at 512 px on a crop around the mask. A warning prints when the crop is so big the fill may look soft |
| 3b. Model edit | send `approved.png` + `mask.png` to the edit model | Prompt only what changes inside the mask |
| 4. Paste back | `pasteback approved.png model-out.png --mask mask.png -o final.png` | `--resize` if the model returned another size |

`heal` and `pasteback` print `outside_changed` and fail unless it is 0. That
number is the proof the customer's approved pixels are untouched; quote it.

Mask tips:
- One click on a thing usually gets the whole thing (an ear, a shirt, a dog).
  Click 2 or 3 points along thin or long things (a line of lettering).
- `--pick 0|1|2` chooses the small, middle or large mask SAM offers when
  `best` takes too much or too little.
- Masks for a new subject must cover the WHOLE subject plus `--grow`, or the
  paste-back cuts it (a chin or a neck edge goes missing).
- `--add old-mask.png` unions a new click with an earlier mask.
- Look at the overlay full size before using a mask. Exit 0 is not proof.

Batch one project over many values without editing the file:

```bash
python "$SCRIPT" render plate.json --set layers.3.text='"JELLYBEAN"' -o jellybean.png
```

## Text placement

`--anchor` is two characters: horizontal `l|m|r`, vertical `t|m|b`. `mm` centres
the block on the given point, which is what you want for a nameplate. The
render report prints the measured `block_width` and `block_height`, so you can
centre against a real number instead of guessing.

- `--tracking` is letter spacing in px. Display serif set wide (10-16px at
  80-90pt) is what makes a nameplate read as engraved rather than typed.
- `--box x,y,w,h` with `--align`/`--valign` for wrapped paragraphs.
- `--font` takes an alias (`georgia-bold`), a filename, or a path. Any inexact
  match is reported on stderr. **Read stderr.** A silently substituted face is
  the difference between premium and cheap.
- Pillow is built without raqm here, so there is no font-level kerning. Set
  tracking explicitly rather than relying on the font's pair kerning.

## Project format

Full schema, every layer type, every effect and every blend mode:
[references/project-format.md](references/project-format.md). Read it before
writing a project with masks, groups or blend modes.

Minimal shape:

```json
{
  "canvas": { "width": 1200, "height": 1500, "background": "#0b1d3a", "dpi": 300 },
  "layers": [
    { "name": "portrait", "type": "image", "src": "art.jpg",
      "x": 100, "y": 110, "width": 1000, "height": 1000, "fit": "cover" },
    { "name": "nameplate", "type": "text", "text": "AUGUSTA",
      "font": "georgia-bold", "size": 88, "color": "#f5f0e6",
      "x": 600, "y": 1240, "anchor": "mt", "tracking": 14 }
  ]
}
```

Layers composite bottom-up, first entry first. Every layer accepts `name`,
`hidden`, `opacity`, `blend`, `mask`, `effects` and `adjust`.

## Gotchas

- Paths on this machine contain spaces. Quote every path argument.
- `render` writes RGBA. Exporting `.jpg` flattens onto `--flatten` (default
  white); pass the real background or you will get a white halo.
- `--dpi` is metadata only. It does not resample. For print, set the pixel
  dimensions to `inches x dpi` yourself.
- `fit: "cover"` centre-crops to fill the box exactly; `contain` letterboxes.
  For a portrait inside a frame you almost always want `cover`.
- A drop shadow needs canvas room. A shadow with `blur: 40` on a layer flush to
  the edge is clipped, because effects render inside the canvas.
- Blend modes and opacity route through numpy float compositing. `normal` at
  full opacity takes a fast path and is bit-identical to `alpha_composite`.
