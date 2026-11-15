import { NextRequest, NextResponse } from "next/server";

import { scopedDagsterRequest } from "@/lib/dagster-scope";
import { sanitizeDagsterOverview, sanitizeFailureResponse } from "@/lib/dagster-failure-diagnostics";

const dagsterBase = () => process.env.DAGSTER_UI_INTERNAL_URL ?? "http://127.0.0.1:14102";
const MAX_BODY_BYTES = 1 * 1024 * 1024;

async function readBoundedText(request: Pick<Request, "body" | "text">, maximumBytes = MAX_BODY_BYTES): Promise<string | null> {
  const reader = request.body?.getReader();
  if (!reader) {
    const text = await request.text();
    return new TextEncoder().encode(text).byteLength <= maximumBytes ? text : null;
  }
  const decoder = new TextDecoder();
  let total = 0;
  let text = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    total += value.byteLength;
    if (total > maximumBytes) {
      await reader.cancel();
      return null;
    }
    text += decoder.decode(value, { stream: true });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await readBoundedText(request);
    if (body === null) {
      return NextResponse.json(
        { errors: [{ message: "Dagster 요청이 너무 큽니다." }] },
        { status: 413, headers: { "cache-control": "no-store, private" } },
      );
    }
    // Only the named operations in lib/dagster-scope.ts go through, with the
    // query text and this project's code-location scope supplied here. A raw
    // GraphQL document from the browser is refused: once the webserver behind
    // this proxy is shared, forwarding it would expose -- and let a mutation
    // act on -- other projects' runs and schedules.
    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      parsed = null;
    }
    const scoped = scopedDagsterRequest(parsed);
    if (!scoped.ok) {
      return NextResponse.json(
        { errors: [{ message: scoped.message }] },
        { status: 400, headers: { "cache-control": "no-store, private" } },
      );
    }
    const response = await fetch(`${dagsterBase().replace(/\/$/, "")}/graphql`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: scoped.body,
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if ((parsed as { operationName?: string } | null)?.operationName === "WeatherDagsterRunFailure") {
      const result = await readBoundedText(response, 4_194_304);
      if (result === null) throw new Error("실행 상세 응답 크기 상한 초과");
      return NextResponse.json(sanitizeFailureResponse(JSON.parse(result)), {
        status: response.status, headers: { "cache-control": "no-store, private" },
      });
    }
    const result = await readBoundedText(response,4_194_304);
    if(result===null)throw new Error("개요 응답 크기 상한 초과");
    return NextResponse.json(sanitizeDagsterOverview(JSON.parse(result)), {
      status:response.status,headers:{"cache-control":"no-store, private"},
    });
  } catch {
    return NextResponse.json({ errors: [{ message: "Dagster 연결 실패" }] }, { status: 502 });
  }
}
