import { NextRequest } from "next/server";
import { parseBoundary, regionLookup, type BoundaryLevel, type WeatherBoundary } from "@/lib/weather-boundary";

const headers = { "cache-control": "no-store, private" };
// Geo의 원 경계는 Geo DB에 저장되어 있다. 이 작은 BFF 캐시는 원본/새 DB를 복제하지 않는다.
const cache = new Map<string, { expires: number; value: WeatherBoundary; bytes: number }>();
const MAX_BYTES = 16 * 1024 * 1024;
let bytes = 0;
let active = 0;

async function boundedJson(response: Response) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("empty");
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > 4 * 1024 * 1024) throw new Error("large");
      chunks.push(next.value);
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const result = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder().decode(result));
}

// react-doctor-disable-next-line react-doctor/nextjs-no-side-effect-in-get-handler -- Geo의 POST search/reverse는 읽기 전용이며 Map/Uint8Array 갱신은 상한 있는 로컬 메모리 캐시·복사다. 원천/도메인 DB write는 없다.
export async function GET(request: NextRequest) {
  // 본 경로도 기존 session-only middleware와 durable revocation 검사를 거친다.
  const params = request.nextUrl.searchParams;
  const latitude = Number(params.get("lat")), longitude = Number(params.get("lon"));
  const level = params.get("level") as BoundaryLevel;
  if (!params.has("lat") || !params.has("lon") || !Number.isFinite(latitude) || !Number.isFinite(longitude)
    || latitude <= 32 || latitude >= 39 || longitude <= 123 || longitude >= 132
    || !["sido", "sigungu", "emd", "li"].includes(level)) {
    return Response.json({ detail: "경계 조회 위치를 확인해 주세요." }, { status: 400, headers });
  }
  const endpoint = process.env.WEATHER_GEO_INTERNAL_URL;
  const apiKey = process.env.WEATHER_GEO_API_KEY;
  if (!endpoint || !apiKey) return Response.json({ data: null, detail: "행정 경계 연결이 설정되지 않았습니다." }, { status: 503, headers });
  const cacheKey = JSON.stringify([endpoint, latitude, longitude, level]);
  const previous = cache.get(cacheKey);
  if (previous && previous.expires > Date.now()) return Response.json({ data: previous.value }, { headers });
  if (active >= 4) return Response.json({ data: null, detail: "행정 경계 조회가 많습니다. 잠시 후 다시 선택해 주세요." }, { status: 503, headers: { ...headers, "retry-after": "1" } });
  active += 1;
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(8_000)]);
  const authentication = { "X-KTG-API-Key": apiKey };
  async function geo(path: string, body: Record<string, unknown>) {
    const url = new URL(path, endpoint);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("configuration");
    const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json", ...authentication },
      body: JSON.stringify(body), cache: "no-store", redirect: "error", signal });
    if (!response.ok) throw new Error("upstream");
    return boundedJson(response);
  }
  try {
    const reverse = await geo("/v2/reverse", { lon: longitude, lat: latitude, include_region: true, include_zipcode: false });
    const lookup = regionLookup(reverse, level);
    if (!lookup) return Response.json({ data: null, detail: "이 위치의 행정 경계를 확인하지 못했습니다." }, { headers });
    const result = await geo("/v2/search", { query: lookup.query, type: "district", include_geometry: true, size: 10, ...lookup.hint });
    const value = parseBoundary(result, level, lookup.code);
    if (!value) return Response.json({ data: null, detail: "해당 단계의 실제 경계 자료가 없습니다." }, { headers });
    const size = new TextEncoder().encode(JSON.stringify(value)).byteLength;
    // upstream 대기 중 같은 키가 갱신됐을 수 있어 저장 직전의 항목만 차감한다.
    const current = cache.get(cacheKey);
    if (current) { bytes -= current.bytes; cache.delete(cacheKey); }
    while (cache.size && (cache.size >= 128 || bytes + size > MAX_BYTES)) {
      const oldest = cache.entries().next();
      if (oldest.done) break;
      const [key, entry] = oldest.value;
      bytes -= entry.bytes; cache.delete(key);
    }
    cache.set(cacheKey, { expires: Date.now() + 6 * 60 * 60 * 1000, value, bytes: size }); bytes += size;
    return Response.json({ data: value }, { headers });
  } catch {
    // URL·키·오류 cause·원 응답을 로그/브라우저에 노출하지 않는다.
    return Response.json({ data: null, detail: "행정 경계 서버에 연결하지 못했습니다." }, { status: 502, headers });
  } finally { active -= 1; }
}
