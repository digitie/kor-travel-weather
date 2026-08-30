"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { createLocation, getLocations, Location, updateLocation } from "@/lib/api";

const PAGE_SIZE = 100;

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
      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="panel-head">
          <div>
            <h2>새 KMA 위치</h2>
            <p>좌표와 격자 anchor를 등록합니다.</p>
          </div>
        </div>
        <form className="toolbar" onSubmit={submit} style={{ flexWrap: "wrap", padding: 18 }}>
          {(["location_id", "name", "latitude", "longitude", "nx", "ny"] as const).map(
            (key) => (
              <input
                key={key}
                required
                placeholder={key}
                value={newLocation[key]}
                onChange={(event) =>
                  setNewLocation((current) => ({ ...current, [key]: event.target.value }))
                }
              />
            ),
          )}
          <button type="submit">위치 추가</button>
        </form>
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>Location catalog</h2>
            <p>비활성화는 이력 보존을 위해 enabled=false로 처리합니다.</p>
          </div>
          <div className="toolbar">
            <input
              aria-label="위치 검색"
              placeholder="검색"
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
          <table>
            <thead>
              <tr><th>location</th><th>grid</th><th>coordinates</th><th>status</th><th /></tr>
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
