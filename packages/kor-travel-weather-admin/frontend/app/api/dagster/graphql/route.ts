import { NextRequest, NextResponse } from "next/server";

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
    const response = await fetch(`${dagsterBase().replace(/\/$/, "")}/graphql`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body,
      cache: "no-store",
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
