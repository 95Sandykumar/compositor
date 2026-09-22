---
name: compositor
description: REQUIRED for all image EDITING, compositing and text placement — everything beyond generation. Use whenever text, a name, a nameplate, a caption, a headline, a price, a logo, a watermark or a badge goes onto an image; whenever two or more images are combined, layered, masked or framed; and for crop, resize, canvas resize, rotate, flip, blend modes, drop shadows, strokes, glows, gradients, borders, background colour, tone/colour adjustment, and export to PNG/JPG/WebP. Also use to inspect an image or to build a proof sheet before calling any visual work done. Do NOT use to create new imagery from a prompt — that is nano-banana. Generate first, then edit here.
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
| 1b. Generative fill | Gemini 2.5 Flash Image via `nano-banana` | Inpaint, content-aware fill, background removal, extending past an edge |
| 2. Edit | **this skill** | Text, layout, layering, masks, effects, crop, colour, export |

Generative fill comes back here for placement. The model invents pixels; this
skill decides where they sit.

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
