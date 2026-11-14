# Compositor

A headless layered image editor for coding agents. It is the **editing** half of
an image workflow: text placement, compositing, masks, layer effects, blend
modes, colour adjustment, crop, resize and export, driven by a JSON project file
that renders the same pixels every time.

It does not generate imagery. That is the point.

## The problem it solves

Ask an image model to put a name on a portrait and three things go wrong:

1. **It misspells the text.** Diffusion models approximate glyphs.
2. **A reference image's own text overrides your prompt.** In the project this
   was built for, every generated nameplate came out reading `MILO` regardless
   of the pet's actual name, because one reference image had `MILO` in it.
3. **You cannot nudge it.** Moving a caption 40px means re-generating, which
   costs a credit, changes the whole image, and is not reproducible.

So split the job. Generate the artwork with clean empty space where the text
belongs. Then place the text here, where the glyphs are exact, the position is a
number you chose, and a revision is a one-line edit instead of a new render.

## Install

**As a Claude Code plugin:**

```
/plugin marketplace add 95Sandykumar/compositor
/plugin install compositor
```

**Manually**, into any agent that reads `SKILL.md` files:

```bash
git clone https://github.com/95Sandykumar/compositor
cp -r compositor/skills/compositor ~/.claude/skills/
```

**Requirements:** Python 3.10+, Pillow and numpy.

```bash
pip install pillow numpy
```

Works on Windows, macOS and Linux. No system packages, no ImageMagick, no GUI.

Optional, for `retouch.py` (click-to-mask, heal, paste-back): `pip install onnxruntime`.
The two models (SAM ViT-B from `Xenova/sam-vit-base`, LaMa from `Carve/LaMa-ONNX`,
both Apache-2.0) download once into `~/.cache/compositor/models`, about 580 MB.
No torch, CPU only.

## Quickstart

```bash
SCRIPT=~/.claude/skills/compositor/scripts/compositor.py

# exact dimensions first, never assume them
python "$SCRIPT" inspect portrait.jpg

# a name, centred on a point, tracked out like an engraving
python "$SCRIPT" text portrait.jpg \
  --text "AUGUSTA" --font georgia-bold --size 88 --color "#f5f0e6" \
  --x 600 --y 1240 --anchor mm --tracking 14 --shadow \
  -o plate.png

# prove it, with a pixel grid so you can check the coordinate landed
python "$SCRIPT" proof plate.png -o proof.png --grid 200
```

## The project file

Anything with more than one layer belongs in a project. The JSON is the source;
the PNG is a build artifact.

```json
{
  "canvas": { "width": 1200, "height": 1500, "background": "#0b1d3a", "dpi": 300 },
  "layers": [
    { "name": "backdrop", "type": "gradient", "angle": 90,
      "stops": [[0.0, "#1a2c4e"], [1.0, "#060d1c"]] },

    { "name": "portrait", "type": "image", "src": "art.jpg",
      "x": 100, "y": 110, "width": 1000, "height": 1000, "fit": "cover",
      "adjust": { "contrast": 1.06, "saturation": 1.04, "grain": 3 },
      "mask": { "shape": { "type": "rect", "box": [100, 110, 1000, 1000], "radius": 18 },
                "feather": 1.5 },
      "effects": [
        { "type": "shadow", "dx": 0, "dy": 16, "blur": 40, "color": "#00000099" },
        { "type": "stroke", "width": 5, "color": "#c8a04d" }
      ] },

    { "name": "nameplate", "type": "text", "text": "AUGUSTA",
      "font": "georgia-bold", "size": 88, "color": "#f5f0e6",
      "x": 600, "y": 1240, "anchor": "mt", "tracking": 14 }
  ]
}
```

```bash
python "$SCRIPT" render plate.json -o out.png
```

Render one design across a batch without touching the file:

```bash
python "$SCRIPT" render plate.json --set layers.2.text='"JELLYBEAN"' -o jellybean.png
```

Full schema in
[skills/compositor/references/project-format.md](skills/compositor/references/project-format.md).

## Commands

| Command | What it does |
|---------|--------------|
| `render` | Render a JSON project, with `--set` overrides |
| `new` | Scaffold a project file |
| `text` | Place text on a single image |
| `composite` | Place one image on another, with mask, blend and opacity |
| `resize` / `crop` / `canvas` | Geometry |
| `adjust` | Levels, exposure, hue, temperature, saturation, contrast, grain |
| `inspect` | Dimensions, mode, alpha stats, dpi, or a project's layer list |
| `fonts` | List every resolvable font on the machine |
| `proof` | Contact sheet with an optional pixel-coordinate grid |

`retouch.py`, for revisions to an approved image:

| Command | What it does |
|---------|--------------|
| `console` | Writes an HTML page: click the area to change, copy the points |
| `mask` | Clicks to a mask PNG with Segment Anything, plus an overlay to check it |
| `heal` | Removes what is under a mask (text, a tag, a speck) with LaMa |
| `pasteback` | Puts a model's edit back through a mask and fails unless every pixel outside it is unchanged |

## What it supports

| Area | Detail |
|------|--------|
| Layer types | image, text, gradient (linear + radial), fill, rect, ellipse, line, polygon, group |
| Groups | Children flatten first, so group opacity dims the whole thing as one object |
| Text | Two-axis anchor, letter tracking, paragraph boxes with valign, wrapping, real font metrics, measured block size returned in the render report |
| Masks | From an image, a shape, or a gradient; feather, invert, luminance or alpha channel |
| Effects | Drop shadow, outer glow, stroke, inner shadow, colour overlay |
| Blend | 15 modes on W3C separable-blend float compositing, correct over a partly transparent backdrop |
| Adjustments | Levels with gamma, exposure in stops, hue rotation about the luma axis, temperature, saturation, contrast, seeded grain, blur, unsharp mask |
| Export | PNG, JPG with explicit flatten colour, WebP, dpi metadata |

Determinism is the contract. Grain is seeded, blends are float, and the same
project renders identically on every run.

## What it does not do

| Not supported | Use instead |
|---------------|-------------|
| Generating imagery from a prompt | Any image model. Pairs cleanly with Gemini 2.5 Flash Image ("Nano Banana") |
| New content: a changed face, a new pose, extending past the edges | A generative model. Mask with `retouch.py mask`, edit, then `retouch.py pasteback` |
| Complex-script shaping and font pair kerning | Needs Pillow built with raqm; set `tracking` explicitly for Latin display type |
| PSD read or write | Not implemented |

## Verification

An exit code of 0 means a file was written, not that it looks right. Text can
land off-canvas, a mask can erase the subject, and a blend can go black, all
while exiting 0.

```bash
python "$SCRIPT" proof out.png -o proof.png --grid 200
```

Then open the **full-size** output, not just the proof tile. Contact-sheet
thumbnails hide soft edges, banding and clipped glyphs.

## Licence

MIT. See [LICENSE](LICENSE).
