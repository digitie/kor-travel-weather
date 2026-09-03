import { describe, expect, it } from "vitest";

import {
  buildVWorldStyle,
  getVWorldMaxZoom,
  getVWorldTileUrl,
  isVWorldApiKeyConfigured,
  redactVWorldUrl,
} from "./vworld-style";

describe("VWorld style adapter", () => {
  it("builds the upstream WMTS addressing and encodes the browser key", () => {
    const url = getVWorldTileUrl(" public/key ", "Base");
    expect(url).toContain("/public%2Fkey/Base/{z}/{y}/{x}.png");
    expect(redactVWorldUrl(url)).toContain("/***/Base/");
  });

  it("composes the satellite and label layers for Hybrid", () => {
    const style = buildVWorldStyle("test-key", "Hybrid");
    expect(style.layers.map((layer) => layer.id)).toEqual([
      "vworld-satellite-layer",
      "vworld-base-layer",
    ]);
    expect(getVWorldMaxZoom("Hybrid")).toBe(18);
  });

  it("keeps a usable MapLibre fallback when no public key is configured", () => {
    expect(isVWorldApiKeyConfigured(undefined)).toBe(false);
    expect(isVWorldApiKeyConfigured("CHANGE_ME")).toBe(false);
    const style = buildVWorldStyle(undefined);
    expect(style.sources).toEqual({});
    expect(style.layers[0]?.type).toBe("background");
  });
});
