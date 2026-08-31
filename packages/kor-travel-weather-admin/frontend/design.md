# kor-travel-weather admin UI

The operator console follows the current `kor-travel-map` admin shell and
component contract. The weather domain changes only the content and primary
accent hue; navigation, spacing, controls, surfaces, and responsive behavior
remain shared.

## Shared shell

- Rail-workbench layout: a 16rem sticky rail on desktop and a horizontal rail
  strip below 62rem; the desktop rail collapses to 4rem and remembers the
  choice in local storage.
- Navigation uses the same grouped rows, 30px row height, 2px active mark,
  focus ring, and logout footer as `kor-travel-map`.
- The header is a flush border band (20px/24px/16px padding) with a section
  label, title, description, and right-aligned actions. Content uses 24px
  gutters and 24px vertical rhythm.

## Tokens

`app/tokens.css` is the weather-owned token source. It preserves map's two
shape radii (6px controls, 8px panels), 36px/30px controls, Pretendard-first
Korean fallback with Geist for Latin/data, and hairline-only surfaces. The
brand token is navy for weather while success, warning, and destructive tones
retain their semantic colors.

## Domain surfaces

The weather page uses the same map workbench pattern: search and view tabs,
MapLibre canvas, list alternative, and a right inspector for current metrics,
forecast rows, freshness, and lineage. Catalog, datasets, sync runs, Dagster,
API test, and provider settings reuse the shared panel/table/form primitives.

## Verification

Run from this directory:

```bash
npm run lint
npm run type-check
npm run build
```

The layout must remain contained at 320px, 375px, 414px, and 768px. Only the
mobile navigation strip may scroll horizontally.
