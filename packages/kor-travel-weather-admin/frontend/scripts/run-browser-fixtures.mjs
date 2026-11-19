// 로컬 production build와 메모리 fixture API만 사용한다. 운영 접속·DB는 없다.
import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { waitForFixtureServer } from "./wait-for-fixture-server.mjs";

const tileFixture = process.argv.includes("--tile-fixture");
const children = new Set();
// 실제 logout replay 검증을 위해 이 fixture 프로세스 안에서만 revocation을 기억한다.
const revokedSessions = new Set();
const backend = createServer(async (request, response) => {
  const path = new URL(request.url, "http://localhost").pathname;
  if (path.startsWith("/v1/admin/session-revocations/")) {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const { session } = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (path.endsWith("/revoke")) revokedSessions.add(session);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ revoked: revokedSessions.has(session) }));
    return;
  }
  const reply =
    path.endsWith("/success")
          ? { cleared: true }
          : path.includes("login-rate-limit")
            ? { allowed: true, retry_after: null }
            : path === "/health"
              ? { status: "ok" }
              : { data: [], meta: {} };
  response.writeHead(200, { "content-type": "application/json" });
  response.end(JSON.stringify(reply));
});
backend.listen(0, "127.0.0.1");
await once(backend, "listening");
const portProbe = createServer();
portProbe.listen(0, "127.0.0.1");
await once(portProbe, "listening");
const webPort = portProbe.address().port;
await new Promise((resolve) => portProbe.close(resolve));
const baseURL = `http://127.0.0.1:${webPort}`;
const env = {
  ...process.env,
  NEXT_TELEMETRY_DISABLED: "1",
  WEATHER_API_INTERNAL_URL: `http://127.0.0.1:${backend.address().port}`,
  WEATHER_UI_USER: "admin",
  WEATHER_UI_PASSWORD: randomBytes(24).toString("hex"),
  WEATHER_UI_SESSION_SECRET: randomBytes(32).toString("hex"),
  WEATHER_ADMIN_TOKEN: randomBytes(32).toString("hex"),
  WEATHER_UI_TRUST_PROXY: "true",
  WEATHER_UI_PUBLIC_ORIGIN: baseURL,
  E2E_BASE_URL: baseURL,
  E2E_TRUST_PROXY: "1",
};
env.E2E_UI_PASSWORD = env.WEATHER_UI_PASSWORD;
if (tileFixture) { env.NEXT_PUBLIC_VWORLD_API_KEY = "layout-tile-fixture-only"; env.E2E_TILE_FIXTURE = "1"; }
function start(args) {
  const child = spawn(process.execPath, args, {
    env,
    stdio: ["ignore", "inherit", "inherit"],
  });
  children.add(child);
  child.on("exit", () => children.delete(child));
  return child;
}
function cleanup() {
  for (const child of children) child.kill("SIGTERM");
  backend.closeAllConnections();
  backend.close();
}
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);
try {
  if (tileFixture) {
    const copy = start(["scripts/copy-maplibre-worker.mjs"]);
    if ((await once(copy, "exit"))[0] !== 0) throw new Error("지도 worker 복사 실패");
    const build = start(["node_modules/next/dist/bin/next", "build"]);
    if ((await once(build, "exit"))[0] !== 0) throw new Error("공개 fixture 지도 빌드 실패");
  }
  const web = start(
    [
      "node_modules/next/dist/bin/next",
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(webPort),
    ],
  );
  await waitForFixtureServer({ child: web, url: baseURL + "/login" });
  const runner = start([
    "node_modules/@playwright/test/cli.js",
    "test",
    ...(tileFixture ? ["map-tile-recovery.spec.ts"] : process.argv.length > 2 ? process.argv.slice(2) : ["admin-shell.spec.ts", "admin-shell-ui.spec.ts", "common-unification.spec.ts", "shared-map-ui.spec.ts", "compact-map-ui.spec.ts", "map-workspace-ui.spec.ts"]),
    "--workers=1",
  ]);
  const [code] = await once(runner, "exit");
  process.exitCode = code ?? 1;
} finally {
  cleanup();
}
