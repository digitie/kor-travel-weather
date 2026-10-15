import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "./route";

const context = (path = ["v1", "weather", "locations"]) => ({ params: Promise.resolve({ path }) });
const request = (method = "GET", signal?: AbortSignal) => new NextRequest(
  "http://localhost/api/weather/v1/weather/locations?limit=1000&location_id=a&location_id=b",
  { method, signal },
);
const socketFailure = () => Object.assign(new TypeError("fetch failed: private detail"), {
  cause: { code: "UND_ERR_SOCKET", message: "private socket address" },
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("weather BFF 위치 GET 전송 복구", () => {
  it("헤더 전 socket 종료만 한 번 재시도하고 원래 query를 보존한다", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(socketFailure())
      .mockResolvedValueOnce(Response.json({ data: ["location"] }));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(request(), context());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ data: ["location"] });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(String(fetcher.mock.calls[0][0])).toContain("limit=1000&location_id=a&location_id=b");
    expect(String(fetcher.mock.calls[1][0])).toBe(String(fetcher.mock.calls[0][0]));
    expect(fetcher.mock.calls[0][1].cache).toBe("no-store");
  });

  it("두 번 모두 실패하면 비밀 없는503으로 끝난다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(socketFailure()));
    const response = await GET(request(), context());
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body = await response.text();
    expect(body).not.toContain("private");
    expect(body).not.toContain("UND_ERR_SOCKET");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each([400, 401, 403, 429, 500, 503])("원천 HTTP%s는 그대로 전달하고 재시도하지 않는다", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("original", { status })));
    const response = await GET(request(), context());
    expect(response.status).toBe(status);
    expect(await response.text()).toBe("original");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("알 수 없는 TypeError는 재시도하지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("invalid private detail")));
    expect((await GET(request(), context())).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["POST", "PATCH", "PUT", "DELETE", "HEAD"])("%s 전송 실패는 자동 재전송하지 않는다", async (method) => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(socketFailure()));
    expect((await POST(request(method), context(["v1", "admin", "locations"]))).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("다른 GET은 새 재시도 정책에 포함하지 않는다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(socketFailure()));
    expect((await GET(request(), context(["v1", "weather", "locations", "a", "latest"]))).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("이미 취소된 요청은 upstream을 호출하지 않는다", async () => {
    const client = new AbortController();
    client.abort();
    vi.stubGlobal("fetch", vi.fn());
    expect((await GET(request("GET", client.signal), context())).status).toBe(499);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("첫 실패와 동시에 취소되면 두 번째 요청을 만들지 않는다", async () => {
    const client = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
      client.abort();
      throw socketFailure();
    }));
    expect((await GET(request("GET", client.signal), context())).status).toBe(499);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("headers 대기 중 caller 취소는 즉시 전파하고 재시도하지 않는다", async () => {
    vi.useFakeTimers();
    const client = new AbortController();
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_url, init) => {
      signal = init.signal;
      return new Promise((_, reject) => {
        init.signal.addEventListener("abort", () => reject(socketFailure()), { once: true });
      });
    }));
    const pending = GET(request("GET", client.signal), context());
    await vi.advanceTimersByTimeAsync(1);
    client.abort();
    expect((await pending).status).toBe(499);
    expect(signal?.aborted).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("재시도해도 pre-header 총20초 예산을 초기화하지 않는다", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => {
      setTimeout(() => reject(socketFailure()), 12_000);
    })).mockImplementationOnce((_url, init) => new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    }));
    vi.stubGlobal("fetch", fetcher);
    let settled = false;
    const pending = GET(request(), context()).then((response) => { settled = true; return response; });
    await vi.advanceTimersByTimeAsync(19_999);
    expect(settled).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect((await pending).status).toBe(504);
    expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("timer callback이 지연돼도 headers 도착 시 monotonic 총예산을 재검증한다", async () => {
    let clock = 0;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    const cancelled = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
      clock = 20_001;
      return new Response(new ReadableStream({ cancel: cancelled }));
    }));
    expect((await GET(request(), context())).status).toBe(504);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(cancelled).toHaveBeenCalledTimes(1);
  });

  it("다른 GET과 write에는20초 상한을 새로 만들지 않는다", async () => {
    vi.useFakeTimers();
    for (const method of ["GET", "POST"]) {
      vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise((resolve) => {
        setTimeout(() => resolve(new Response("slow original")), 25_000);
      })));
      const pending = GET(request(method), context(["v1", "admin", "export"]));
      await vi.advanceTimersByTimeAsync(25_000);
      const response = await pending;
      expect(response.status).toBe(200);
      expect(await response.text()).toBe("slow original");
      expect(fetch).toHaveBeenCalledTimes(1);
    }
  });

  it("헤더 뒤 body 오류는 재시도하지 않고 reader를 해제한다", async () => {
    const body = new ReadableStream({ pull(controller) { controller.error(socketFailure()); } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const response = await GET(request(), context());
    await expect(response.text()).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
  });

  it("헤더 이후에는 새20초 body 제한을 부과하지 않고 취소를 전달한다", async () => {
    vi.useFakeTimers();
    const cancelled = vi.fn();
    const body = new ReadableStream({ cancel: cancelled });
    const fetcher = vi.fn().mockResolvedValue(new Response(body));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(request(), context());
    await vi.advanceTimersByTimeAsync(25_000);
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(false);
    await response.body?.cancel();
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("응답 소비가 멈춘 뒤 caller 취소만으로 reader와 listener를 해제한다", async () => {
    const client = new AbortController();
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array([1])); },
      cancel: cancelled,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const incoming = request("GET", client.signal);
    const remove = vi.spyOn(incoming.signal, "removeEventListener");
    const response = await GET(incoming, context());
    // 한 chunk가 downstream queue를 채워 이후 pull이 없는 상태를 만든다.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(body.locked).toBe(true);
    client.abort();
    // response의 소비/cancel 없이 취소 자체가 자원을 해제해야 한다.
    await vi.waitFor(() => expect(body.locked).toBe(false), { timeout: 200 });
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(fetch).toHaveBeenCalledTimes(1);
    void response;
  });

  it("진행 중 pull과 caller 취소가 겹쳐도 reader를 해제한다", async () => {
    const client = new AbortController();
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel: cancelled });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const incoming = request("GET", client.signal);
    const remove = vi.spyOn(incoming.signal, "removeEventListener");
    const response = await GET(incoming, context());
    const pending = response.text();
    const rejected = expect(pending).rejects.toThrow("날씨 서버 응답 전송이 중단됐습니다.");
    client.abort();
    await rejected;
    await vi.waitFor(() => expect(body.locked).toBe(false));
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  it("downstream 취소와 caller 취소가 겹쳐도 한 번만 upstream을 취소한다", async () => {
    const client = new AbortController();
    let finishCancel!: () => void;
    const cancelled = vi.fn(() => new Promise<void>((resolve) => { finishCancel = resolve; }));
    const body = new ReadableStream<Uint8Array>({ cancel: cancelled });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const incoming = request("GET", client.signal);
    const remove = vi.spyOn(incoming.signal, "removeEventListener");
    const response = await GET(incoming, context());
    const pending = response.body!.cancel();
    client.abort();
    finishCancel();
    await pending;
    await vi.waitFor(() => expect(body.locked).toBe(false));
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("headers 이후 caller 취소도 upstream body를 닫는다", async () => {
    const client = new AbortController();
    const cancelled = vi.fn();
    const body = new ReadableStream({ cancel: cancelled });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const response = await GET(request("GET", client.signal), context());
    client.abort();
    await expect(response.text()).rejects.toThrow("날씨 서버 응답 전송이 중단됐습니다.");
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
