/** 지도 화면 범위(경위도). 한국 범위라 날짜변경선을 넘지 않는다. */
export type MapViewport = {
  west: number;
  east: number;
  south: number;
  north: number;
};

/** MapLibre의 zoom 0 세계 크기(px). raster 타일 크기와 무관하게 transform은 512 기준이다. */
const WORLD_TILE_SIZE = 512;
const MAX_MERCATOR_LATITUDE = 85.051129;

/**
 * 지도 `load` 전에 첫 카메라(`center`·`zoom`)와 지도 컨테이너 크기로 화면 범위를 계산한다(호출 검토 B7).
 *
 * MapLibre는 스타일·타일을 받은 뒤에야 `load`를 알리고 그 전에는 `getBounds()`를 쓸 수 없다.
 * 마커 요청은 이 추정 범위로 먼저 시작하고, `load` 뒤 실제 범위가 오면 그것으로 바꾼다. Web
 * Mercator 식은 MapLibre transform과 같고(회전·기울기 없음), 크기가 0이면 계산하지 않는다.
 */
export function estimateViewport(
  center: readonly [number, number],
  zoom: number,
  width: number,
  height: number,
): MapViewport | null {
  if (!(width > 0 && height > 0) || !Number.isFinite(zoom)) return null;
  const worldSize = WORLD_TILE_SIZE * 2 ** zoom;
  const latitude = Math.max(-MAX_MERCATOR_LATITUDE, Math.min(MAX_MERCATOR_LATITUDE, center[1]));
  const sin = Math.sin((latitude * Math.PI) / 180);
  const x = ((center[0] + 180) / 360) * worldSize;
  const y = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * worldSize;
  const longitudeAt = (px: number) => (px / worldSize) * 360 - 180;
  const latitudeAt = (py: number) =>
    (180 / Math.PI) * Math.atan(Math.sinh(Math.PI - (2 * Math.PI * py) / worldSize));
  return {
    west: longitudeAt(x - width / 2),
    east: longitudeAt(x + width / 2),
    north: latitudeAt(y - height / 2),
    south: latitudeAt(y + height / 2),
  };
}

export function viewportCenter(viewport: MapViewport | null): [number, number] | null {
  return viewport
    ? [(viewport.west + viewport.east) / 2, (viewport.south + viewport.north) / 2]
    : null;
}
