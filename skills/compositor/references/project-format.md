# Compositor project format

A project is one JSON object: a `canvas` and an ordered `layers` array. Layers
composite bottom-up, so `layers[0]` is the backmost.

```json
{
  "canvas": { "width": 1200, "height": 1500, "background": "#0b1d3a", "dpi": 300 },
  "crop": [0, 0, 1200, 1200],
  "layers": [ ... ]
}
```

| Canvas key | Default | Notes |
|------------|---------|-------|
| `width`, `height` | 1024 | Pixels |
| `background` | `#00000000` | Transparent by default |
| `dpi` | none | Metadata only, written on export. Does not resample |

`crop` is `[x, y, w, h]` applied after every layer is composited. Use it to
render a large working canvas and ship a tighter frame.

## Keys every layer accepts

| Key | Default | Notes |
|-----|---------|-------|
| `name` | type | Appears in the render report and `inspect` |
| `type` | `image` | See layer types below |
| `hidden` | false | Skipped, and reported as skipped |
| `opacity` | 1.0 | 0.0 - 1.0 |
| `blend` | `normal` | See blend modes |
| `mask` | none | See masks |
| `effects` | none | See effects |
| `adjust` | none | See adjustments |

## Layer types

### image

```json
{ "type": "image", "src": "art.jpg", "x": 100, "y": 110,
  "width": 1000, "height": 1000, "fit": "cover",
  "rotate": -3, "flip": "h", "anchor": "center" }
```

| Key | Notes |
|-----|-------|
| `src` | Path. Absolute is safer; relative resolves against the process cwd |
| `x`, `y` | Top-left in canvas px, unless `anchor` is `center` |
| `width`, `height` | Target box. Either may be omitted to keep the aspect |
| `fit` | `contain` (default, letterbox), `cover` (centre-crop to fill), `stretch`, `none` |
| `rotate` | Degrees counter-clockwise, expands the bounding box |
| `flip` | `h` or `v` |
| `anchor` | `center` makes `x,y` the centre instead of the top-left |

Layers may bleed off any edge; the overflow is clipped, not wrapped.

### text

```json
{ "type": "text", "text": "AUGUSTA", "font": "georgia-bold", "size": 88,
  "color": "#f5f0e6", "x": 600, "y": 1240, "anchor": "mt",
  "tracking": 14, "line_height": 1.2, "uppercase": true }
```

| Key | Default | Notes |
|-----|---------|-------|
| `text` | "" | `\n` forces a line break |
| `font` | `arial` | Alias, filename, or full path |
| `size` | 48 | Points at the font's own em size |
| `color` | `#000000` | |
| `x`, `y` | 0 | The anchor point |
| `anchor` | `lt` | horizontal `l\|m\|r` + vertical `t\|m\|b` |
| `align` | `left` | Alignment of lines within a multi-line block |
| `tracking` | 0 | Letter spacing in px, may be negative |
| `line_height` | 1.2 | Multiple of `size` |
| `max_width` | none | Wraps greedily at this width |
| `uppercase` | false | Applied before measuring |

Paragraph box form, which replaces `x`/`y`/`anchor`:

```json
{ "type": "text", "box": [200, 1352, 800, 200], "align": "center", "valign": "m" }
```

`valign` is `t`, `m` or `b`. Vertical metrics come from the font's real
ascent/descent, so a block's height is measured, never estimated.

The render report returns `block_width`, `block_height`, `lines` and the
resolved `origin` for each text layer. Use those numbers to align other
elements against the text instead of eyeballing it.

### gradient

```json
{ "type": "gradient", "angle": 90, "box": [0, 1380, 1200, 120],
  "stops": [[0.0, "#00000000"], [1.0, "#000000cc"]] }
```

| Key | Default | Notes |
|-----|---------|-------|
| `gradient_type` | `linear` | `linear` or `radial`. On a layer the `type` key is already `"gradient"`, so the shape goes here. In a *mask* gradient spec, plain `type` works too |
| `angle` | 90 | Degrees. 0 = left to right, 90 = top to bottom. Linear only |
| `stops` | black to white | `[[position 0-1, colour], ...]` |
| `from`, `to` | | Shorthand for two stops |
| `box` | whole canvas | `[x, y, w, h]` |
| `cx`, `cy`, `radius` | 0.5, 0.5, 0.5 | Radial only, as fractions of the box |

### fill

```json
{ "type": "fill", "color": "#f5f0e6" }
```

Fills the whole canvas. Combine with `mask` and `blend` for tints and washes.

### shapes

```json
{ "type": "rect", "box": [480, 1188, 240, 2], "radius": 0, "fill": "#c8a04d" }
{ "type": "ellipse", "box": [100, 100, 400, 400], "stroke": "#ffffff", "stroke_width": 6 }
{ "type": "line", "points": [[0, 0], [1200, 0]], "width": 3, "stroke": "#c8a04d" }
{ "type": "polygon", "points": [[0,0],[100,0],[50,80]], "fill": "#8b0000" }
```

