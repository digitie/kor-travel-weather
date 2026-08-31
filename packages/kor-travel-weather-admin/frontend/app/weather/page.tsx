"use client";

import { useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { WeatherMap } from "@/components/weather-map";
import { getAllPublicLocations, Location } from "@/lib/api";

export default function WeatherPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [message, setMessage] = useState("위치 카탈로그를 불러오는 중…");

  useEffect(() => {
    getAllPublicLocations()
      .then((result) => {
        setLocations(result);
        setMessage(result.length ? "" : "활성화된 위치가 없습니다.");
      })
      .catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "위치를 불러오지 못했습니다."));
  }, []);

  return (
    <>
      <PageHeader
        actions={<span className="status on">{locations.length || "—"} active locations</span>}
        description="kor-travel-map의 feature 조회 흐름처럼 지도에서 위치를 고르고 최신 관측·예보를 확인합니다."
        section="Weather"
        title="날씨 지도"
      />
      {message ? <div className={message.includes("못") ? "error" : "loading-banner"}>{message}</div> : null}
      {locations.length ? <WeatherMap locations={locations} /> : null}
    </>
  );
}
