"use client";

import { useEffect, useState } from "react";

import { getAllPublicLocations, getLatest, Location, WeatherValue } from "@/lib/api";

export default function WeatherPage() {
  const [locations, setLocations] = useState<Location[]>([]);
  const [selected, setSelected] = useState("");
  const [values, setValues] = useState<WeatherValue[]>([]);
  const [message, setMessage] = useState("위치를 선택하세요.");

  useEffect(() => {
    getAllPublicLocations().then((result) => {
      setLocations(result);
      if (result[0]) setSelected(result[0].location_id);
    }).catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "위치를 불러오지 못했습니다."));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setMessage("최신 fact를 불러오는 중…");
    getLatest(selected).then((result) => {
      setValues(result.data);
      setMessage(result.data.length ? "" : "선택한 위치에 아직 fact가 없습니다.");
    }).catch((reason: unknown) => setMessage(reason instanceof Error ? reason.message : "날씨를 불러오지 못했습니다."));
  }, [selected]);

  return (
    <>
      <header className="header"><div><div className="eyebrow">latest projection</div><h1>최신 날씨</h1><p className="description">논리 시각별 최신 revision projection을 확인합니다.</p></div><select aria-label="위치 선택" value={selected} onChange={(event) => setSelected(event.target.value)}><option value="">위치 선택</option>{locations.map((location) => <option key={location.location_id} value={location.location_id}>{location.name}</option>)}</select></header>
      <section className="panel">
        <div className="panel-head"><div><h2>{locations.find((location) => location.location_id === selected)?.name ?? "Weather values"}</h2><p>history가 아닌 current projection</p></div><span className="status on">{values.length} metrics</span></div>
        {message ? <div className={message.includes("실패") || message.includes("못") ? "error" : "empty"}>{message}</div> : <table><thead><tr><th>metric</th><th>value</th><th>target</th><th>known</th><th>source</th></tr></thead><tbody>{values.map((value) => <tr key={value.value_id}><td><strong>{value.metric_key}</strong><br /><small>{value.metric_name ?? value.dataset_key}</small></td><td>{value.value_number ?? value.value_text ?? "—"} <small>{value.unit ?? ""}</small></td><td><code>{new Date(value.target_at).toLocaleString("ko-KR")}</code></td><td><code>{value.known_at ? new Date(value.known_at).toLocaleString("ko-KR") : "—"}</code></td><td><code>{value.source_record_key.slice(0, 15)}…</code></td></tr>)}</tbody></table>}
      </section>
    </>
  );
}
