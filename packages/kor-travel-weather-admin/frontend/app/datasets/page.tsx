"use client";

import Link from "next/link";
import { MapPin } from "lucide-react";
import { useEffect, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getProviders, Provider } from "@/lib/api";
import type { StatusTone } from "@/lib/status-label";

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

function credentialTone(provider: Provider): StatusTone {
  if (!provider.auth_required) return "neutral";
  return provider.credential_configured ? "success" : "warning";
}

const linkClassName =
  "rounded-control underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

export default function DatasetsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getProviders().then((result) => setProviders(result.data)).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "데이터셋을 불러오지 못했습니다."));
  }, []);
  return (
    <AdminShell
      description="각 데이터 제공처가 어떤 데이터를 얼마나 자주 보내주는지 정리한 목록입니다."
      section="수집 파이프라인"
      title="데이터셋"
    >
      <div className="flex flex-col gap-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <section
          aria-label="제공처별 데이터셋"
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
        >
          {providers.flatMap((provider) => provider.datasets.map((dataset) => {
            const meta = (
              <>
                <code className="font-mono text-xs text-text-primary">{dataset.cadence}</code>
                <StatusBadge label={credentialLabel(provider)} status={null} tone={credentialTone(provider)} />
              </>
            );
            return (
              <Card key={`${provider.provider}-${dataset.key}`} size="sm">
                <CardHeader>
                  <CardDescription>{provider.label} · {dataset.label}</CardDescription>
                  <CardTitle>
                    <Link className={linkClassName} href={detailHref(provider.provider)}>
                      {dataset.description}
                    </Link>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Link
                    className={`${linkClassName} inline-flex items-center gap-1 text-xs text-text-secondary hover:text-text-primary`}
                    href={detailHref(provider.provider)}
                  >
                    <MapPin aria-hidden="true" size={12} />
                    {detailLabel(provider.provider)}
                  </Link>
                </CardContent>
                <CardFooter className="flex items-center justify-between gap-2">
                  {provider.auth_required ? (
                    <Link
                      className={`${linkClassName} flex w-full items-center justify-between gap-2`}
                      href={`/settings/providers#provider-${provider.provider}`}
                      title="이 제공처의 API 키 설정으로 이동"
                    >
                      {meta}
                    </Link>
                  ) : (
                    <div className="flex w-full items-center justify-between gap-2">{meta}</div>
                  )}
                </CardFooter>
              </Card>
            );
          }))}
          {!providers.length && !error ? (
            <EmptyState className="col-span-full" framed title="등록된 데이터셋이 없습니다." />
          ) : null}
        </section>
      </div>
    </AdminShell>
  );
}
