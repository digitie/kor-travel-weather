"use client";

import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import type { ColumnDef } from "@kor-travel/ui/virtual-table";
import { locationSchema, type LocationFields } from "@/lib/location-schema";
import { NULL_GLYPH } from "@/lib/format";

import { CursorPager } from "@/components/pagination-bar";
import { DataTable } from "@/components/ui/data-table";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/empty-state";
import { FilterActions, FilterBar, FilterField } from "@/components/filter-bar";
import { SectionCard } from "@/components/section-card";
import { StatusBadge } from "@/components/status-badge";

import {
  createLocation,
  getLocations,
  Location,
  updateLocation,
} from "@/lib/api";

const PAGE_SIZE = 100;
const defaultLocation: LocationFields = {
  location_id: "",
  name: "",
  latitude: 37.5665,
  longitude: 126.978,
  nx: 60,
  ny: 127,
};

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
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<LocationFields>({
    resolver: zodResolver(locationSchema),
    defaultValues: defaultLocation,
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

  async function submit(newLocation: LocationFields) {
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
      reset(defaultLocation);
      setOffset(0);
      if (offset === 0) await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "생성 실패");
    }
  }

  async function toggle(location: Location) {
    try {
      await updateLocation(location.location_id, {
        enabled: !location.enabled,
      });
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
    <div className="flex flex-col gap-6">
      <SectionCard
        description="위도·경도와 격자 좌표를 등록합니다."
        title="새 KMA 위치"
      >
        {/*
          .location-create-form은 e2e 로케이터용 훅이다. globals.css의 레거시 규칙
          (display:grid · padding · button min-width)이 unlayered라 utility를 이기므로
          form에서만 `flex!`·`p-0!`로 무력화한다. 그 규칙이 globals에서 사라지면 같이 걷어낸다.
        */}
        <form
          className="location-create-form flex! flex-col gap-4 p-0!"
          onSubmit={handleSubmit(submit)}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {newLocationFields.map((field) => {
              const inputId = `new-location-${field.key}`;
              return (
                <Field key={field.key}>
                  <FieldLabel htmlFor={inputId}>{field.label}</FieldLabel>
                  <Input
                    id={inputId}
                    required
                    step={field.type === "number" ? field.step : undefined}
                    type={field.type}
                    {...register(field.key, {
                      valueAsNumber: field.type === "number",
                    })}
                    aria-invalid={Boolean(errors[field.key])}
                  />
                </Field>
              );
            })}
          </div>
          {Object.keys(errors).length ? (
            <Alert variant="destructive">
              <AlertDescription>
                위치 ID와 이름, 유효한 좌표를 확인하세요.
              </AlertDescription>
            </Alert>
          ) : null}
          <div className="flex justify-end">
            <Button disabled={isSubmitting} type="submit">
              위치 추가
            </Button>
          </div>
        </form>
      </SectionCard>

      <SectionCard
        description="비활성화해도 기록은 남고, 수집 대상에서만 제외됩니다."
        title="등록된 위치 목록"
      >
        <FilterBar>
          <FilterField className="w-full sm:w-64" htmlFor="location-search" label="위치 검색">
            <Input
              aria-label="위치 검색"
              id="location-search"
              name="search"
              placeholder="검색"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </FilterField>
          <FilterActions>
            <Button onClick={runSearch} type="button" variant="outline">
              검색
            </Button>
            <Button
              onClick={() => void refresh()}
              type="button"
              variant="outline"
            >
              새로고침
            </Button>
          </FilterActions>
        </FilterBar>
        {message ? (
          <Alert variant="destructive">
            <AlertDescription>{message}</AlertDescription>
          </Alert>
        ) : null}
        {loading ? (
          <p aria-busy="true" className="py-4 text-sm text-text-secondary">
            위치를 불러오는 중…
          </p>
        ) : locations.length === 0 ? (
          <EmptyState title="등록된 위치가 없습니다." />
        ) : (
          <DataTable
            ariaLabel="등록된 위치 목록"
            data={locations}
            getRowId={(location) => location.location_id}
            columns={
              [
                {
                  id: "c0",
                  header: "위치",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const location = row.original;
                    return (
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <span className="font-medium text-text-primary">
                          {location.name}
                        </span>
                        <code className="font-mono text-xs text-text-secondary">
                          {location.location_id}
                        </code>
                      </div>
                    );
                  },
                },
                {
                  id: "c1",
                  header: "격자",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const location = row.original;
                    return (
                      <code className="font-mono text-xs text-text-primary tabular-nums">
                        {location.nx ?? NULL_GLYPH} /{" "}
                        {location.ny ?? NULL_GLYPH}
                      </code>
                    );
                  },
                },
                {
                  id: "c2",
                  header: "좌표",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const location = row.original;
                    return (
                      <code className="font-mono text-xs text-text-primary tabular-nums">
                        {location.latitude.toFixed(4)},{" "}
                        {location.longitude.toFixed(4)}
                      </code>
                    );
                  },
                },
                {
                  id: "c3",
                  header: "상태",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const location = row.original;
                    return (
                      <StatusBadge
                        label={location.enabled ? "사용 중" : "사용 안 함"}
                        status={location.enabled ? "active" : "inactive"}
                      />
                    );
                  },
                },
                {
                  id: "c4",
                  header: "",
                  enableSorting: false,
                  cell: ({ row }) => {
                    const location = row.original;
                    return (
                      <Button
                        onClick={() => void toggle(location)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        {location.enabled ? "비활성화" : "활성화"}
                      </Button>
                    );
                  },
                },
              ] satisfies ColumnDef<Location, unknown>[]
            }
          />
        )}
        <CursorPager
          hasNext={offset + locations.length < total}
          isFetching={loading}
          isFirst={offset === 0}
          onFirst={() => setOffset(0)}
          onNext={() => setOffset((value) => value + PAGE_SIZE)}
          previous={{
            available: offset > 0,
            onActivate: () =>
              setOffset((value) => Math.max(0, value - PAGE_SIZE)),
          }}
          summary={
            <>
              {total.toLocaleString("ko-KR")}개 위치 · {first}–{last}
            </>
          }
        />
      </SectionCard>
    </div>
  );
}
