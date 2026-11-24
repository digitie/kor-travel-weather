const SESSION_COOKIE = "ktw_admin_session";
const SESSION_MAX_AGE = 60 * 60 * 8;
const SESSION_SECRET_MIN_LENGTH = 32;
const SESSION_AUDIENCE = "kor-travel-weather-admin-ui";
const SESSION_VERSION = 1;
const SESSION_CLOCK_SKEW_SECONDS = 60;
const SESSION_ID_BYTES = 32;
const MAX_COOKIE_VALUE_LENGTH = 2048;
const BASE64URL_RE = /^[0-9A-Za-z_-]+$/;
const PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256";
const PASSWORD_HASH_ITERATIONS = 310_000;

type HeaderReader = { get(name: string): string | null };
type RequestLike = { headers: HeaderReader };
type SessionSource = HeaderReader | RequestLike | null;
type Env = Record<string, string | undefined>;
type RevocationStore = Map<string, number>;

type SessionPayload = {
  aud: string;
  exp: number;
  fp: string;
  iat: number;
  sid: string;
  sub: string;
  v: number;
};

type DurableSessionCache = {
  /** 서명 쿠키 값 → active 판정 재사용 만료 시각(ms). */
  active: Map<string, number>;
  /** 서명 쿠키 값 → 진행 중인 단일 확인. */
  inflight: Map<string, Promise<DurableSessionState>>;
  /** 서명 쿠키 값 → 다시 캐시하지 않을 만료 시각(ms). 로그아웃·재로그인 직후 경합을 막는다. */
  forgotten: Map<string, number>;
};

type SessionGlobal = typeof globalThis & {
  __korTravelWeatherRevokedSessions?: RevocationStore;
  __korTravelWeatherDurableSessionCache?: DurableSessionCache;
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

/**
 * Check the shared backend revocation store used by every web replica.
 *
 * The browser cookie remains a signed value, but a logout must also survive a
 * Next.js restart or a request landing on another replica.  Fail closed in
 * production when the internal API contract is unavailable; development keeps
 * the local-only workflow usable.
 */
export type DurableSessionState = "active" | "revoked" | "unavailable";
const DURABLE_AUTH_READ_TIMEOUT_MS = 5000;

// durable 확인 결과의 프로세스 메모리 캐시(docs/weather-admin-map-call-resilience.md B1).
// - active 판정만 TTL(기본 10초) 동안 재사용한다. revoked·unavailable은 저장하지 않으므로
//   저장소 장애는 지금처럼 매 요청 503(fail-closed)이다.
// - 같은 쿠키의 동시 확인은 진행 중인 요청 하나를 공유한다(single-flight).
// - 키는 서명 쿠키 값 전체다. 백엔드 폐기 저장소도 같은 값의 digest를 보므로 sid와 1:1이고,
//   서명을 확인하지 않은 값이 다른 쿠키의 판정을 대신할 수 없다.
// - 이 프로세스의 로그아웃·재로그인은 해당 쿠키를 즉시 잊고 폐기 기록이 끝날 동안 다시 캐시하지
//   않는다. 다른 web replica·runtime에서 한 로그아웃은 최대 TTL 늦게 반영된다.
const DURABLE_SESSION_CACHE_DEFAULT_SECONDS = 10;
const DURABLE_SESSION_CACHE_MAX_SECONDS = 60;
const DURABLE_SESSION_CACHE_MAX_ENTRIES = 1024;
// 폐기 기록 쓰기 예산(5초)과 TTL 상한보다 길게 둔다.
const DURABLE_SESSION_FORGET_HOLD_MS = 60_000;

function durableSessionCache(): DurableSessionCache {
  const root = globalThis as SessionGlobal;
  if (!root.__korTravelWeatherDurableSessionCache) {
    root.__korTravelWeatherDurableSessionCache = {
      active: new Map(),
      inflight: new Map(),
      forgotten: new Map(),
    };
  }
  return root.__korTravelWeatherDurableSessionCache;
}

/**
 * active 판정 재사용 시간(ms). `WEATHER_UI_SESSION_CHECK_CACHE_SECONDS`가 비어 있으면 10초,
 * 0이면 캐시와 동시 확인 공유를 모두 끈다(이전 동작). 숫자가 아니거나 음수면 보수적으로 끈다.
 */
export function durableSessionCacheTtlMs(env: Env = process.env): number {
  const raw = env.WEATHER_UI_SESSION_CHECK_CACHE_SECONDS?.trim();
  if (!raw) return DURABLE_SESSION_CACHE_DEFAULT_SECONDS * 1000;
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds <= 0) return 0;
  return Math.round(Math.min(seconds, DURABLE_SESSION_CACHE_MAX_SECONDS) * 1000);
}

