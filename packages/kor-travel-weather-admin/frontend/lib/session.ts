const SESSION_COOKIE = "ktw_admin_session";
const SESSION_MAX_AGE = 60 * 60 * 8;
const SESSION_SECRET_MIN_LENGTH = 32;

type RevocationStore = Map<string, number>;
type SessionGlobal = typeof globalThis & {
  __korTravelWeatherRevokedSessions?: RevocationStore;
};

function revocationStore(): RevocationStore {
  const root = globalThis as SessionGlobal;
  if (!root.__korTravelWeatherRevokedSessions) {
    root.__korTravelWeatherRevokedSessions = new Map<string, number>();
  }
  return root.__korTravelWeatherRevokedSessions;
}

function pruneRevocations(now = Date.now()) {
  for (const [key, expiresAt] of revocationStore()) {
    if (expiresAt <= now) revocationStore().delete(key);
  }
}

const PLACEHOLDER_SESSION_SECRETS = new Set([
  "change-me",
  "change-this-secret",
  "change-this-session-secret",
  "changeme",
  "password",
  "placeholder",
  "replace-me",
  "replace-this-secret",
  "replace-this-session-secret",
  "secret",
  "test-secret",
  "your-secret",
  "your-session-secret",
]);

function isPlaceholderSessionSecret(value: string) {
  const normalized = value.toLowerCase().replace(/[\s_.]+/g, "-");
  return (
    PLACEHOLDER_SESSION_SECRETS.has(normalized) ||
    normalized.startsWith("change-this-") ||
    normalized.startsWith("replace-this-")
  );
}

function toBase64Url(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function signature(payload: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { hash: "SHA-256", name: "HMAC" },
    false,
    ["sign", "verify"],
  );
  return toBase64Url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload)));
}

export function sessionSecret(username: string, password: string): string {
  const configured = process.env.WEATHER_UI_SESSION_SECRET?.trim();
  if (process.env.NODE_ENV === "production") {
    if (
      !configured ||
      configured.length < SESSION_SECRET_MIN_LENGTH ||
      isPlaceholderSessionSecret(configured)
    ) {
      throw new Error(
        `production에서는 WEATHER_UI_SESSION_SECRET에 ${SESSION_SECRET_MIN_LENGTH}바이트 이상의 무작위 값을 설정해야 합니다.`,
      );
    }
  }
  return configured || `${username}:${password}`;
}

export async function createSessionValue(username: string, secret: string): Promise<string> {
  const nonce = toBase64Url(crypto.getRandomValues(new Uint8Array(18)));
  const payload = `${username}.${Math.floor(Date.now() / 1000) + SESSION_MAX_AGE}.${nonce}`;
  return `${toBase64Url(new TextEncoder().encode(payload))}.${await signature(payload, secret)}`;
}

/** Revoke one browser session until its natural expiry. */
export function revokeSessionValue(value: string | undefined) {
  if (!value) return;
  const [encodedPayload] = value.split(".");
  if (!encodedPayload) return;
  let expiresAt = Date.now() + SESSION_MAX_AGE * 1000;
  try {
    const payload = new TextDecoder().decode(fromBase64Url(encodedPayload));
    const parsedExpiry = Number(payload.split(".")[1]);
    if (Number.isFinite(parsedExpiry)) expiresAt = parsedExpiry * 1000;
  } catch {
    // An invalid cookie has no useful expiry; keep a short-lived revocation
    // marker so a concurrent request cannot race the logout response.
  }
  pruneRevocations();
  revocationStore().set(encodedPayload, Math.max(Date.now() + 1000, expiresAt));
}

export async function verifySessionValue(value: string | undefined, secret: string): Promise<string | null> {
  if (!value) return null;
  const [encodedPayload, suppliedSignature] = value.split(".");
  if (!encodedPayload || !suppliedSignature) return null;
  try {
    pruneRevocations();
    if (revocationStore().has(encodedPayload)) return null;
    const payload = new TextDecoder().decode(fromBase64Url(encodedPayload));
    const [username, expiresAt] = payload.split(".");
    if (!username || !expiresAt || Number(expiresAt) < Math.floor(Date.now() / 1000)) return null;
    const expected = await signature(payload, secret);
    const expectedBytes = fromBase64Url(expected);
    const suppliedBytes = fromBase64Url(suppliedSignature);
    if (expectedBytes.length !== suppliedBytes.length) return null;
    let different = 0;
    for (let index = 0; index < expectedBytes.length; index += 1) different |= expectedBytes[index] ^ suppliedBytes[index];
    return different === 0 ? username : null;
  } catch {
    return null;
  }
}

export { SESSION_COOKIE, SESSION_MAX_AGE, SESSION_SECRET_MIN_LENGTH };
