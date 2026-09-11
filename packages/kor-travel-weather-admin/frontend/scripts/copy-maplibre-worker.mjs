// maplibre-gl v6 loads its worker as `new URL('maplibre-gl-worker.mjs',
// import.meta.url)`. Next.js's own asset handling turns that into a hashed
// asset without emitting the worker's `maplibre-gl-shared.mjs` sibling next
// to it -- the worker then fails on its first import and the map mounts but
// never requests a tile. This applies to both of Next's bundler modes
// (`next build` / Turbopack and `next build --webpack`), since the asset
// handling that breaks it is Next's, not the bundler's.
//
// The fix (from maplibre-gl's own installation docs, "Next.js" tab): serve
// both files from `public/` and point `setWorkerUrl` at the worker
// (vworld-map-view.tsx does that once, at module load). This script performs
// the copy at build/dev time, from node_modules, so it always matches the
// installed maplibre-gl version -- nothing under public/maplibre is checked
// in.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const dist = path.join(
  path.dirname(createRequire(import.meta.url).resolve("maplibre-gl/package.json")),
  "dist",
);
const dest = path.join(process.cwd(), "public", "maplibre");

mkdirSync(dest, { recursive: true });
for (const file of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(path.join(dist, file), path.join(dest, file));
}