`rect` and `ellipse` take `box` as `[x, y, w, h]`. `fill`, `stroke` and
`stroke_width` are optional; omit `fill` for an outline only.

### group

```json
{ "type": "group", "name": "plaque", "opacity": 0.9, "blend": "multiply",
  "layers": [ ... ] }
```

Children composite into their own buffer first, so the group's `opacity`,
`mask`, `blend` and `effects` apply to the flattened result. A group's opacity
dims everything inside it as one object, which is not the same as setting the
same opacity on each child.

## Masks

A mask is an `L` channel: white keeps, black hides. Set it on any layer.

```json
"mask": { "src": "matte.png", "channel": "luminance", "feather": 2, "invert": false }
"mask": { "shape": { "type": "rect", "box": [100, 110, 1000, 1000], "radius": 18 } }
"mask": { "shape": { "type": "ellipse", "box": [200, 200, 600, 600] }, "feather": 40 }
"mask": { "gradient": { "angle": 90, "stops": [[0.6, "#ffffff"], [1.0, "#000000"]] } }
```

| Key | Notes |
|-----|-------|
| `src` | Image path, resized to the canvas if needed |
| `channel` | `luminance` (default) or `alpha` |
| `shape` | `rect` (with `radius`), `ellipse`, or `polygon` with `points` |
| `gradient` | Same keys as a gradient layer. Good for fades |
| `feather` | Gaussian blur radius on the mask, in px |
| `invert` | Applied last |

A bare string is shorthand for `{"src": "..."}`.

## Effects

Applied in Photoshop order: shadows and glows behind the layer, then the layer,
then strokes and overlays.

```json
"effects": [
  { "type": "shadow", "dx": 0, "dy": 16, "blur": 40, "spread": 0, "color": "#00000099" },
  { "type": "glow", "blur": 24, "spread": 4, "color": "#c8a04d66" },
  { "type": "stroke", "width": 5, "color": "#c8a04d", "solid": false },
  { "type": "inner_shadow", "dx": 0, "dy": 3, "blur": 8, "color": "#00000099" },
  { "type": "color_overlay", "color": "#8b0000", "opacity": 0.35 }
]
```

| Effect | Keys |
|--------|------|
| `shadow` / `drop_shadow` | `dx`, `dy`, `blur`, `spread`, `color` |
| `glow` / `outer_glow` | `blur`, `spread`, `color` |
| `stroke` | `width`, `color`, `solid` (true fills the whole silhouette, not just the ring) |
| `inner_shadow` | `dx`, `dy`, `blur`, `color` |
| `color_overlay` | `color`, `opacity` |

Effects render inside the canvas. A layer flush against an edge will have its
shadow clipped; give the canvas room or inset the layer.

## Adjustments

Set `adjust` on any layer, or run `adjust` as a standalone subcommand.

```json
"adjust": {
  "brightness": 1.05, "contrast": 1.1, "saturation": 1.15,
  "exposure": 0.2, "hue": 12, "temperature": 15,
  "levels": { "in_black": 8, "in_white": 247, "gamma": 1.05,
              "out_black": 0, "out_white": 255 },
  "grain": 4, "grain_seed": 7,
  "blur": 0, "sharpen": 0.6,
  "grayscale": false, "invert": false
}
```

| Key | Neutral | Notes |
|-----|---------|-------|
| `brightness`, `contrast`, `saturation` | 1.0 | Multipliers |
| `exposure` | 0 | Stops, so 1.0 doubles the light |
| `hue` | 0 | Degrees, rotated about the luma axis |
| `temperature` | 0 | -100 cool to +100 warm |
| `levels` | | Input/output black and white in 0-255, plus gamma |
| `grain` | 0 | 0-100. `grain_seed` keeps it reproducible |
| `blur` | 0 | Gaussian radius in px |
| `sharpen` | 0 | Unsharp mask amount, roughly 0-2 |

Alpha is always preserved. `grain` is seeded, so a re-render is identical.

## Blend modes

`normal`, `multiply`, `screen`, `overlay`, `soft_light`, `hard_light`,
`darken`, `lighten`, `difference`, `exclusion`, `add` / `linear_dodge`,
`subtract`, `linear_burn`, `color_dodge`, `color_burn`.

Compositing follows the W3C separable-blend formula in float, so a blend over a
partly transparent backdrop is correct rather than approximated. `normal` at
opacity 1.0 takes a fast integer path.

## Colours

`#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `[r, g, b]`, `[r, g, b, a]`, or a
name: `black`, `white`, `red`, `green`, `blue`, `gold`, `cream`, `charcoal`,
`ivory`, `navy`, `crimson`, `silver`, `grey`, `transparent`.

## Overriding a project from the command line

```bash
python "$SCRIPT" render plate.json \
  --set layers.3.text='"JELLYBEAN"' \
  --set canvas.background='"#1a1a1a"' \
  -o jellybean.png
```

The path is dotted; numeric segments index into arrays. The value is parsed as
JSON when it can be, so strings need their own quotes inside the shell quotes.
This is how you render one design across a batch without editing the file.
