import { setTimeout as delay } from "node:timers/promises";

// 서버가 응답하지 않아도 전체 기동 예산 안에서 끝내고, HTTP/시그널 원인을 남긴다.
export async function waitForFixtureServer({
  child,
  url,
  timeoutMs = 20_000,
  intervalMs = 200,
  fetchResponse = fetch,
}) {
  const deadline = performance.now() + timeoutMs;
  let lastObservation = "아직 응답 없음";
  while (performance.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(
        "로컬 production 서버 기동 실패: exit=" + child.exitCode +
        ", signal=" + child.signalCode,
      );
    }
    try {
      const remaining = Math.max(1, Math.ceil(deadline - performance.now()));
      const response = await fetchResponse(url, {
        signal: AbortSignal.timeout(Math.min(5000, remaining)),
      });
      lastObservation = "HTTP " + response.status;
      // 준비 확인 HTML을 사용하지 않으므로 연결을 즉시 반환한다.
      await response.body?.cancel();
      if (response.ok) return;
    } catch (error) {
      lastObservation = error.cause?.code ?? error.name;
    }
    const remaining = deadline - performance.now();
    if (remaining > 0) await delay(Math.min(intervalMs, remaining));
  }
  throw new Error("로컬 production 서버 준비 시간 초과: " + lastObservation);
}
