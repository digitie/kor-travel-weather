"use client";

import { AdminShell } from "@/components/admin-shell";
import { WeatherMap } from "@/components/weather-map";
import type { Location } from "@/lib/api";
import { useLocationCatalog, useRegionProjection } from "@/lib/location-catalog";

const NO_LOCATIONS: Location[] = [];

export default function WeatherPage() {
  // 전체 목록과 대표 참조는 병행한다. 대표 참조의 실패·미준비는 전체 조회를 막지 않는다.
  // 둘 다 화면 사이 캐시(TanStack Query)라 다시 들어와도 바로 그리고, 오래됐으면 ETag로 다시 확인한다.
  const catalog = useLocationCatalog({});
  const regions = useRegionProjection();
  const locations = catalog.data?.locations ?? NO_LOCATIONS;
  const loading = catalog.isPending;
  // 이미 받은 목록이 있으면 뒤 재확인 실패는 목록을 지우지 않는다.
  const error = catalog.isError && !catalog.data
    ? catalog.error instanceof Error ? catalog.error.message : "위치를 불러오지 못했습니다."
    : null;
  const retry = () => {
    void catalog.refetch();
    void regions.refetch();
  };

  // 상단 바 설명은 로딩·실패 중에도 한 줄을 유지한다(한 줄 상단 바라 작업면 높이가 바뀌지 않는다).
  return (
    <AdminShell
      description={loading ? "활성 위치 카탈로그 확인 중" : error ? "활성 위치 카탈로그 확인 실패" : `활성 위치 카탈로그 ${locations.length}곳`}
      fillViewport
      help={
        <>
          <p>위치를 선택하면 최신 관측값과 예보를 확인할 수 있습니다. 종류와 데이터 출처로 표시할 자료를 좁힐 수 있습니다.</p>
          <p>지도 건수는 현재 범위의 여백과 선택 위치까지 포함해 불러온 위치 수입니다. 목록 건수는 종류·출처·검색 조건에 맞는 위치 수이며 묶음 마커 수와 다릅니다.</p>
        </>
      }
      helpVariant="info"
      title="날씨 지도"
    >
      <WeatherMap locations={locations} regionProjection={regions.data ?? null} catalogLoading={loading} catalogError={error} onRetryCatalog={retry} />
    </AdminShell>
  );
}
