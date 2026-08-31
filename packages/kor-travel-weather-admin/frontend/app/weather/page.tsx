"use client";

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
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
        actions={<span className="status on">{locations.length || "—"} active locations</span>}
        description="kor-travel-map의 feature 조회 흐름처럼 지도에서 위치를 고르고 최신 관측·예보를 확인합니다."
        section="날씨 탐색"
        title="날씨 지도"
      />
      {loading ? <div className="loading-banner" role="status" aria-live="polite" aria-busy="true">위치 카탈로그를 불러오는 중…</div> : null}
      {error ? <div className="weather-notice" role="alert"><span>{error}</span><button className="secondary" onClick={loadLocations} type="button">다시 시도</button></div> : null}
      {!loading && !error && locations.length ? <WeatherMap locations={locations} /> : null}
      {!loading && !error && !locations.length ? <div className="empty" role="status">활성화된 위치가 없습니다.</div> : null}
    </>
  );
}
