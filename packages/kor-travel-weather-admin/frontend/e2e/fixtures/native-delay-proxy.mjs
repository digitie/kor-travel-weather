// 로컬 native retry E2E 전용. 운영 URL을 받지 않는다.
import { createServer } from "node:http";
import { setTimeout as delay } from "node:timers/promises";

let armed = false;
let completed = 0;
createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/state") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ armed, completed }));
    return;
  }
  if (request.method !== "POST") { response.writeHead(405); response.end(); return; }
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (request.url === "/arm") {
    armed = true; completed = 0;
    response.writeHead(200, { "content-type": "application/json" }); response.end("{}");
    return;
  }
  try {
    const body = Buffer.concat(chunks).toString("utf8");
    const pause = armed && JSON.parse(body).query?.includes("LaunchAddressRefresh");
    if (pause) { armed = false; await delay(12_000); }
    const upstream = await fetch("http://127.0.0.1:24102/graphql", {
      method: "POST", headers: { "content-type": "application/json" }, body,
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await upstream.text();
    if (pause) completed += 1;
    if (!response.destroyed) {
      response.writeHead(upstream.status, { "content-type": "application/json" });
      response.end(payload);
    }
  } catch {
    if (!response.destroyed) { response.writeHead(502); response.end("{}"); }
  }
}).listen(24602, "127.0.0.1");
