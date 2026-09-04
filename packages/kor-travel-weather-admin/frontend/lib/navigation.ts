/**
 * Keep post-login redirects on this admin origin.
 *
 * The value is shared by the browser form and the server route so a caller
 * cannot get a different redirect policy by bypassing the page and posting
 * directly to the API.
 */
export function sanitizeLocalPath(value: unknown, fallback = "/"): string {
  if (typeof value !== "string") return fallback;
  const candidate = value.trim();
  if (
    !candidate ||
    candidate.length > 500 ||
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\")
  ) {
    return fallback;
  }
  try {
    const parsed = new URL(candidate, "http://weather.local");
    if (parsed.origin !== "http://weather.local") return fallback;
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}
