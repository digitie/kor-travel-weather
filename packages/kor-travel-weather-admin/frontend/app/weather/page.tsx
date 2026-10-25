"use client";

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { InfoButton } from "@/components/info-button";
import { WeatherMap } from "@/components/weather-map";
import { getAllPublicLocations, Location } from "@/lib/api";

export default function WeatherPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadLocations = useCallback(() => {
    setLoading(true);
    setError(null);
    getAllPublicLocations()
      .then((result) => {
        setLocations(result);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "위치를 불러오지 못했습니다."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadLocations();
  }, [loadLocations]);

  return (
    <>
      <PageHeader
        compact
        info={<InfoButton label="날씨 지도"><p>위치를 선택하면 최신 관측값과 예보를 확인할 수 있습니다. 종류와 데이터 출처로 표시할 자료를 좁힐 수 있습니다.</p><p>지도 건수는 현재 범위의 여백과 선택 위치까지 포함해 불러온 위치 수입니다. 목록 건수는 종류·출처·검색 조건에 맞는 위치 수이며 묶음 마커 수와 다릅니다.</p>{!loading && !error ? <p>활성 위치 카탈로그 {locations.length}곳</p> : null}</InfoButton>}
        section="날씨 탐색"
        title="날씨 지도"
      />
      <WeatherMap locations={locations} catalogLoading={loading} catalogError={error} onRetryCatalog={loadLocations} />
    </>
  );
}
