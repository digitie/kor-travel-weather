"use client";

import { useEffect, useState } from "react";

import { getProviders, Provider } from "@/lib/api";

export default function DatasetsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getProviders().then((result) => setProviders(result.data)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "데이터셋을 불러오지 못했습니다."));
  }, []);
  return <><header className="header"><div><div className="eyebrow">provider contract</div><h1>데이터셋</h1><p className="description">provider별 raw source를 공통 WeatherValue와 dataset 계약으로 정규화합니다.</p></div></header>{error ? <div className="error" role="alert">{error}</div> : null}<section className="dataset-list">{providers.flatMap((provider) => provider.datasets.map((dataset) => <article className="panel dataset-row" key={`${provider.provider}-${dataset.key}`}><div><span className="eyebrow">{provider.label} · {dataset.label}</span><h2>{dataset.key}</h2><p>{dataset.description}</p><small>{provider.provider} · {provider.credential_configured === false ? "credential 미설정" : provider.auth_required ? "credential 설정됨" : "key 불필요"}</small></div><code>{dataset.cadence}</code></article>))}</section></>;
}
