import { NextRequest, NextResponse } from "next/server";

const dagsterBase = () => process.env.DAGSTER_UI_INTERNAL_URL ?? "http://127.0.0.1:14102";

export async function POST(request: NextRequest) {
  try {
    const body = await request.text();
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
