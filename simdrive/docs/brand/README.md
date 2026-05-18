# SimDrive Brand Assets

Vector source for the SimDrive logo system. All marks are hand-coded SVG so they scale infinitely and are git-diff-friendly. Convert to PNG/ICO at the sizes you need with `rsvg-convert`, `inkscape --export-type=png`, or any browser.

## Files

| File | Use | Canvas |
|---|---|---|
| `logo-primary.svg` | README hero, PyPI long description, MCP-registry listing | 1200 × 320 |
| `logo-mark-only.svg` | App icon, social avatar, larger-than-favicon contexts | 200 × 200 |
| `favicon.svg` | Browser tab, ≤32 px contexts | 32 × 32 |
| `wordmark-bracket.svg` | CLI banners, ASCII-adjacent surfaces, monochrome contexts | 880 × 220 |

## Concept

**"Pixel pin."** A 4 × 4 pixel grid (the screenshot the agent sees). Two thin crosshair lines pass through one cell. At the intersection: a vivid red tap-pin. The mark literally depicts the product mechanic — the agent picked that pixel; SimDrive taps it.

The mark scales: at favicon size the grid drops away and the crosshair + pin survive (see `favicon.svg`).

## Colors

| Token | Hex | Use |
|---|---|---|
| Ink | `#0A0A0A` | Wordmark, crosshairs, line work |
| Pixel | `#E5E5E5` | Grid cells (representing screenshot pixels) |
| Signal | `#FF3D2E` | Tap pin — sourced from the SoM annotation red the agent already sees in product |

## Typography

The wordmark sets in a geometric monospace — JetBrains Mono, Berkeley Mono, or IBM Plex Mono are all aligned. Two weights: `600` for `sim`, `400` for `drive`. Lowercase always.

## Voice

For the brand voice that pairs with these marks, see `simdrive/CHANGELOG.md` (the canonical example) and the in-progress brand memo. Short version: state the change then the why; numbers not adverbs; sentence > paragraph; tables when comparing options; never `revolutionizing` / `seamlessly` / `next-generation`.

## Don't

- Render the mark with gradients, drop shadows, glows, or 3D effects.
- Recolor the tap pin (the red is non-negotiable — it's earned from product).
- Use the wordmark with anything other than the geometric monospaces named above.
- Crop the mark so the crosshair extends past the grid bounds.
- Animate the tap pin (no pulses, no blinks). It's a static target.
