# MangaTrax Brand Identity — Sumi Ink & Hanko

**Status:** implemented visual identity (post-v1.1.0, unreleased at time of writing).
**Applies to:** desktop and mobile; the same visual language is shared, with mobile layout adaptations.

## 1. Positioning

**Name:** MangaTrax · **Archetype:** *The Tactical Archivist* — precise, zero-lock, automated,
self-hosted. **Theme:** *Sumi Ink & Screentone* — deep carbon-charcoal surfaces, halftone ink texture,
crisp hairline structure, monospace index data, and Hanko-crimson accents.

The goal is to stop reading as a default Tailwind/SaaS dashboard and start reading as a cataloguing
tool for printed manga: ink, paper, spines, stamps and indexes.

## 2. Colour tokens

Backgrounds move from blue-cast slate to neutral *Sumi Carbon*; text shades are neutralised so cover
art carries the colour.

| Token / role | Previous | Current | Note |
|---|---|---|---|
| `darkbg` canvas | `#0b0f19` | `#090a0f` | Deep sumi ink; covers pop |
| `darkcard` surface | `#161e2e` | `#12141d` | Neutral graphite |
| Slate 850 hover/alt | `#1b2537` | `#171a24` | Elevated row/card state |
| `darkborder` hairline | `#273549` | `#232736` | Sharp structural borders |
| Slate 700–950 | blue-slate | `#2e3346 / #202432 / #171a24 / #0e1017 / #07080c` | Neutral structural ramp |
| Slate 100–600 (text) | blue-slate | `#f4f4f5 / #e4e4e7 / #d4d4d8 / #a1a1aa / #8b8b95 / #7f7f8a` | Neutral typography, WCAG-safe |
| `brand-500` | `#f43f5e` | `#f43f5e` | Hanko crimson; kept for UX-015 focus ring |
| `brand-600` | `#e11d48` | `#e11d48` | Primary CTA / stamp surface |
| Owned volume | emerald pills | muted emerald spine (`#064e3b`/`#34d399`) | Solid shelf-spine look |
| Wanted volume | amber pills | amber ticket (`#451a03`/`#fbbf24`) | High-contrast attention |

Contrast constraints from `UI_ENHANCEMENT_SPEC.md` UX-015 are preserved: white on brand-600 ≥4.5:1,
neutral body/secondary text ≥4.5:1, focus ring 2 px solid crimson. Brand-500 is not used for text.

## 3. Typography

Loaded from Google Fonts in `base.html` (same CDN class as Tailwind/Lucide):

* **Space Grotesk 600/700** — page/series/KPI display headings (`.font-display`, `h1`–`h3`).
* **Plus Jakarta Sans 400–700** — UI and synopsis body (`font-sans`).
* **JetBrains Mono 500–700** — volume badges, speeds, sizes, hashes, technical data (`font-mono`).

## 4. Signature elements

* **Screentone:** a pure-CSS radial dot texture at low opacity on the page canvas and dark cards
  (`base.html` identity CSS layer) — no image requests.
* **Tankōbon volume spines:** volume badges use a sharp 2 px left border and monospace label; owned
  volumes are muted emerald, wanted/missing volumes are brighter amber. Implemented in
  `backend/app/templates/partials/series_card_chunk.html` via `.vol-spine-owned` /
  `.vol-spine-wanted`.
* **Card de-noising:** discovery card top badges are neutral except the volume-1/complete signal and
  the prioritized VIP star; the MangaUpdates synopsis is a quiet left-rule block ("catalog entry")
  instead of a framed box-in-box, and the MangaUpdates link is a neutral metadata chip.
* **Hanko mark:** three tankōbon spines (the third tilted with a bookmark) as an inline SVG logo and
  a data-URI favicon in `base.html`; halo ring `.hanko-ring`.
* **Cover treatment:** cover images get a thin spine shadow and inner edge highlight to feel like
  physical volumes.

## 5. Implementation map

| Surface | File |
|---|---|
| Fonts, favicon, Tailwind token remap, identity CSS, logo, header version pill | `backend/app/templates/base.html` |
| Volume spine badges | `backend/app/templates/partials/series_card_chunk.html` |
| Discovery badge de-noising + synopsis rule + card accent | `backend/app/templates/discovery.html` |
| Version footer / release notes | `CHANGELOG.md`, `README.md` |

## 6. Constraints respected

* `UI_ENHANCEMENT_SPEC.md` **UX-015** (colour/contrast/focus tokens) and **UX-018** (pinned literals,
  DOM ids, test contracts) remain intact; the visual layer is additive CSS/config plus class updates.
* No new runtime dependency, build step or image asset; fonts are the only external addition.
* Desktop and mobile share the same visual language; responsive cards and the bottom bar are unchanged.
