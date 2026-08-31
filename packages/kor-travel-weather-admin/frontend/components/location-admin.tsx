"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { createLocation, getLocations, Location, updateLocation } from "@/lib/api";

const PAGE_SIZE = 100;

const newLocationFields = [
  { key: "location_id", label: "위치 ID", type: "text" },
  { key: "name", label: "이름", type: "text" },
  { key: "latitude", label: "위도", type: "number", step: "any" },
  { key: "longitude", label: "경도", type: "number", step: "any" },
  { key: "nx", label: "격자 X", type: "number", step: "1" },
  { key: "ny", label: "격자 Y", type: "number", step: "1" },
] as const;

export function LocationAdmin() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [newLocation, setNewLocation] = useState({
    location_id: "",
    name: "",
    latitude: "37.5665",
    longitude: "126.978",
    nx: "60",
    ny: "127",
  });

  const refresh = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    try {
      const result = await getLocations(search, PAGE_SIZE, offset);
      setLocations(result.data);
      setTotal(result.meta.page?.total ?? result.data.length);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "불러오기 실패");
    } finally {
      setLoading(false);
    }
  }, [offset, search]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      await createLocation({
        ...newLocation,
        latitude: Number(newLocation.latitude),
        longitude: Number(newLocation.longitude),
        nx: Number(newLocation.nx),
        ny: Number(newLocation.ny),
        enabled: true,
      });
      setNewLocation({
        location_id: "",
        name: "",
        latitude: "37.5665",
        longitude: "126.978",
        nx: "60",
        ny: "127",
      });
      setOffset(0);
      if (offset === 0) await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "생성 실패");
    }
  }

  async function toggle(location: Location) {
    try {
      await updateLocation(location.location_id, { enabled: !location.enabled });
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "상태 변경 실패");
    }
  }

  function runSearch() {
    if (offset === 0) {
      void refresh();
    } else {
      setOffset(0);
    }
  }

  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + locations.length, total);

  return (
    <>
      <div className="panel location-create-panel">
        <div className="panel-head">
          <div>
            <h2>새 KMA 위치</h2>
            <p>좌표와 격자 anchor를 등록합니다.</p>
          </div>
        </div>
        <form className="location-create-form" onSubmit={submit}>
          {newLocationFields.map((field) => (
            <label className="location-field" htmlFor={`new-location-${field.key}`} key={field.key}>
              <span>{field.label}</span>
              <input
                id={`new-location-${field.key}`}
                name={field.key}
                required
                step={field.type === "number" ? field.step : undefined}
                type={field.type}
                value={newLocation[field.key]}
                onChange={(event) =>
                  setNewLocation((current) => ({ ...current, [field.key]: event.target.value }))
                }
              />
            </label>
          ))}
          <button type="submit">위치 추가</button>
        </form>
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>Location catalog</h2>
            <p>비활성화는 이력 보존을 위해 enabled=false로 처리합니다.</p>
          </div>
          <div className="toolbar catalog-toolbar">
            <input
              aria-label="위치 검색"
              id="location-search"
              name="search"
              placeholder="검색"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <button className="secondary" onClick={runSearch} type="button">
              검색
            </button>
            <button className="secondary" onClick={() => void refresh()} type="button">
              새로고침
            </button>
          </div>
        </div>
        {message ? <div className="error" role="alert">{message}</div> : null}
        {loading ? (
          <div className="loading" aria-busy="true">위치를 불러오는 중…</div>
        ) : locations.length === 0 ? (
          <div className="empty">등록된 위치가 없습니다.</div>
        ) : (
          <div className="table-wrap">
          <table>
            <thead>
              <tr><th scope="col">location</th><th scope="col">grid</th><th scope="col">coordinates</th><th scope="col">status</th><th scope="col" /></tr>
            </thead>
            <tbody>
              {locations.map((location) => (
                <tr key={location.location_id}>
                  <td><strong>{location.name}</strong><br /><code>{location.location_id}</code></td>
                  <td><code>{location.nx ?? "—"} / {location.ny ?? "—"}</code></td>
                  <td><code>{location.latitude.toFixed(4)}, {location.longitude.toFixed(4)}</code></td>
                  <td><span className={`status ${location.enabled ? "on" : "off"}`}>{location.enabled ? "enabled" : "disabled"}</span></td>
                  <td><button className="secondary" onClick={() => void toggle(location)} type="button">{location.enabled ? "비활성화" : "활성화"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
        <div className="pagination" aria-label="위치 페이지 이동">
          <span>{total.toLocaleString("ko-KR")}개 위치 · {first}–{last}</span>
          <div className="toolbar">
            <button className="secondary" type="button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>이전</button>
            <button className="secondary" type="button" disabled={offset + locations.length >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>다음</button>
          </div>
        </div>
      </div>
    </>
  );
}