function boundedSet(store: Map<string, number>, key: string, expiresAt: number, now: number) {
  store.delete(key);
  if (store.size >= DURABLE_SESSION_CACHE_MAX_ENTRIES) {
    for (const [entry, until] of store) if (until <= now) store.delete(entry);
  }
  // 상한을 넘으면 가장 오래 넣은 항목부터 버린다. 버려진 active는 다음 요청에서 다시 확인한다.
  while (store.size >= DURABLE_SESSION_CACHE_MAX_ENTRIES) {
    const oldest = store.keys().next().value;
    if (oldest === undefined) break;
    store.delete(oldest);
  }
  store.set(key, expiresAt);
}

function recentlyForgotten(cache: DurableSessionCache, session: string, now: number): boolean {
  const until = cache.forgotten.get(session);
  if (until === undefined) return false;
  if (until > now) return true;
  cache.forgotten.delete(session);
  return false;
}

/**
 * 이 프로세스의 확인 캐시에서 쿠키 하나를 지운다. 로그아웃·재로그인처럼 쿠키를 폐기하는 요청이
 * 실제 폐기 기록 전에 호출한다. 진행 중이던 확인 결과도 저장하지 않는다.
 */
export function forgetDurableSession(session: string | undefined, now = Date.now()) {
  if (!session || session.length > MAX_COOKIE_VALUE_LENGTH) return;
  const cache = durableSessionCache();
  cache.active.delete(session);
  cache.inflight.delete(session);
  boundedSet(cache.forgotten, session, now + DURABLE_SESSION_FORGET_HOLD_MS, now);
}

/** 확인 캐시 전체를 비운다. 시험과 설정 변경 뒤 재설정용이다. */
export function clearDurableSessionCache() {
  const cache = durableSessionCache();
  cache.active.clear();
  cache.inflight.clear();
  cache.forgotten.clear();
}

export async function checkDurableSession(
  session: string,
  env: Env = process.env,
): Promise<DurableSessionState> {
  if (env.NODE_ENV !== "production") return "active";
  const ttlMs = durableSessionCacheTtlMs(env);
  if (ttlMs <= 0) return fetchDurableSessionState(session, env);
  const cache = durableSessionCache();
  const cachedUntil = cache.active.get(session);
  if (cachedUntil !== undefined) {
    if (cachedUntil > Date.now()) return "active";
    cache.active.delete(session);
  }
  const pending = cache.inflight.get(session);
  if (pending) return pending;
  const lookup: Promise<DurableSessionState> = fetchDurableSessionState(session, env).then((state) => {
    // forget 뒤에 끝난 확인은 공유 목록에서 이미 빠졌으므로 저장하지 않는다.
    if (cache.inflight.get(session) !== lookup) return state;
    cache.inflight.delete(session);
    const now = Date.now();
    if (state === "active" && !recentlyForgotten(cache, session, now)) {
      boundedSet(cache.active, session, now + ttlMs, now);
    } else {
      cache.active.delete(session);
    }
    return state;
  });
  cache.inflight.set(session, lookup);
  return lookup;
}

async function fetchDurableSessionState(
  session: string,
  env: Env,
): Promise<DurableSessionState> {
  const apiBase = env.WEATHER_API_INTERNAL_URL?.trim();
  const adminToken = env.WEATHER_ADMIN_TOKEN?.trim();
  if (!apiBase || !adminToken) return "unavailable";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DURABLE_AUTH_READ_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${apiBase.replace(/\/$/, "")}/v1/admin/session-revocations/check`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-admin-token": adminToken },
        body: JSON.stringify({ session }),
        cache: "no-store",
        signal: controller.signal,
      },
    );
    if (response.status !== 200) return "unavailable";
    const payload: unknown = await response.json();
    if (
      !payload || typeof payload !== "object" || Array.isArray(payload) ||
      !("revoked" in payload) || typeof payload.revoked !== "boolean"
    ) return "unavailable";
    return payload.revoked ? "revoked" : "active";
  } catch {
    return "unavailable";
  } finally {
    clearTimeout(timeout);
  }
}

/** Persist a logout marker in the shared backend store. */
// 읽기·쓰기 모두 유한한 5초 예산을 사용한다. CPU가 바쁜 호스트의 일시 지연을
// 세션 폐기로 해석하지 않으며, 저장소를 확인하지 못하면 접근은 계속 차단한다.
// 자동 재시도는 두지 않는다. 권한 허용 재사용은 읽기의 active 판정을 위 TTL 동안만 한다.
const DURABLE_AUTH_WRITE_TIMEOUT_MS = 5000;

export async function durableRevokeSession(
  session: string,
  env: Env = process.env,
): Promise<boolean> {
  if (env.NODE_ENV !== "production") return true;
  const apiBase = env.WEATHER_API_INTERNAL_URL?.trim();
  const adminToken = env.WEATHER_ADMIN_TOKEN?.trim();
  if (!apiBase || !adminToken) return false;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DURABLE_AUTH_WRITE_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${apiBase.replace(/\/$/, "")}/v1/admin/session-revocations/revoke`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-admin-token": adminToken },
        body: JSON.stringify({ session }),
        cache: "no-store",
        signal: controller.signal,
      },
    );
    if (response.status !== 200) return false;
    const payload: unknown = await response.json();
    return Boolean(
      payload && typeof payload === "object" && !Array.isArray(payload) &&
      "revoked" in payload && payload.revoked === true,
    );
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

