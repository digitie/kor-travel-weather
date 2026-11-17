import { describe, expect, it } from "vitest";

import { sanitizeLocalPath } from "./navigation";

describe("sanitizeLocalPath", () => {
  it("keeps a local path including its query and hash", () => {
    expect(sanitizeLocalPath("/weather?location=seoul#forecast")).toBe(
      "/weather?location=seoul#forecast",
    );
  });

  it("rejects protocol-relative and external redirects", () => {
    expect(sanitizeLocalPath("//evil.example/login")).toBe("/");
    expect(sanitizeLocalPath("https://evil.example/login")).toBe("/");
  });

  it("requires the normalised path to stay local, like the shared helper", () => {
    expect(sanitizeLocalPath("/a/..//outside.invalid")).toBe("/");
    expect(sanitizeLocalPath("/.//outside.invalid")).toBe("/");
    expect(sanitizeLocalPath("/a/../b?x=1")).toBe("/b?x=1");
  });

  it("rejects backslash and non-string values", () => {
    expect(sanitizeLocalPath("/\\\\evil.example")).toBe("/");
    expect(sanitizeLocalPath(null)).toBe("/");
  });
});
