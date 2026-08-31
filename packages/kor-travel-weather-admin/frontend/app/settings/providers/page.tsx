"use client";

import { KeyRound, LoaderCircle, RotateCcw, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/admin-shell";
import {
  deleteProviderCredential,
  getProviderCredentials,
  getProviders,
  Provider,
  ProviderCredential,
  updateProviderCredential,
} from "@/lib/api";

function sourceLabel(source: ProviderCredential["source"]) {
  if (source === "database") return "DB override";
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
      setError(reason instanceof Error ? reason.message : "provider 설정을 불러오지 못했습니다.");
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
    if (!window.confirm("DB에 저장된 API 키를 삭제할까요? 환경변수 키는 삭제되지 않습니다.")) return;
    setBusy(provider);
    setError(null);
    setNotice(null);
    try {
      await deleteProviderCredential(provider);
      await refresh();
      setNotice(`${providerByKey.get(provider)?.label ?? provider} DB override를 삭제했습니다.`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "API 키 삭제에 실패했습니다.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        actions={
          <button className="button secondary" type="button" onClick={() => void refresh()} disabled={loading}>
            <RotateCcw size={15} className={loading ? "spin" : ""} aria-hidden="true" />
            새로고침
          </button>
        }
        description="provider별 API 키를 암호화된 DB override로 관리합니다. 키 원문은 다시 표시하지 않습니다."
        section="시스템"
        title="Provider API 키"
      />
      {error ? <div className="error" role="alert">{error}</div> : null}
      {notice ? <div className="notice" role="status">{notice}</div> : null}
      <section className="panel settings-note" aria-label="API 키 보안 안내">
        <div className="settings-note-icon" aria-hidden="true"><KeyRound size={17} /></div>
        <div>
          <strong>안전한 credential 경계</strong>
          <p>저장 시 암호화하고 화면에는 source, fingerprint, 마지막 4자리만 표시합니다. 환경변수에서 온 키는 이 화면에서 삭제할 수 없습니다.</p>
        </div>
      </section>
      <section className="credential-grid" aria-label="provider API 키 목록">
        {loading && !credentials.length ? <div className="panel loading" role="status" aria-busy="true">provider 설정을 불러오는 중…</div> : null}
        {!loading && !credentials.length && !error ? <div className="panel empty">설정 가능한 provider가 없습니다.</div> : null}
        {credentials.map((credential) => {
          const provider = providerByKey.get(credential.provider);
          const isBusy = busy === credential.provider;
          const keyless = provider ? !provider.auth_required : false;
          return (
            <article className="panel credential-card" key={credential.provider}>
              <div className="credential-header">
                <div>
                  <span className="eyebrow">{credential.provider}</span>
                  <h2>{provider?.label ?? credential.provider}</h2>
                  <p>{provider?.base_url ?? "provider catalog"}</p>
                </div>
                <span className={`status ${credential.configured ? "on" : "off"}`}>
                  {credential.configured ? "configured" : "not configured"}
                </span>
              </div>
              <dl className="credential-meta">
                <div><dt>source</dt><dd>{sourceLabel(credential.source)}</dd></div>
                <div><dt>fingerprint</dt><dd><code>{credential.fingerprint ? credential.fingerprint.slice(0, 19) + "…" : "—"}</code></dd></div>
                <div><dt>last four</dt><dd><code>{credential.last4 ? `••••${credential.last4}` : "—"}</code></dd></div>
                <div><dt>updated</dt><dd>{updatedLabel(credential.updated_at)}</dd></div>
              </dl>
              {keyless ? (
                <p className="credential-readonly">이 provider는 API 키 없이 동작합니다.</p>
              ) : (
                <div className="credential-form">
                  <label htmlFor={`credential-${credential.provider}`}>
                    <span>새 API 키</span>
                    <input
                      autoComplete="new-password"
                      id={`credential-${credential.provider}`}
                      name={`credential-${credential.provider}`}
                      placeholder={credential.configured ? "변경할 때만 입력" : "API 키 입력"}
                      type="password"
                      value={drafts[credential.provider] ?? ""}
                      onChange={(event) => setDrafts((current) => ({ ...current, [credential.provider]: event.target.value }))}
                    />
                  </label>
                  <div className="credential-actions">
                    <button className="button" type="button" disabled={isBusy} onClick={() => void save(credential.provider)}>
                      {isBusy ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <Save size={15} aria-hidden="true" />}
                      저장
                    </button>
                    <button className="button secondary" type="button" disabled={isBusy || credential.source !== "database"} onClick={() => void remove(credential.provider)}>
                      <Trash2 size={15} aria-hidden="true" />
                      DB 키 삭제
                    </button>
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </section>
    </>
  );
}
