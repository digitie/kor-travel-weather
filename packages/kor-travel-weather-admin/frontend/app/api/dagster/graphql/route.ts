import { NextRequest, NextResponse } from "next/server";

import { scopedDagsterRequest } from "@/lib/dagster-scope";

const dagsterBase = () => process.env.DAGSTER_UI_INTERNAL_URL ?? "http://127.0.0.1:14102";
const MAX_BODY_BYTES = 1 * 1024 * 1024;

async function readBoundedText(request: NextRequest): Promise<string | null> {
  const reader = request.body?.getReader();
  if (!reader) {
    const text = await request.text();
    return new TextEncoder().encode(text).byteLength <= MAX_BODY_BYTES ? text : null;
  }
  const decoder = new TextDecoder();
  let total = 0;
  let text = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
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
    return new NextResponse(response.body, {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store, private",
      },
    });
  } catch (reason: unknown) {
    return NextResponse.json({ errors: [{ message: reason instanceof Error ? reason.message : "Dagster 연결 실패" }] }, { status: 502 });
  }
}
