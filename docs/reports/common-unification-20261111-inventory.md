# 2026-11-11 Common UI 검토

이 목록은 최초 인계 전의 변경 전 사용처 기록이다. 현재 소비 경로와 검증 결과는
[Weather 전담 전환 기록](common-unification-20261111-weather.md)을 따른다.


## table

```text
packages/kor-travel-weather-admin/frontend/components/location-admin.tsx:160:          <table>
packages/kor-travel-weather-admin/frontend/components/address-refresh.tsx:83:        <table className={styles.table}>
packages/kor-travel-weather-admin/frontend/components/weather-details.tsx:116:        <table style={{width: 76 + slots.length * 64}}><caption className="sr-only">한국 시간 기준 기온 그래프와 강수·습도·바람 예보</caption>
packages/kor-travel-weather-admin/frontend/app/sync-runs/page.tsx:90:                  <table>
packages/kor-travel-weather-admin/frontend/app/sync-runs/page.tsx:169:          <table>
```

## overlay

```text
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:16:import { createPortal } from "react-dom";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:345:  return element ? createPortal(children, element) : null;
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:3:import { Popover } from "@base-ui/react/popover";
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:10:    <Popover.Root>
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:11:      <Popover.Trigger className="info-button" aria-label={`${label} 안내`}>
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:14:      <Popover.Portal>
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:15:        <Popover.Positioner sideOffset={6} collisionPadding={12} className="info-positioner">
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:16:          <Popover.Popup className="info-popup">
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:18:              <Popover.Title>{label}</Popover.Title>
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:19:              <Popover.Close aria-label="안내 닫기" className="info-button"><X size={15} aria-hidden="true" /></Popover.Close>
packages/kor-travel-weather-admin/frontend/components/info-button.tsx:21:            <Popover.Description render={<div />}>{children}</Popover.Description>
```

## map

