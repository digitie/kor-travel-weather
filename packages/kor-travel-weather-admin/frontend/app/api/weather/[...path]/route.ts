import { NextRequest } from "next/server";

const apiBase = () => process.env.WEATHER_API_INTERNAL_URL ?? "http://127.0.0.1:14101";
const MAX_PROXY_BODY_BYTES = 1_048_576;
// 위치 목록과 공급자 키 상태의 읽기만 헤더 전 전송 장애를 한 번 복구한다.
// headers 이후 stream·다른 GET·관리 write에는 새 상한을 두지 않는다.
const READ_HEADERS_BUDGET_MS = 20_000;
const RECOVERABLE_READ_PATHS = new Set([
  "v1/weather/locations",
  "v1/admin/provider-credentials",
]);
const TRANSIENT_CODES = new Set(["UND_ERR_SOCKET", "ECONNRESET", "EPIPE"]);

function transientCode(error: unknown): string | null {
  if (!(error instanceof TypeError) || !("cause" in error)) return null;
  const cause = error.cause;
  if (typeof cause !== "object" || cause === null || !("code" in cause)) return null;
  return typeof cause.code === "string" && TRANSIENT_CODES.has(cause.code) ? cause.code : null;
}

function unavailable(status: 499 | 503 | 504) {
  return Response.json(
    { detail: status === 499 ? "요청이 취소되었습니다." : "날씨 서버 응답을 받지 못했습니다. 잠시 후 다시 시도하세요." },
    { status, headers: { "cache-control": "no-store, private", ...(status === 499 ? {} : { "retry-after": "1" }) } },
  );
}

async function readBody(request: NextRequest): Promise<ArrayBuffer | null | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PROXY_BODY_BYTES) return null;
  const reader = request.body?.getReader();
  if (!reader) return new ArrayBuffer(0);
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > MAX_PROXY_BODY_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(next.value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new ArrayBuffer(total);
  const bytes = new Uint8Array(body);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

// Next catchall은 이미 decoding한 segment를 준다. URL이 dot/separator/percent를
// 다시 해석할 여지를 남기지 않고, 서비스 경로는 서버 토큰 대입 전에 거부한다.
function browserProxyPathAllowed(path: string[]): boolean {
  if (path.length === 0 || path.some((part) =>
    !part || part === "." || part === ".." || /[%\\/\s?#\u0000-\u001f\u007f]/u.test(part)
  )) return false;
  return !(path[0] === "v1" && path[1] === "service");
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!browserProxyPathAllowed(path)) {
    return Response.json({ detail: "요청 경로를 찾을 수 없습니다." }, {
      status: 404, headers: { "cache-control": "no-store, private" },
    });
  }
  if (request.signal.aborted) return unavailable(499);
  const target = new URL(`${apiBase()}/${path.join("/")}`);
  // Preserve repeated query keys such as marker batches (`location_id`);
  // `set()` would silently forward only the last requested location.
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.append(key, value));
  // Forward only harmless content negotiation headers. In particular, do not
  // leak the browser's Basic-Auth header/cookies to the backend; the proxy
  // supplies the server-side admin token below.
  const headers = new Headers();
  for (const name of ["accept", "content-type", "if-none-match", "x-request-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set(
    "x-admin-token",
    process.env.WEATHER_ADMIN_TOKEN ?? process.env.KOR_TRAVEL_WEATHER_ADMIN_TOKEN ?? "",
  );
  const body = await readBody(request);
  if (body === null) {
    return Response.json(
      { detail: "요청 본문이 너무 큽니다." },
      { status: 413, headers: { "cache-control": "no-store" } },
    );
  }
  const recoverRead = request.method === "GET" && RECOVERABLE_READ_PATHS.has(path.join("/"));
  const upstream = new AbortController();
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let timedOut = false;
  const deadline = performance.now() + READ_HEADERS_BUDGET_MS;
  // event-loop가 밀려 timer callback보다 fetch가 먼저 재개돼도 총예산을 지킨다.
  const expired = () => timedOut || (recoverRead && performance.now() >= deadline);
  const timer = recoverRead ? setTimeout(() => {
    timedOut = true;
    upstream.abort();
  }, READ_HEADERS_BUDGET_MS) : undefined;
  const cleanup = () => {
    clearTimeout(timer);
    request.signal.removeEventListener("abort", abort);
  };
  const abort = () => {
    upstream.abort();
    // downstream이 더 읽지 않아도 취소 자체가 reader와 listener를 회수한다.
    const activeReader = reader;
    if (activeReader) {
      void activeReader.cancel().catch(() => {}).finally(() => {
        activeReader.releaseLock();
        if (reader === activeReader) reader = undefined;
        cleanup();
      });
    } else cleanup();
  };
  request.signal.addEventListener("abort", abort, { once: true });
  if (request.signal.aborted) abort();

  for (let attempt = 1; attempt <= (recoverRead ? 2 : 1); attempt += 1) {
    try {
      if (request.signal.aborted) { cleanup(); return unavailable(499); }
      if (expired()) { upstream.abort(); cleanup(); return unavailable(504); }
      const response = await fetch(target, {
        method: request.method, headers, body, cache: "no-store", signal: upstream.signal,
      });
      // HTTP status는 재시도하지 않는다. body를 받은 뒤에도 새 제한을 부과하지 않는다.
      clearTimeout(timer);
      if (request.signal.aborted || expired()) {
        upstream.abort();
        await response.body?.cancel().catch(() => {});
        cleanup();
        return unavailable(request.signal.aborted ? 499 : 504);
      }
      let forwardedBody: ReadableStream<Uint8Array> | null = null;
      if (response.body) {
        reader = response.body.getReader();
        const bodyReader = reader;
        forwardedBody = new ReadableStream<Uint8Array>({
          async pull(controller) {
            try {
              const result = await bodyReader.read();
              if (request.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
              if (result.done) {
                controller.close();
                bodyReader.releaseLock();
                cleanup();
              } else controller.enqueue(result.value);
            } catch {
              // 일부 body가 이미 전달됐을 수 있어 재시도하거나503 body로 바꾸지 않는다.
              controller.error(new TypeError("날씨 서버 응답 전송이 중단됐습니다."));
              await bodyReader.cancel().catch(() => {});
              bodyReader.releaseLock();
              cleanup();
            }
          },
          async cancel() {
            upstream.abort();
            try { await bodyReader.cancel(); } finally { bodyReader.releaseLock(); cleanup(); }
          },
        });
      } else cleanup();
      return new Response(forwardedBody, {
        status: response.status,
        headers: {
          "content-type": response.headers.get("content-type") ?? "application/json",
          "cache-control": "no-store, private",
          "x-request-id": response.headers.get("x-request-id") ?? "",
        },
      });
    } catch (error) {
      if (request.signal.aborted || expired()) {
        upstream.abort();
        cleanup();
        return unavailable(request.signal.aborted ? 499 : 504);
      }
      const code = transientCode(error);
      if (recoverRead && attempt === 1 && code !== null) continue;
      // Error/cause/URL/header는 기록하지 않는다. 고정 allowlist category만 남긴다.
      console.warn("weather_proxy_transport_failure", { category: code ?? "unclassified", attempt });
      cleanup();
      return unavailable(503);
    }
  }
  cleanup();
  return unavailable(503);
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
