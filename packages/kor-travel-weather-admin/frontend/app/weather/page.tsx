"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { WeatherMap } from "@/components/weather-map";
import { getAllPublicLocations, getPublicRegionLocations, Location, type WeatherRegionProjection } from "@/lib/api";

export default function WeatherPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regionProjection, setRegionProjection] = useState<WeatherRegionProjection | null>(null);
  const catalogRequest = useRef<AbortController | null>(null);

  const loadLocations = useCallback(() => {
    catalogRequest.current?.abort();
    const request = new AbortController();
    catalogRequest.current = request;
    setLoading(true);
    setError(null);
    setRegionProjection(null);
    // 전체 목록은 병행해서 유지한다. 대표 참조의 실패·미준비는 전체 조회를 막지 않는다.
    void getPublicRegionLocations(AbortSignal.any([request.signal, AbortSignal.timeout(10_000)]))
      .then(({ data }) => { if (!request.signal.aborted && data.ready) setRegionProjection(data); })
      .catch(() => { /* 기존 전체 카탈로그 경로로 진행한다. */ });
    getAllPublicLocations(undefined, request.signal)
      .then((result) => {
        if (!request.signal.aborted) setLocations(result);
      })
      .catch((reason: unknown) => {
        if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "위치를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    loadLocations();
    return () => catalogRequest.current?.abort();
  }, [loadLocations]);

  // 메타 줄은 로딩·실패 중에도 한 줄을 유지해 카탈로그 응답 전후 작업면이 밀리지 않게 한다.
  return (
    <AdminShell
      compact
      fillViewport
      help={
        <>
          <p>위치를 선택하면 최신 관측값과 예보를 확인할 수 있습니다. 종류와 데이터 출처로 표시할 자료를 좁힐 수 있습니다.</p>
          <p>지도 건수는 현재 범위의 여백과 선택 위치까지 포함해 불러온 위치 수입니다. 목록 건수는 종류·출처·검색 조건에 맞는 위치 수이며 묶음 마커 수와 다릅니다.</p>
        </>
      }
      helpVariant="info"
      meta={loading ? "활성 위치 카탈로그 확인 중" : error ? "활성 위치 카탈로그 확인 실패" : `활성 위치 카탈로그 ${locations.length}곳`}
      section="날씨 탐색"
      title="날씨 지도"
    >
      <WeatherMap locations={locations} regionProjection={regionProjection} catalogLoading={loading} catalogError={error} onRetryCatalog={loadLocations} />
    </AdminShell>
  );
}
