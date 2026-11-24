"use client";

import {
  keepPreviousData,
  type QueryClient,
  queryOptions,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  fetchLocationCatalog,
  getPublicRegionLocations,
  type LocationCatalog,
  type WeatherKind,
} from "@/lib/api";

/**
 * 위치 카탈로그를 화면·필터 사이에서 재사용한다(호출 검토 B12).
 *
 * 키는 지도 필터의 `sourceKey`(`JSON.stringify([제공처, 종류])`)다. 같은 조건으로 돌아오면
 * {@link CATALOG_STALE_MS} 안에서는 다시 받지 않고, 지난 뒤에는 페이지별 ETag로 다시 물어 바뀌지
 * 않은 페이지는 304로 재사용한다. 요청 함수가 이미 일시 오류를 다시 보내므로 query 재시도는 끈다.
 */
export const CATALOG_STALE_MS = 60_000;
const REGION_TIMEOUT_MS = 10_000;

export function catalogSourceKey(
  providers: readonly string[] | null | undefined,
  kinds: readonly WeatherKind[] | undefined,
): string {
  return JSON.stringify([providers ?? null, kinds ?? null]);
}

export function locationCatalogQueryKey(sourceKey: string) {
  return ["weather-location-catalog", sourceKey] as const;
}

/** 카탈로그 query 설정. 다시 받을 때 같은 key의 지난 카탈로그(페이지 ETag)를 넘긴다. */
export function locationCatalogQuery(
  client: QueryClient,
  providers: readonly string[] | null | undefined,
  kinds: readonly WeatherKind[] | undefined,
) {
  return queryOptions({
    queryKey: locationCatalogQueryKey(catalogSourceKey(providers, kinds)),
    queryFn: ({ queryKey, signal }) =>
      fetchLocationCatalog(
        { providers: providers ?? undefined, kinds },
        signal,
        client.getQueryData<LocationCatalog>(queryKey),
      ),
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
}

export function useLocationCatalog({
  providers,
  kinds,
  enabled = true,
  keepPrevious = false,
}: {
  providers?: readonly string[] | null;
  kinds?: readonly WeatherKind[];
  enabled?: boolean;
  keepPrevious?: boolean;
}) {
  const client = useQueryClient();
  return useQuery({
    ...locationCatalogQuery(client, providers, kinds),
    enabled,
    placeholderData: keepPrevious ? keepPreviousData : undefined,
  });
}

/** 시·도/시·군·구 대표 위치. 실패·미준비는 전체 카탈로그 경로로 진행하므로 오류를 보이지 않는다. */
export function useRegionProjection() {
  return useQuery({
    queryKey: ["weather-region-projection"],
    queryFn: ({ signal }) =>
      getPublicRegionLocations(AbortSignal.any([signal, AbortSignal.timeout(REGION_TIMEOUT_MS)]))
        .then(({ data }) => (data.ready ? data : null)),
    staleTime: CATALOG_STALE_MS,
    retry: false,
  });
}
