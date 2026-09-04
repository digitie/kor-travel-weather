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

  it("rejects backslash and non-string values", () => {
    expect(sanitizeLocalPath("/\\\\evil.example")).toBe("/");
    expect(sanitizeLocalPath(null)).toBe("/");
  });
});
