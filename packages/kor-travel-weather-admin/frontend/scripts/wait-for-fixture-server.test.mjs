import { createServer } from "node:http";
import { once } from "node:events";
import { afterEach, expect, test, vi } from "vitest";
import { waitForFixtureServer } from "./wait-for-fixture-server.mjs";

const runningChild = () => ({ exitCode: null, signalCode: null });
const servers = [];
afterEach(async () => {
  await Promise.all(servers.splice(0).map(async (server) => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }));
});

test("503 이후 200을 기다리고 각 준비 확인 응답의 body를 닫는다", async () => {
  const cold = new Response("warming", { status: 503 });
  const ready = new Response("ready");
  const coldCancel = vi.spyOn(cold.body, "cancel");
  const readyCancel = vi.spyOn(ready.body, "cancel");
  const fetchResponse = vi.fn().mockResolvedValueOnce(cold).mockResolvedValueOnce(ready);
  await waitForFixtureServer({
    child: runningChild(), url: "http://127.0.0.1", fetchResponse, intervalMs: 1,
  });
  expect(fetchResponse).toHaveBeenCalledTimes(2);
  expect(coldCancel).toHaveBeenCalledOnce();
  expect(readyCancel).toHaveBeenCalledOnce();
});

test("exitCode가 null이어도 시그널로 종료된 자식은 즉시 실패한다", async () => {
  const fetchResponse = vi.fn().mockResolvedValue(new Response("not ready", { status: 503 }));
  await expect(waitForFixtureServer({
    child: { exitCode: null, signalCode: "SIGTERM" },
    url: "http://127.0.0.1", fetchResponse, timeoutMs: 20, intervalMs: 1,
  })).rejects.toThrow("signal=SIGTERM");
  expect(fetchResponse).not.toHaveBeenCalled();
});

test("준비되지 않는 HTTP 응답은 성공으로 취급하지 않고 상태를 남긴다", async () => {
  await expect(waitForFixtureServer({
    child: runningChild(), url: "http://127.0.0.1",
    fetchResponse: async () => new Response("unavailable", { status: 503 }),
    timeoutMs: 20, intervalMs: 1,
  })).rejects.toThrow("시간 초과: HTTP 503");
});

test("응답 헤더가 끝내 오지 않는 실제 loopback 연결도 기동 예산 안에서 중단한다", async () => {
  const server = createServer(() => {});
  servers.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  await expect(waitForFixtureServer({
    child: runningChild(),
    url: "http://127.0.0.1:" + server.address().port,
    timeoutMs: 30, intervalMs: 1,
  })).rejects.toThrow("준비 시간 초과");
}, 1000);
