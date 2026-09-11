"use client";

import Link from "next/link";
import { MapPin } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import { getProviders, Provider } from "@/lib/api";

// These providers key their readings off this project's own location catalog
// (fixed KMA grids / measurement stations); the rest fetch on demand for
// whatever lat/lon a request asks about, so their detail lives on the map
// instead.
const LOCATION_CATALOG_PROVIDERS = new Set([
  "python-kma-api",
  "python-khoa-api",
  "python-krforest-api",
  "python-krex-api",
  "python-airkorea-api",
]);

function detailHref(providerKey: string) {
  return LOCATION_CATALOG_PROVIDERS.has(providerKey) ? "/locations" : "/weather";
}

function detailLabel(providerKey: string) {
  return LOCATION_CATALOG_PROVIDERS.has(providerKey) ? "위치 카탈로그에서 보기" : "날씨 지도에서 보기";
}

function credentialLabel(provider: Provider) {
  if (!provider.auth_required) return "API 키 불필요";
  return provider.credential_configured ? "API 키 설정됨" : "API 키 미설정";
}

export default function DatasetsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getProviders().then((result) => setProviders(result.data)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "데이터셋을 불러오지 못했습니다."));
  }, []);
  return (
    <>
      <PageHeader
        description="각 데이터 제공처가 어떤 데이터를 얼마나 자주 보내주는지 정리한 목록입니다."
        section="수집 파이프라인"
        title="데이터셋"
      />
      {error ? <div className="error" role="alert">{error}</div> : null}
      <section className="dataset-list" aria-label="제공처별 데이터셋">
        {providers.flatMap((provider) => provider.datasets.map((dataset) => (
          <article className="panel dataset-row" key={`${provider.provider}-${dataset.key}`}>
            <div>
              <span className="eyebrow">{provider.label} · {dataset.label}</span>
              <Link className="dataset-detail-link" href={detailHref(provider.provider)}>
                <h2>{dataset.description}</h2>
                <small><MapPin size={12} aria-hidden="true" /> {detailLabel(provider.provider)}</small>
              </Link>
            </div>
            {provider.auth_required ? (
              <Link className="dataset-cadence" href={`/settings/providers#provider-${provider.provider}`} title="이 제공처의 API 키 설정으로 이동">
                <code>{dataset.cadence}</code>
                <small>{credentialLabel(provider)}</small>
              </Link>
            ) : (
              <div className="dataset-cadence">
                <code>{dataset.cadence}</code>
                <small>{credentialLabel(provider)}</small>
              </div>
            )}
          </article>
        )))}
        {!providers.length && !error ? <div className="panel empty">등록된 데이터셋이 없습니다.</div> : null}
      </section>
    </>
  );
}