```text
packages/kor-travel-weather-admin/frontend/components/weather-map.test.ts:148:  const source = readFileSync(new URL("./vworld-map-view.tsx", import.meta.url), "utf8");
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:3:// maplibre-gl v6 is ESM-only and dropped its default export; a namespace
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:4:// import keeps every `maplibregl.X` reference below working as both a value
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:5:// (`new maplibregl.Map(...)`, `new maplibregl.Marker(...)`) and a type
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:6:// (`maplibregl.ErrorEvent`, `maplibregl.IControl`), which named imports alone
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:8:import * as maplibregl from "maplibre-gl";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:15:} from "maplibre-gl";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:26:} from "@/lib/vworld-style";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:36:import "maplibre-gl/dist/maplibre-gl.css";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:38:// Next.js's asset handling hashes the worker without its `maplibre-gl-shared.mjs`
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:40:// tiles. `scripts/copy-maplibre-worker.mjs` (predev/prebuild) copies both files
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:41:// to public/maplibre; this points the client at them. Must run before the first
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:42:// `new maplibregl.Map(...)`, which is why it sits at module scope in the one file
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:44:maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:47: * React/MapLibre boundary modelled on digitie's maplibre-vworld-react
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:48: * `vworld-map-web` package.  The app keeps the adapter local because that
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:66:  onError?: (event: maplibregl.ErrorEvent) => void;
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:125:      instance = new maplibregl.Map({
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:136:      onErrorRef.current?.({ type: "error", error } as maplibregl.ErrorEvent);
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:147:    const failedTiles = new Map<string, maplibregl.ErrorEvent>();
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:153:    const handleError = (event: maplibregl.ErrorEvent) => {
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:154:      const key = rasterTileKey(event as maplibregl.ErrorEvent & RasterTileEvent);
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:167:    const handleSourceData = (event: maplibregl.MapSourceDataEvent) => {
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:190:    const controls: maplibregl.IControl[] = [];
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:192:      const control = new maplibregl.NavigationControl({ showCompass: false });
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:197:      const control = new maplibregl.ScaleControl({ maxWidth: 150, unit: "metric" });
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:250:        data-vworld-basemap={configured ? layerType : "fallback"}
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:251:        data-vworld-key={configured ? "configured" : "missing"}
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:272:/** React portal marker primitive matching maplibre-vworld-react's Marker. */
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:300:    const marker = new maplibregl.Marker({ element, anchor, offset })
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:358:    <span aria-hidden="true" className="vworld-weather-marker-icon">{weatherConditionIcon(condition)}</span>
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:359:    <span className="vworld-weather-marker-copy">{regionLabel ? <small>{regionLabel}</small> : null}
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:368: * Weather marker from maplibre-vworld-react's WeatherMarker model, adapted to
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:388:      className={`weather-marker vworld-weather-marker weather-marker-${condition}${regionKey ? " weather-region-marker" : ""}${markerProps.selected ? " selected" : ""}`}
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:394:        className="vworld-weather-marker-button"
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:413:  element.className = "vworld-weather-cluster";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:434:  element.className = `weather-marker vworld-weather-marker weather-marker-${marker.condition}${marker.selected ? " selected" : ""}`;
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:437:  button.className = "vworld-weather-marker-button";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:442:  icon.className = "vworld-weather-marker-icon";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:446:  copy.className = "vworld-weather-marker-copy";
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:508:    const source = map.getSource(WEATHER_CLUSTER_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:517:    const markerPool = new Map<string, maplibregl.Marker>();
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:583:              const clusterSource = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:591:            const nextMarker = new maplibregl.Marker({ element }).setLngLat(coordinates);
packages/kor-travel-weather-admin/frontend/components/vworld-map-view.tsx:615:          const nextMarker = new maplibregl.Marker({ element }).setLngLat(coordinates);
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:74:    expect(await page.locator('[role="application"]').getAttribute("data-vworld-key")).toBe("missing");
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:76:      await expect(page.locator(".maplibregl-ctrl-zoom-in")).toHaveCSS("width", "44px");
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:77:      await expect(page.locator(".maplibregl-ctrl-zoom-in")).toHaveCSS("height", "44px");
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:103:    const canvas = await page.locator(".maplibregl-canvas").boundingBox();
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:238:    const canvas = await page.locator(".maplibregl-canvas").elementHandle();
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:257:    const detailMetrics = await detail.evaluate((element) => Object.fromEntries(Object.entries({ detail: element, header: element.querySelector('[data-slot="map-detail-header"]')!, body: element.querySelector('[data-slot="map-detail-body"]')!, close: element.querySelector('button[aria-label="상세 닫기"]')!, freshness: element.querySelector(".weather-current-status")!, alert: element.querySelector(".alert-panel")!, temperature: element.querySelector(".metric-card.primary")!, zoomIn: document.querySelector(".maplibregl-ctrl-zoom-in")!, zoomOut: document.querySelector(".maplibregl-ctrl-zoom-out")! }).map(([name, node]) => {
packages/kor-travel-weather-admin/frontend/e2e/map-workspace-ui.spec.ts:303:    await page.route("**/req/wmts/1.0.0/layout-tile-fixture-only/**", (route) => route.fulfill({ contentType: "image/png", body: readFileSync("e2e/fixtures/vworld-tile.png") }));
packages/kor-travel-weather-admin/frontend/components/weather-map.tsx:4:import type { Map as MapLibreMap } from "maplibre-gl";
packages/kor-travel-weather-admin/frontend/components/weather-map.tsx:27:} from "@/components/vworld-map-view";
packages/kor-travel-weather-admin/frontend/components/weather-map.tsx:444:        const button = host.querySelector<HTMLElement>(".vworld-weather-marker-button");
packages/kor-travel-weather-admin/frontend/components/weather-map.tsx:453:      const controls = root.closest(".weather-region-stage")?.querySelectorAll<HTMLElement>(".kt-map-detail, .kt-map-stage-summary, .weather-region-access > button, .maplibregl-ctrl-top-right, .maplibregl-ctrl-bottom-right") ?? [];
packages/kor-travel-weather-admin/frontend/components/weather-map.tsx:800:                {markers.map(marker => <button key={marker.id} type="button" className={`vworld-weather-marker-button vworld-weather-marker weather-marker-${marker.condition}`} aria-pressed={marker.selected} onClick={marker.onClick}><WeatherMarkerLabel {...marker} /><span className="sr-only">{marker.title} 날씨 보기</span></button>)}
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:5:const tileImage = readFileSync("e2e/fixtures/vworld-tile.png");
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:49:  await expect(page.locator('[role="application"]')).toHaveAttribute("data-vworld-key", "configured");
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:55:    await page.locator(".maplibregl-ctrl-zoom-in").click();
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:62:      const canvas = (await page.locator(".maplibregl-canvas").boundingBox())!;
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:74:      await page.locator(".maplibregl-ctrl-zoom-in").click();
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:77:      await page.locator(".maplibregl-ctrl-zoom-out").click();
packages/kor-travel-weather-admin/frontend/e2e/map-tile-recovery.spec.ts:79:      await page.locator(".maplibregl-ctrl-zoom-in").click();
packages/kor-travel-weather-admin/frontend/package.json:6:    "predev": "node ./scripts/copy-maplibre-worker.mjs",
packages/kor-travel-weather-admin/frontend/package.json:8:    "prebuild": "node ./scripts/copy-maplibre-worker.mjs",
packages/kor-travel-weather-admin/frontend/package.json:22:    "maplibre-gl": "6.17.0",
packages/kor-travel-weather-admin/frontend/lib/vworld-style.test.ts:9:} from "./vworld-style";
packages/kor-travel-weather-admin/frontend/lib/vworld-style.test.ts:21:      "vworld-satellite-layer",
packages/kor-travel-weather-admin/frontend/lib/vworld-style.test.ts:22:      "vworld-base-layer",
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:1:import type { StyleSpecification } from "maplibre-gl";
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:3:// The URL/style boundary follows digitie's maplibre-vworld-react repository
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:4:// (vworld-map-core + vworld-map-web).  Keeping this small web adapter local
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:27: * Build the VWorld WMTS URL used by maplibre-vworld-react's web adapter.
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:33:  return `https://api.vworld.kr/req/wmts/1.0.0/${encodeURIComponent(apiKey.trim())}/${LAYER_NAME[layerType]}/{z}/{y}/{x}.${extension}`;
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:50:        id: "vworld-key-missing",
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:70:  if (layerType === "Hybrid") addRaster("vworld-satellite", "vworld-satellite-layer", "Satellite");
packages/kor-travel-weather-admin/frontend/lib/vworld-style.ts:71:  addRaster("vworld-base", "vworld-base-layer", layerType);
```
