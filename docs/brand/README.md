# SimDrive — SVG assets

All marks use **JetBrains Mono Bold (800)**, the cyan accent is **#22D3EE**,
ink is **#0A0A0A**, paper is **#FFFFFF**.

## Files

| File | What | Use it for |
| --- | --- | --- |
| `wordmark.svg` | Primary wordmark on transparent | Headers on light backgrounds, README hero |
| `wordmark-dark.svg` | Wordmark tuned for dark backgrounds | Footers, dark-mode site headers |
| `wordmark-mono.svg` | Monochrome wordmark (`currentColor`) | Terminal output, single-plate print, anywhere color is stripped |
| `sd-favicon.svg` | `sd.` monogram, transparent | Favicon, browser tabs, GitHub avatar — pair with the right bg |
| `sd-favicon-mono.svg` | Monochrome `sd.` (`currentColor`) | Same as above when color isn't an option |
| `avatar-1024-light.svg` | 1024×1024, white field, cyan dot | App Store, PyPI, default avatar slot |
| `avatar-1024-dark.svg` | 1024×1024, black field, cyan dot | Dark-mode app icons |
| `avatar-1024-cyan.svg` | 1024×1024, cyan field, black `sd.` | High-contrast variant — best for dense app drawers |

## How the type is rendered

These SVGs embed a Google Fonts `@import` so JetBrains Mono loads
automatically when the SVG is viewed in a browser, GitHub README, or
anywhere with network access.

**For production print, App Store submissions, or anywhere the network
isn't guaranteed,** convert text to outlines first:

- **Figma**: paste the SVG, select the text, ⌘⇧O ("Outline stroke" / "Flatten")
- **Illustrator**: Type → Create Outlines (⌘⇧O)
- **CLI**: `npx svgo --enable=convertTextToPath` *(experimental — verify result)*

After outlining, the file is self-contained and renders identically in
every viewer.

## Mono variants and `currentColor`

`wordmark-mono.svg` and `sd-favicon-mono.svg` use `fill: currentColor`
so they adapt to the surrounding text color. **This only works when the
SVG is embedded inline** (or via `<use href>`, or as an `<object>`).
SVGs loaded via `<img src>` are isolated documents and resolve
`currentColor` to their own root — which defaults to black.

If you need the mono mark loaded as `<img>`, copy the file and hard-code
the fill to your background pair (e.g. `mono-white.svg` for dark backgrounds).

## Geometry reference

- Wordmark viewBox: `0 0 560 130` · type size 100 · letter-spacing −4
- Favicon viewBox: `0 0 100 100` · type size 62 · letter-spacing −3.7
- 1024 avatar: type size 415 · letter-spacing −25 · dot `dx="-18"`
  (gives ~18% padding around the monogram)

The dot tightening (`dx="-18"` on the avatar, `dx="-2"` on the favicon)
pulls the period close to the "d" so it reads as a typographic terminator
rather than a free-floating point.

## Color tokens

```
--cyan:  #22D3EE
--ink:   #0A0A0A
--paper: #FFFFFF
--sim:   #A3A3A3   /* "sim" tucked, ~50% ink on light bg */
--sim-d: #525252   /* "sim" tucked on dark bg */
```
