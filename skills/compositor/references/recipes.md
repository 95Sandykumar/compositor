# Recipes: common revision asks with no model call

Each recipe is a project JSON. `approved.png` is the file the customer
picked. `bg.png` is a background mask from
`retouch.py mask approved.png --points "<clicks on the background>;<red clicks on the subject>" -o bg.png --feather 2`.
Tested 2026-11-14 on a 1024x1024 charcoal portrait: 4 green clicks in the
corners and sides plus 2 red clicks on the people gave a clean background mask.

## Recolour the backdrop

"Make the background violet / richer / a different colour."

```json
{ "canvas": { "width": 1024, "height": 1024 },
  "layers": [
    { "name": "approved", "type": "image", "src": "approved.png" },
    { "name": "backdrop recolour", "type": "image", "src": "approved.png",
      "adjust": { "hue": -150, "saturation": 1.6 },
      "mask": { "src": "bg.png" } }
  ] }
```

- `hue` is degrees. Try steps of 15 and show a strip of options rather than
  guessing one.
- A tint of a chosen colour instead: replace the second layer with
  `{ "type": "fill", "color": "#3b2a6b", "blend": "soft_light", "mask": { "src": "bg.png" } }`.
  `soft_light` or `multiply` keep the paper texture; `normal` paints it flat.

## Halo or glow behind the subject

"Add a soft halo behind her, like the other portrait."

```json
{ "name": "halo", "type": "gradient", "gradient_type": "radial",
  "cx": 0.5, "cy": 0.35, "radius": 0.55,
  "stops": [[0, "#fff4d6ee"], [1, "#fff4d600"]],
  "blend": "screen", "opacity": 0.8,
  "mask": { "src": "bg.png" } }
```

Put `cx`, `cy` on the head. Because of the mask, the glow sits behind the
subject and never washes over the face.

## Darker or lighter in one area only

"Her face is too dark." Click the face, then:

```json
{ "name": "lift face", "type": "image", "src": "approved.png",
  "adjust": { "exposure": 0.25 },
  "mask": { "src": "face.png", "feather": 12 } }
```

## Sharpen to match another picture

```bash
python compositor.py adjust approved.png --sharpen 0.8 -o sharper.png
```

Compare full size against the reference before sending.

## Mirror one element (a window, a bloom)

Click the element, then place a flipped copy:

```json
{ "name": "mirrored window", "type": "image", "src": "approved.png",
  "flip": "h", "mask": { "src": "window-flipped.png", "feather": 4 } }
```

Flip the mask the same way (`compositor.py composite` on a blank canvas, or
PIL `ImageOps.mirror`). Then `retouch.py heal` the spot the element left.
