"use client";

import { DagsterOperations } from "@kor-travel/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/admin-shell";
import { type DagsterSnapshot, getDagsterSnapshot, jobLabel } from "@/lib/dagster";
import { dagsterLocationUrl, dagsterRunUrl, dagsterScheduleUrl } from "@/lib/dagster-scope";

export default function DagsterPage() {
  const [snapshot, setSnapshot] = useState<DagsterSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const requestVersion = useRef(0);
  const load = useCallback(() => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    getDagsterSnapshot()
      .then((next) => { if (version === requestVersion.current) setSnapshot(next); })
      .catch((reason: unknown) => {
        if (version === requestVersion.current) {
          setError(reason instanceof Error ? reason.message : "Dagster 상태를 읽지 못했습니다.");
        }
      })
      .finally(() => { if (version === requestVersion.current) setLoading(false); });
  }, []);
  useEffect(() => {
    const sequence = requestVersion;
    load();
    return () => { ++sequence.current; };
  }, [load]);

  return (
    <>
      <PageHeader
        description="매시간 날씨를 자동으로 가져오는 작업이 잘 돌고 있는지 보여줍니다. 실패하거나 멈춘 작업을 여기서 확인하세요."
        section="시스템"
        title="Dagster 운영"
      />
      <DagsterOperations snapshot={snapshot} error={error} loading={loading} onRefresh={load}
        jobLabel={jobLabel} runUrl={dagsterRunUrl} scheduleUrl={dagsterScheduleUrl} locationUrl={dagsterLocationUrl()} />
    </>
  );
}
