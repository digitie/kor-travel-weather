import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Mirror tsconfig's `@/*` path so unit tests resolve the same imports as
// `next build`. Without it every test file whose module graph touches an
// `@/lib/...` import failed to load (lib/api.test.ts already did on main).
export default defineConfig({
  resolve: {
    alias: [{ find: /^@\//, replacement: fileURLToPath(new URL("./", import.meta.url)) }],
  },
});
