# 날씨 관리자 UI

The operator console uses the shared `@kor-travel/ui` admin shell
(`AdminShell` + `AppMenu`, 0.2.5). The weather domain changes only the content
and primary accent hue; navigation, spacing, controls, surfaces, and responsive
behavior remain shared.

## Shared shell

- `components/admin-shell.tsx` wraps the common `AdminShell` per page. Desktop
  (lg, 1024px+) shows a 16rem left rail that collapses to 4rem and remembers
  the choice under `kor-travel-weather:sidebar-collapsed`. Below lg the menu
  folds into a left drawer opened from the top bar menu button.
- The top bar is one line (~48px) on PC and mobile: the title is
  "날씨 - <page>" only (no app tagline/wordmark), and the page description sits
  next to it in small, truncated text. Actions stay at the right; on mobile they
  are icon buttons (labels become screen-reader text).
- Focus is one 2px outline with a 2px offset (`--kt-focus`) issued by the
  `:focus-visible` rule in `@layer base`. Do not add unlayered `button`/`a`
  element or state rules: they beat Tailwind utilities and doubled the button
  border/ring before 2026-11-19.

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

The layout must remain contained at 320px, 375px, 414px, and 768px without
horizontal page scroll. `e2e/admin-shell.spec.ts` covers the drawer, the
one-line title, the single focus outline, and the removed region list.
