"use client";

import { KeyRoundIcon, RotateCcwIcon, SaveIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AdminShell } from "@/components/admin-shell";
import { useConfirm } from "@/components/confirm-dialog";
import { DetailList } from "@/components/detail-list";
import { EmptyState } from "@/components/empty-state";
import { SectionCard } from "@/components/section-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import {
  deleteProviderCredential,
  getProviderCredentials,
  getProviders,
  Provider,
  ProviderCredential,
  updateProviderCredential,
} from "@/lib/api";

function sourceLabel(source: ProviderCredential["source"]) {
  if (source === "database") return "직접 등록한 키";
  if (source === "environment") return "환경변수";
  return "미설정";
}

function updatedLabel(value: string | null) {
  if (!value) return "아직 저장되지 않음";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "업데이트 시각 없음" : date.toLocaleString("ko-KR");
}

export default function ProviderSettingsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const confirm = useConfirm();

  const providerByKey = useMemo(
    () => new Map(providers.map((provider) => [provider.provider, provider])),
    [providers],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [providerResult, credentialResult] = await Promise.all([
        getProviders(),
        getProviderCredentials(),
      ]);
      setProviders(providerResult.data);
      setCredentials(credentialResult.data);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "제공처 설정을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function save(provider: string) {
    const apiKey = drafts[provider]?.trim() ?? "";
    if (!apiKey) {
      setError("저장할 API 키를 입력하세요.");
      return;
    }
    setBusy(provider);
    setError(null);
    setNotice(null);
    try {
      await updateProviderCredential(provider, apiKey);
      setDrafts((current) => ({ ...current, [provider]: "" }));
      await refresh();
      setNotice(`${providerByKey.get(provider)?.label ?? provider} API 키를 저장했습니다.`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "API 키 저장에 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(provider: string) {
    const ok = await confirm({
      title: "직접 등록한 API 키를 삭제할까요?",
      description: "환경변수로 설정한 키는 삭제되지 않습니다.",
      confirmLabel: "삭제",
      destructive: true,
    });
    if (!ok) return;
    setBusy(provider);
    setError(null);
    setNotice(null);
    try {
      await deleteProviderCredential(provider);
      await refresh();
      setNotice(`${providerByKey.get(provider)?.label ?? provider}에 직접 등록했던 키를 삭제했습니다.`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "API 키 삭제에 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AdminShell
      actions={
        <Button loading={loading} type="button" variant="outline" onClick={() => void refresh()}>
          <RotateCcwIcon data-icon="inline-start" />
          새로고침
        </Button>
      }
      description="제공처 API 키를 암호화해서 저장하고 관리합니다. 한 번 저장한 키는 원문으로 다시 보여주지 않습니다."
      section="시스템"
      title="제공처 API 키"
    >
      <div className="flex flex-col gap-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {notice ? (
          <Alert>
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        ) : null}
        <Alert aria-label="API 키 보안 안내">
          <KeyRoundIcon />
          <AlertTitle>안전하게 보관합니다</AlertTitle>
          <AlertDescription>
            저장할 때 암호화하고, 화면에는 키가 어디서 왔는지와 마지막 4자리만 보여줍니다. 환경변수로 설정한 키는 이 화면에서 삭제할 수 없습니다.
          </AlertDescription>
        </Alert>
        <section aria-label="제공처 API 키 목록" className="grid gap-6 lg:grid-cols-2">
          {loading && !credentials.length ? (
            <div className="lg:col-span-2" role="status" aria-busy="true">
              <EmptyState title="제공처 설정을 불러오는 중…" />
            </div>
          ) : null}
          {!loading && !credentials.length && !error ? (
            <div className="lg:col-span-2">
              <EmptyState title="설정 가능한 제공처가 없습니다." />
            </div>
          ) : null}
          {credentials.map((credential) => {
            const provider = providerByKey.get(credential.provider);
            const isBusy = busy === credential.provider;
            const keyless = provider ? !provider.auth_required : false;
            return (
              <article className="min-w-0" id={`provider-${credential.provider}`} key={credential.provider}>
                <SectionCard
                  actions={
                    <Badge variant={credential.configured ? "success" : "neutral"}>
                      {credential.configured ? "설정됨" : "미설정"}
                    </Badge>
                  }
                  description={provider?.base_url ?? "등록된 제공처 정보 없음"}
                  title={provider?.label ?? credential.provider}
                >
                  <DetailList
                    items={[
                      { label: "제공처 ID", value: credential.provider, mono: true },
                      { label: "등록 방식", value: sourceLabel(credential.source) },
                      {
                        label: "키 식별값",
                        value: credential.fingerprint ? `${credential.fingerprint.slice(0, 19)}…` : null,
                        mono: true,
                      },
                      {
                        label: "끝 네 자리",
                        value: credential.last4 ? `••••${credential.last4}` : null,
                        mono: true,
                      },
                      { label: "저장 시각", value: updatedLabel(credential.updated_at) },
                    ]}
                    layout="inline"
                  />
                  {keyless ? (
                    <p className="text-sm text-text-secondary">이 제공처는 API 키 없이 동작합니다.</p>
                  ) : (
                    <div className="flex flex-col gap-4">
                      <FormField
                        autoComplete="new-password"
                        id={`credential-${credential.provider}`}
                        label="새 API 키"
                        name={`credential-${credential.provider}`}
                        placeholder={credential.configured ? "변경할 때만 입력" : "API 키 입력"}
                        type="password"
                        value={drafts[credential.provider] ?? ""}
                        onChange={(event) => setDrafts((current) => ({ ...current, [credential.provider]: event.target.value }))}
                      />
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          loading={isBusy}
                          type="button"
                          onClick={() => void save(credential.provider)}
                        >
                          <SaveIcon data-icon="inline-start" />
                          저장
                        </Button>
                        <Button
                          disabled={isBusy || credential.source !== "database"}
                          disabledReason={
                            credential.source !== "database" ? "직접 등록한 키만 삭제할 수 있습니다." : undefined
                          }
                          type="button"
                          variant="destructive"
                          onClick={() => void remove(credential.provider)}
                        >
                          <Trash2Icon data-icon="inline-start" />
                          등록한 키 삭제
                        </Button>
                      </div>
                    </div>
                  )}
                </SectionCard>
              </article>
            );
          })}
        </section>
      </div>
    </AdminShell>
  );
}