export type DurableLoginRateLimitResult =
  | { available: true; retryAfter: number | null }
  | { available: false };

/** Hash a trusted client bucket before it crosses the internal API boundary. */
export async function hashLoginBucket(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Check one shared login-failure bucket with a short fail-closed timeout. */
export async function checkDurableLoginRateLimit(
  bucketHash: string,
  env: Env = process.env,
): Promise<DurableLoginRateLimitResult> {
  return postDurableLoginRateLimit("check", bucketHash, env);
}

/** Record one failed credential attempt in the shared bucket. */
export async function recordDurableLoginFailure(
  bucketHash: string,
  env: Env = process.env,
): Promise<DurableLoginRateLimitResult> {
  return postDurableLoginRateLimit("failure", bucketHash, env);
}

/** Clear shared failures after a successful login. */
export async function clearDurableLoginFailures(
  bucketHash: string,
  env: Env = process.env,
): Promise<DurableLoginRateLimitResult> {
  return postDurableLoginRateLimit("success", bucketHash, env);
}

async function postDurableLoginRateLimit(
  action: "check" | "failure" | "success",
  bucketHash: string,
  env: Env,
): Promise<DurableLoginRateLimitResult> {
  if (env.NODE_ENV !== "production") return { available: true, retryAfter: null };
  const started = performance.now();
  let upstreamStatus: number | null = null;
  let readingReply = false;
  const unavailable = (
    reason: "configuration" | "timeout" | "transport" | "non-200" | "invalid-reply",
  ): DurableLoginRateLimitResult => {
    // 원인 분류만 남긴다. 주소·계정·token·bucket·응답 본문은 로그에 넣지 않는다.
    try {
      console.warn(JSON.stringify({
        event: "weather.auth.rate_limit.unavailable",
        action,
        reason,
        upstream_status: upstreamStatus,
        elapsed_ms: Math.max(0, Math.round(performance.now() - started)),
      }));
    } catch {
      // 관측 실패가 기존 fail-closed 응답을 바꾸지 않게 한다.
    }
    return { available: false };
  };
  const apiBase = env.WEATHER_API_INTERNAL_URL?.trim();
  const adminToken = env.WEATHER_ADMIN_TOKEN?.trim();
  if (!apiBase || !adminToken || !/^[0-9a-f]{64}$/.test(bucketHash)) {
    return unavailable("configuration");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DURABLE_AUTH_WRITE_TIMEOUT_MS);
  try {
    const response = await fetch(
      `${apiBase.replace(/\/$/, "")}/v1/admin/login-rate-limit/${action}`,
      {
        method: "POST",
        headers: { "content-type": "application/json", "x-admin-token": adminToken },
        body: JSON.stringify({ bucket_hash: bucketHash }),
        cache: "no-store",
        signal: controller.signal,
      },
    );
    upstreamStatus = response.status;
    if (response.status !== 200) return unavailable("non-200");
    readingReply = true;
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return unavailable("invalid-reply");
    }
    if (action === "success") {
      return "cleared" in payload && payload.cleared === true
        ? { available: true, retryAfter: null }
        : unavailable("invalid-reply");
    }
    if (!("allowed" in payload) || typeof payload.allowed !== "boolean" ||
        !("retry_after" in payload)) return unavailable("invalid-reply");
    if (payload.allowed && payload.retry_after === null) {
      return { available: true, retryAfter: null };
    }
    if (!payload.allowed && typeof payload.retry_after === "number" &&
        Number.isInteger(payload.retry_after) && payload.retry_after > 0 &&
        payload.retry_after <= 24 * 60 * 60) {
      return { available: true, retryAfter: payload.retry_after };
    }
    return unavailable("invalid-reply");
  } catch (error) {
    return unavailable(
      controller.signal.aborted ? "timeout" :
        readingReply && error instanceof SyntaxError ? "invalid-reply" : "transport",
    );
  } finally {
    clearTimeout(timeout);
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

/** Resolve the configured admin username and support the Geo-style hash contract. */
export function adminUsername(env: Env = process.env): string {
  return env.WEATHER_UI_USER?.trim() || "admin";
}

/**
 * Verify the configured admin credentials without exposing the configured password.
 *
 * Deployments may continue to use WEATHER_UI_PASSWORD for compatibility. New deployments
 * can provide WEATHER_UI_PASSWORD_HASH (pbkdf2_sha256$iterations$salt$hash), matching the
 * kor-travel-geo password-verification contract; the hash takes precedence when present.
 */
export async function verifyAdminLogin(
  input: { username: string; password: string },
  env: Env = process.env,
): Promise<"ok" | "invalid" | "misconfigured"> {
  const expectedUsername = adminUsername(env);
  const configuredPassword = env.WEATHER_UI_PASSWORD;
  const passwordHash = env.WEATHER_UI_PASSWORD_HASH?.trim();
  if (!passwordHash && !configuredPassword) return "misconfigured";

  const usernameMatches = constantTimeEqual(input.username.trim(), expectedUsername);
  const passwordMatches = passwordHash
    ? await verifyPassword(input.password, passwordHash)
    : constantTimeEqual(input.password, configuredPassword ?? "");
  return usernameMatches && passwordMatches ? "ok" : "invalid";
}

/** Create a Geo-compatible PBKDF2 password hash for secret-manager provisioning. */
export async function hashAdminPasswordForEnv(
  password: string,
  salt: Uint8Array = randomBytes(16),
  iterations = PASSWORD_HASH_ITERATIONS,
): Promise<string> {
  const hash = await pbkdf2(password, salt, iterations);
  return [
    PASSWORD_HASH_ALGORITHM,
    String(iterations),
    toBase64Url(salt),
    toBase64Url(hash),
  ].join("$");
}

export function sessionSecret(username: string, password: string, env: Env = process.env): string {
  const configured = env.WEATHER_UI_SESSION_SECRET?.trim();
  if (env.NODE_ENV === "production") {
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

export async function createSessionValue(
  username: string,
  secret: string,
  source: SessionSource = null,
  nowMs = Date.now(),
): Promise<string> {
  const issuedAt = Math.floor(nowMs / 1000);
  const payload: SessionPayload = {
    aud: SESSION_AUDIENCE,
    exp: issuedAt + SESSION_MAX_AGE,
    fp: await sessionFingerprint(source, secret),
    iat: issuedAt,
    sid: toBase64Url(randomBytes(SESSION_ID_BYTES)),
    sub: username,
    v: SESSION_VERSION,
  };
  const payloadPart = toBase64Url(new TextEncoder().encode(JSON.stringify(payload)));
  return `${payloadPart}.${await signature(payloadPart, secret)}`;
}

/** Revoke one browser session until its natural expiry. */
export function revokeSessionValue(value: string | undefined) {
  if (!value || value.length > MAX_COOKIE_VALUE_LENGTH) return;
  const [encodedPayload] = value.split(".");
  if (!encodedPayload || !isBase64UrlString(encodedPayload)) return;

  let key = encodedPayload;
  let expiresAt = Date.now() + SESSION_MAX_AGE * 1000;
  try {
    const parsed = JSON.parse(
      new TextDecoder().decode(fromBase64Url(encodedPayload)),
    ) as Partial<SessionPayload>;
    if (typeof parsed.sid === "string" && isBase64UrlString(parsed.sid)) key = parsed.sid;
    if (typeof parsed.exp === "number" && Number.isFinite(parsed.exp)) {
      expiresAt = parsed.exp * 1000;
    }
  } catch {
    // An invalid cookie has no useful expiry; keep a short-lived marker so a concurrent request
    // cannot race the logout response. The route still clears the browser cookie.
  }
  pruneRevocations();
  revocationStore().set(key, Math.max(Date.now() + 1000, expiresAt));
  forgetDurableSession(value);
}

/**
 * Verify a signed session and return its subject, matching kor-travel-geo's session contract.
 * The optional expectedUsername is used by the middleware/route to bind the cookie to the
 * currently configured account during username rotation.
 */
export async function verifySessionValue(
  value: string | undefined,
  secret: string,
  source: SessionSource = null,
  expectedUsername?: string,
  nowMs = Date.now(),
): Promise<string | null> {
  if (!value || value.length > MAX_COOKIE_VALUE_LENGTH) return null;
  const [encodedPayload, suppliedSignature, extra] = value.split(".");
  if (
    !encodedPayload ||
    !suppliedSignature ||
    extra !== undefined ||
    !isBase64UrlString(encodedPayload) ||
    !isBase64UrlString(suppliedSignature)
  ) {
    return null;
  }

  try {
    pruneRevocations(nowMs);
    const expected = await signature(encodedPayload, secret);
    if (!constantTimeEqual(suppliedSignature, expected)) return null;
    const payload = JSON.parse(
      new TextDecoder().decode(fromBase64Url(encodedPayload)),
    ) as Partial<SessionPayload>;
    if (!sessionPayloadHasShape(payload)) return null;

    const nowSeconds = Math.floor(nowMs / 1000);
    if (
      payload.exp <= nowSeconds ||
      payload.iat > nowSeconds + SESSION_CLOCK_SKEW_SECONDS ||
      payload.exp - payload.iat > SESSION_MAX_AGE + SESSION_CLOCK_SKEW_SECONDS ||
      (expectedUsername !== undefined && payload.sub !== expectedUsername) ||
      revocationStore().has(payload.sid) ||
      revocationStore().has(encodedPayload)
    ) {
      return null;
    }
    if (!constantTimeEqual(payload.fp, await sessionFingerprint(source, secret))) return null;
    return payload.sub;
  } catch {
    return null;
  }
}

function sessionPayloadHasShape(payload: Partial<SessionPayload>): payload is SessionPayload {
  return (
    payload.aud === SESSION_AUDIENCE &&
    payload.v === SESSION_VERSION &&
    typeof payload.exp === "number" &&
    Number.isInteger(payload.exp) &&
    typeof payload.fp === "string" &&
    isBase64UrlString(payload.fp) &&
    typeof payload.iat === "number" &&
    Number.isInteger(payload.iat) &&
    typeof payload.sid === "string" &&
    isBase64UrlString(payload.sid) &&
    typeof payload.sub === "string" &&
    payload.sub.length > 0 &&
    payload.sub.length <= 128
  );
}

async function sessionFingerprint(source: SessionSource, secret: string): Promise<string> {
  const userAgent = (headersFrom(source)?.get("user-agent") ?? "").slice(0, 300);
  return signature(`fingerprint:${userAgent}`, secret);
}

async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  const parts = encoded.split("$");
  if (parts.length !== 4 || parts[0] !== PASSWORD_HASH_ALGORITHM) return false;
  const iterations = Number(parts[1]);
  if (!Number.isInteger(iterations) || iterations < 100_000 || iterations > 2_000_000) {
    return false;
  }
  try {
    const salt = fromBase64Url(parts[2]);
    const expected = fromBase64Url(parts[3]);
    const actual = await pbkdf2(password, salt, iterations);
    return constantTimeEqualBytes(actual, expected);
  } catch {
    return false;
  }
}

async function pbkdf2(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: salt as BufferSource, iterations },
    key,
    256,
  );
  return new Uint8Array(bits);
}

async function signature(payload: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { hash: "SHA-256", name: "HMAC" },
    false,
    ["sign"],
  );
  return toBase64Url(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload)),
  );
}

function headersFrom(source: SessionSource): HeaderReader | null {
  if (!source) return null;
  if (typeof (source as HeaderReader).get === "function") return source as HeaderReader;
  const inner = (source as RequestLike).headers;
  return inner && typeof inner.get === "function" ? inner : null;
}

function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return bytes;
}

function toBase64Url(value: ArrayBuffer | Uint8Array): string {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64Url(value: string): Uint8Array {
  if (!isBase64UrlString(value) || value.length % 4 === 1) throw new Error("invalid base64url");
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(
    normalized.length + ((4 - (normalized.length % 4)) % 4),
    "=",
  );
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function isBase64UrlString(value: string): boolean {
  return value.length > 0 && BASE64URL_RE.test(value);
}

function constantTimeEqual(left: string, right: string): boolean {
  return constantTimeEqualBytes(new TextEncoder().encode(left), new TextEncoder().encode(right));
}

function constantTimeEqualBytes(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
  return difference === 0;
}

export {
  SESSION_AUDIENCE,
  SESSION_COOKIE,
  SESSION_MAX_AGE,
  SESSION_SECRET_MIN_LENGTH,
  SESSION_VERSION,
};
