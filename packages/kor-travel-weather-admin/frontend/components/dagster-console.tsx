"use client";

import { DagsterLogControls } from "@kor-travel/ui/dagster-workbench";
import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import { SectionCard } from "@/components/section-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormSelect } from "@/components/ui/form-select";
import { NativeSelectOption } from "@/components/ui/native-select-option";
import { CONSOLE_ACTIVE, CONSOLE_COUNT_LABELS, consoleStatus, type ConsoleActionRequest, type ConsoleActionResult, type ConsoleCatalog, type ConsoleEvent, type ConsoleInstigator, type ConsoleRunDetail } from "@/lib/dagster-console-model";
import { ConsoleRequestError, getConsoleCatalog, getConsoleRequestStatus, getConsoleRun, performConsoleAction } from "@/lib/dagster-console-client";
import { dagsterRunUrl } from "@/lib/dagster-scope";

function eventRows(events:ConsoleEvent[]) {
  const counts=new Map<string,number>();
  return events.map(event=>{const identity=JSON.stringify([event.kind,event.time,event.step,event.detail,event.counts]);const occurrence=counts.get(identity)??0;counts.set(identity,occurrence+1);return {event,key:`${identity}|${occurrence}`};});
}

const date = (value: number | null) => value === null ? "미확인" : new Date(value * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
const elapsed = (start: number | null, end: number | null) => start === null ? "—" : `${Math.max(0,Math.round((end ?? Date.now()/1000)-start)).toLocaleString()}초`;

function ActionNotice({ result, pending, onCheck, onRecover, busy }: { result: ConsoleActionResult | null; pending: ConsoleActionRequest | null; onCheck: () => void; onRecover: () => void; busy: boolean }) {
  if (!result) return null;
  return <Alert variant={result.state === "rejected" ? "destructive" : "default"}>
    <AlertDescription>
      <p>{result.message}</p>
      <div className="flex flex-wrap items-center gap-2">
        {result.runId ? <a className="text-brand underline underline-offset-4" href={dagsterRunUrl(result.runId)} rel="noreferrer" target="_blank">요청한 실행 보기 ↗</a> : null}
        {result.state === "unknown" && pending && ["launch","retry"].includes(pending.action) ? <><Button loading={busy} size="sm" type="button" variant="outline" onClick={onCheck}>요청 상태 확인</Button><Button loading={busy} size="sm" type="button" variant="outline" onClick={onRecover}>같은 요청 예약 확인·복구</Button></> : null}
      </div>
    </AlertDescription>
  </Alert>;
}

/** 기존 예약을 확인한 복구에서는 뒤늦은 실행 차단을 예약 해제로 해석하지 않는다. */
function keepKnownReservation(current:ConsoleActionResult,next:ConsoleActionResult,requestId:string):ConsoleActionResult {
  if(current.state!=="unknown"||!current.runId||!["blocked","rejected"].includes(next.state))return next;
  return {...next,state:"unknown",runId:current.runId,requestId,message:`${next.message} 기존 예약의 해제가 확인되지 않아 같은 요청을 유지합니다.`};
}

function useConsoleAction(onChanged?: () => void) {
  const [busy,setBusy] = useState(false), [result,setResult] = useState<ConsoleActionResult | null>(null);
  const pending = useRef<ConsoleActionRequest | null>(null), inFlight = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current=true; return () => { mounted.current=false; }; },[]);
  const perform = async (action: Omit<ConsoleActionRequest,"operation" | "requestId">) => {
    if (inFlight.current || result?.state === "unknown" && pending.current && ["launch","retry"].includes(pending.current.action)) return;
    inFlight.current=true; setBusy(true); setResult(null);
    const command: ConsoleActionRequest = { ...action, operation:"action",requestId:crypto.randomUUID() };
    pending.current=command;
    try {
      const next=await performConsoleAction(command);
      if (!mounted.current) return;
      setResult(next);
      if (next.state !== "unknown") pending.current=null;
      if (next.state === "accepted" || next.state === "unchanged") onChanged?.();
    } catch (error) {
      if (mounted.current) setResult({ state:error instanceof ConsoleRequestError && !error.uncertain?"rejected":"unknown",message:error instanceof ConsoleRequestError?error.message:"요청 결과를 확인하지 못했습니다. 상태를 새로 조회하세요." });
    } finally { inFlight.current=false; if(mounted.current)setBusy(false); }
  };
  const check = async () => {
    if(inFlight.current || !pending.current) return;
    inFlight.current=true; setBusy(true);
    try { const next=await getConsoleRequestStatus(pending.current.requestId); if(mounted.current){setResult(next);if(next.state!=="unknown"){pending.current=null;onChanged?.();}} }
    catch { if(mounted.current)setResult({state:"unknown",message:"요청 상태를 읽지 못했습니다. 변경 요청은 다시 보내지 않았습니다."}); }
    finally { inFlight.current=false;if(mounted.current)setBusy(false); }
  };
  const recover = async () => {
    const command=pending.current;
    if(inFlight.current||!command||!["launch","retry"].includes(command.action))return;
    inFlight.current=true;setBusy(true);
    try {
      const current=await getConsoleRequestStatus(command.requestId);
      // 명시적으로 누른 이 경로에서만 동일 ID/본문으로 native idempotent 예약을 복구한다.
      const attempted=current.state==="unknown"?await performConsoleAction(command):current;
      const next=keepKnownReservation(current,attempted,command.requestId);
      if(mounted.current){setResult(next);if(next.state!=="unknown"){pending.current=null;onChanged?.();}}
    }catch{if(mounted.current)setResult({state:"unknown",message:"같은 요청의 복구 결과가 확인되지 않았습니다. 새 실행 요청은 만들지 않았습니다."});}
    finally{inFlight.current=false;if(mounted.current)setBusy(false);}
  };
  return {busy,result,pending:pending.current,perform,check,recover,unresolved:result?.state==="unknown"&&pending.current!==null&&["launch","retry"].includes(pending.current.action)};
}

type WarningRun = { runId:string;jobName:string;tags?:Array<{key:string;value:string}> };

/** 실행 성공과 자료 갱신의 완결성은 별개다. 경고 태그만 읽고 로그를 목록마다 미리 받지 않는다. */
export function DagsterCollectionWarnings({runs,jobLabel,onSelectRun}:{runs:WarningRun[];jobLabel:(name:string)=>string;onSelectRun:(runId:string)=>void}) {
  const warnings=[...new Map(runs.map(run=>[run.runId,run])).values()].filter(run=>run.tags?.some(tag=>tag.key==="kortravel/collection-outcome"&&["partial","deferred"].includes(tag.value)));
  return <Alert><AlertDescription>
    <p>실행 완료는 모든 자료의 최신화를 보장하지 않습니다. 수집 결과는 실행 상세에서 확인하세요.</p>
    {warnings.length?<><p className="font-semibold">이번 조회의 자료 확인 필요 실행 {warnings.length}건</p><div className="flex flex-wrap gap-2">{warnings.slice(0,20).map(run=><Button key={run.runId} size="sm" type="button" variant="outline" onClick={()=>onSelectRun(run.runId)}>{jobLabel(run.jobName)} · {run.tags?.find(tag=>tag.key==="kortravel/collection-outcome")?.value==="partial"?"부분 완료":"갱신 대기"} · {run.runId.slice(0,8)}</Button>)}</div>{warnings.length>20?<p>나머지 실행은 아래 실행 목록에서 선택하세요.</p>:null}</>:null}
  </AlertDescription></Alert>;
}

type ActionCommand = Omit<ConsoleActionRequest,"operation" | "requestId">;
type ActionController = ReturnType<typeof useConsoleAction>;
type JobLabel = (name:string)=>string;

function useConsoleCatalog() {
  const [catalog,setCatalog]=useState<ConsoleCatalog|null>(null),[error,setError]=useState(""),[loading,setLoading]=useState(true);
  const request=useRef<AbortController|null>(null),sequence=useRef(0);
  const load=useCallback(()=>{
    const version=++sequence.current;
    request.current?.abort();
    const current=new AbortController();request.current=current;setLoading(true);
    getConsoleCatalog(current.signal).then(next=>{
      if(version!==sequence.current||current.signal.aborted)return;
      setCatalog(next);setError("");
    }).catch(()=>{
      if(version===sequence.current&&!current.signal.aborted)setError("작업 제어 상태를 읽지 못했습니다. 마지막 확인 결과를 유지합니다.");
    }).finally(()=>{if(version===sequence.current)setLoading(false);});
  },[]);
  useEffect(()=>{
    const current=request,seq=sequence;
    load();
    return()=>{++seq.current;current.current?.abort();};
  },[load]);
  return {catalog,error,loading,load};
}

function ConsoleActionNotice({action}:{action:ActionController}) {
  return <ActionNotice result={action.result} pending={action.pending} onCheck={()=>void action.check()} onRecover={()=>void action.recover()} busy={action.busy}/>;
}

function JobControl({jobs,supported,disabled,action,jobLabel}:{jobs:string[];supported:boolean|null;disabled:boolean;action:ActionController;jobLabel:JobLabel}) {
  const [selected,setSelected]=useState("");
  const chosen=jobs.includes(selected)?selected:jobs[0]??"";
  return <div className="flex min-w-0 flex-col gap-2">
    <FormSelect label="수동 실행 작업" reserveMessage={false} disabled={!jobs.length||disabled} value={chosen} onChange={event=>setSelected(event.currentTarget.value)}>
      {!jobs.length?<NativeSelectOption value="">실행 가능한 작업 없음</NativeSelectOption>:jobs.map(name=><NativeSelectOption key={name} value={name}>{jobLabel(name)}</NativeSelectOption>)}
    </FormSelect>
    <Button size="sm" type="button" loading={action.busy} disabled={disabled||!chosen||!supported} disabledReason="작업 목록·권한과 기존 요청 상태를 먼저 확인하세요." onClick={()=>void action.perform({action:"launch",name:chosen})}>지금 실행</Button>
    {supported===false?<p className="text-xs text-text-secondary">현재 서버가 안전한 실행 예약 기능을 제공하지 않습니다.</p>:null}
  </div>;
}

type InstigatorControlProps={items:ConsoleInstigator[];disabled:boolean;busy:boolean;onPerform:(action:ActionCommand)=>Promise<void>};
function ScheduleControl({items,disabled,busy,onPerform,jobLabel}:InstigatorControlProps&{jobLabel:JobLabel}) {
  const [selected,setSelected]=useState("");
  const chosen=items.find(item=>item.name===selected)??items[0];
  const running=chosen?.status==="RUNNING";
  const permitted=running?chosen?.canStop:chosen?.canStart;
  return <div className="flex min-w-0 flex-col gap-2">
    <FormSelect label="스케줄" reserveMessage={false} disabled={!items.length||disabled} value={chosen?.name??""} onChange={event=>setSelected(event.currentTarget.value)}>
      {!items.length?<NativeSelectOption value="">등록된 스케줄 없음</NativeSelectOption>:items.map(item=><NativeSelectOption key={item.name} value={item.name}>{jobLabel(item.jobName??item.name)} · {item.status==="RUNNING"?"사용 중":"중지"}</NativeSelectOption>)}
    </FormSelect>
    <Button size="sm" type="button" variant="outline" loading={busy} disabled={disabled||!permitted} disabledReason="이 스케줄을 변경할 권한 또는 서버 기능이 없습니다." onClick={()=>chosen&&void onPerform({action:running?"stopSchedule":"startSchedule",name:chosen.name})}>{running?"스케줄 중지":"스케줄 시작"}</Button>
    {chosen?.cron?<p className="text-xs text-text-secondary">실행 규칙: {chosen.cron}</p>:null}
  </div>;
}

function SensorControl({items,disabled,busy,onPerform}:InstigatorControlProps) {
  const [selected,setSelected]=useState("");
  const chosen=items.find(item=>item.name===selected)??items[0];
  const running=chosen?.status==="RUNNING";
  const permitted=running?chosen?.canStop:chosen?.canStart;
  return <div className="flex min-w-0 flex-col gap-2">
    <FormSelect label="센서" reserveMessage={false} disabled={!items.length||disabled} value={chosen?.name??""} onChange={event=>setSelected(event.currentTarget.value)}>
      {!items.length?<NativeSelectOption value="">등록된 센서 없음</NativeSelectOption>:items.map(item=><NativeSelectOption key={item.name} value={item.name}>{item.name} · {item.status==="RUNNING"?"사용 중":"중지"}</NativeSelectOption>)}
    </FormSelect>
    <Button size="sm" type="button" variant="outline" loading={busy} disabled={disabled||!permitted} disabledReason="이 센서를 변경할 권한 또는 서버 기능이 없습니다." onClick={()=>chosen&&void onPerform({action:running?"stopSensor":"startSensor",name:chosen.name})}>{running?"센서 중지":"센서 시작"}</Button>
  </div>;
}

export function DagsterConsoleControls({jobLabel,onChanged}:{jobLabel:JobLabel;onChanged?:()=>void}) {
  const {catalog,error,loading,load}=useConsoleCatalog();
  const action=useConsoleAction(()=>{load();onChanged?.();});
  const disabled=loading||action.busy||action.unresolved;
  return <SectionCard title="작업 실행·예약 제어" description="현재 프로젝트의 작업만 제어합니다. 실행 예약은 데이터 적재 완료를 뜻하지 않습니다.">
    <div className="flex justify-end"><Button size="sm" type="button" variant="outline" loading={loading} onClick={load}>제어 상태 새로고침</Button></div>
    {error?<Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>:null}
    <div className="grid min-w-0 gap-4 lg:grid-cols-3">
      <JobControl jobs={catalog?.jobs??[]} supported={catalog?.capabilities.launch??null} disabled={disabled} action={action} jobLabel={jobLabel}/>
      <ScheduleControl items={catalog?.schedules??[]} disabled={disabled} busy={action.busy} onPerform={action.perform} jobLabel={jobLabel}/>
      <SensorControl items={catalog?.sensors??[]} disabled={disabled} busy={action.busy} onPerform={action.perform}/>
    </div>
    <ConsoleActionNotice action={action}/>
    <p className="text-xs text-text-secondary">스케줄·센서 중지는 새 예약을 중지합니다. 이미 진행 중인 실행은 선택 상세에서 별도로 취소할 수 있습니다.</p>
  </SectionCard>;
}

function mergeRunDetail(next:ConsoleRunDetail,previous:ConsoleRunDetail|null,nextPage:boolean,statusOnly:boolean):ConsoleRunDetail {
  if(!previous)return next;
  if(statusOnly)return {...next,events:previous.events,cursor:previous.cursor,hasMore:previous.hasMore};
  if(!nextPage)return next;
  const unavailable=next.events.some(event=>event.kind==="UNAVAILABLE");
  const events=[...new Map([...previous.events,...next.events].map(event=>[`${event.kind}|${event.time}|${event.step}|${event.detail}`,event])).values()].slice(-200);
  return {...next,events,cursor:unavailable?previous.cursor:next.cursor,hasMore:unavailable?previous.hasMore:next.hasMore};
}

function useRunRefresh(detail:ConsoleRunDetail|null,loading:boolean,load:(nextPage?:boolean,statusOnly?:boolean)=>void) {
  const visible=useEffectEvent(()=>{
    if(document.visibilityState!=="visible"||!detail||!CONSOLE_ACTIVE.has(detail.status))return;
    load(!detail.hasMore,detail.hasMore);
  });
  useEffect(()=>{
    const handler=()=>visible();
    document.addEventListener("visibilitychange",handler);
    return()=>document.removeEventListener("visibilitychange",handler);
  },[]);
  useEffect(()=>{
    if(!detail||loading||!CONSOLE_ACTIVE.has(detail.status))return;
    const timer=setTimeout(()=>{if(document.visibilityState!=="hidden")load(!detail.hasMore,detail.hasMore);},5000);
    return()=>clearTimeout(timer);
  },[detail,loading,load]);
}

function useConsoleRun(runId:string) {
  const [detail,setDetail]=useState<ConsoleRunDetail|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState("");
  const request=useRef<AbortController|null>(null),sequence=useRef(0),currentDetail=useRef<ConsoleRunDetail|null>(null);
  const load=useCallback((nextPage=false,statusOnly=false)=>{
    const version=++sequence.current;
    request.current?.abort();
    const controller=new AbortController();request.current=controller;setLoading(true);
    const previous=currentDetail.current,cursor=nextPage?previous?.cursor:null;
    getConsoleRun(runId,cursor,controller.signal,!statusOnly).then(next=>{
      if(version!==sequence.current||controller.signal.aborted)return;
      const value=mergeRunDetail(next,previous,nextPage,statusOnly);
      currentDetail.current=value;setDetail(value);setError("");
    }).catch(()=>{
      if(version===sequence.current&&!controller.signal.aborted)setError("실행 상세를 읽지 못했습니다. 마지막 확인 결과를 유지합니다.");
    }).finally(()=>{if(version===sequence.current){setLoading(false);request.current=null;}});
  },[runId]);
  useEffect(()=>{
    const current=request,seq=sequence;
    load();
    return()=>{++seq.current;current.current?.abort();};
  },[load]);
  useRunRefresh(detail,loading,load);
  return {detail,loading,error,load};
}

function RunToolbar({detail,loading,action,confirmCancel,onToggleCancel,onRefresh,runId}:{detail:ConsoleRunDetail|null;loading:boolean;action:ActionController;confirmCancel:boolean;onToggleCancel:()=>void;onRefresh:()=>void;runId:string}) {
  return <div className="flex flex-wrap items-center gap-2">
    <h4 className="font-semibold">실행·단계 진행</h4>
    <Button size="sm" type="button" variant="outline" loading={loading} onClick={onRefresh}>상세 새로고침</Button>
    {detail?.canCancel?<Button size="sm" type="button" variant="outline" disabled={action.busy} aria-expanded={confirmCancel} onClick={onToggleCancel}>실행 취소</Button>:null}
    {detail?.canRetry?<Button size="sm" type="button" variant="outline" loading={action.busy} disabled={action.unresolved} disabledReason="이전 실행 요청의 상태를 먼저 확인하세요." onClick={()=>void action.perform({action:"retry",runId})}>같은 작업 다시 실행</Button>:null}
  </div>;
}

function CancelConfirmation({busy,onConfirm,onClose}:{busy:boolean;onConfirm:()=>void;onClose:()=>void}) {
  return <Alert><AlertDescription><p>선택한 실행에 정상 취소를 요청합니다. 이미 저장된 자료는 유지합니다.</p><div className="flex flex-wrap gap-2"><Button size="sm" type="button" loading={busy} onClick={onConfirm}>취소 요청 보내기</Button><Button size="sm" type="button" variant="ghost" onClick={onClose}>돌아가기</Button></div></AlertDescription></Alert>;
}

function RunMetrics({detail}:{detail:ConsoleRunDetail}) {
  return <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
    <dt className="text-text-secondary">현재 상태</dt><dd>{consoleStatus(detail.status)}</dd>
    <dt className="text-text-secondary">대기열 등록</dt><dd>{date(detail.queuedTime??detail.creationTime)}</dd>
    <dt className="text-text-secondary">시작</dt><dd>{date(detail.startTime)}</dd>
    <dt className="text-text-secondary">종료</dt><dd>{date(detail.endTime)}</dd>
    <dt className="text-text-secondary">실행 시간</dt><dd>{elapsed(detail.startTime,detail.endTime)}</dd>
    <dt className="text-text-secondary">단계</dt><dd>완료 {detail.stepsSucceeded??"미확인"} · 실패 {detail.stepsFailed??"미확인"} · 자료 게시 {detail.materializations??"미확인"}</dd>
    <dt className="text-text-secondary">최근 확인</dt><dd>{date(Date.parse(detail.checkedAt)/1000)}</dd>
  </dl>;
}

function RunWarnings({events}:{events:ConsoleEvent[]}) {
  const warnings=events.filter(event=>event.warning);
  if(!warnings.length)return null;
  return <Alert><AlertDescription><p>실행 상태와 별도로 자료 수집·후속 처리에 확인할 내용이 있습니다.</p>{eventRows(warnings.slice(-3)).map(({event,key})=><p className="break-words whitespace-pre-wrap" key={key}>{event.detail??event.title}</p>)}</AlertDescription></Alert>;
}

function RunSteps({detail,jobLabel}:{detail:ConsoleRunDetail;jobLabel:JobLabel}) {
  return <>
    {detail.steps.length?<ul aria-label="단계별 상태" className="flex flex-col gap-2">{detail.steps.map(step=><li className="flex min-w-0 flex-col gap-1 text-xs" key={step.key}><span className="break-words font-semibold">{jobLabel(step.key)} · {consoleStatus(step.status)}</span><span className="text-text-secondary">{elapsed(step.startTime,step.endTime)} · 시도 {step.attempts}회</span></li>)}</ul>:<p className="text-xs text-text-secondary">아직 실행된 단계가 없습니다. 대기 상태와 시작 시각을 확인하세요.</p>}
    {detail.stepsTruncated?<p className="text-xs text-text-secondary">단계가 1,000개를 넘어 일부만 표시합니다. 전체는 Dagster에서 확인하세요.</p>:null}
  </>;
}

function RunEvent({event,jobLabel}:{event:ConsoleEvent;jobLabel:JobLabel}) {
  return <li className="flex min-w-0 flex-col gap-1 text-xs">
    <span>{date(event.time)} · {event.title}</span>
    {event.step?<span className="break-words text-text-secondary">{jobLabel(event.step)}</span>:null}
    {event.detail?<p className="break-words whitespace-pre-wrap">{event.detail}</p>:null}
    {Object.keys(event.counts).length?<p className="break-words text-text-secondary">{Object.entries(event.counts).map(([key,value])=>`${CONSOLE_COUNT_LABELS[key]} ${value.toLocaleString()}`).join(" · ")}</p>:null}
  </li>;
}

function RunEvents({ detail, loading, onNextPage, jobLabel }: {
  detail: ConsoleRunDetail;
  loading: boolean;
  onNextPage: () => void;
  jobLabel: JobLabel;
}) {
  const [errorsOnly, setErrorsOnly] = useState(false);
  const events = errorsOnly
    ? detail.events.filter(event => event.kind === "ExecutionStepFailureEvent" || event.kind === "RunFailureEvent")
    : detail.events;

  return <>
    <div className="flex flex-wrap items-center gap-2">
      <h4 className="font-semibold">처리 내역</h4>
      <DagsterLogControls errorsOnly={errorsOnly} onFilter={setErrorsOnly} />
      {detail.hasMore
        ? <Button size="sm" type="button" variant="outline" loading={loading} onClick={onNextPage}>다음 내역 보기</Button>
        : null}
    </div>
    {events.length
      ? <ol aria-label="안전한 처리 내역" className="flex flex-col gap-3">
          {eventRows(events).map(({ event, key }) => <RunEvent key={key} event={event} jobLabel={jobLabel} />)}
        </ol>
      : <p className="text-xs text-text-secondary">
          {errorsOnly ? "불러온 내역에 오류 로그가 없습니다." : "이 범위에는 표시할 처리 내역이 없습니다."}
        </p>}
  </>;
}

export function DagsterRunInspector({runId,jobLabel}:{runId:string;jobLabel:JobLabel}) {
  const {detail,loading,error,load}=useConsoleRun(runId);
  const [confirmCancel,setConfirmCancel]=useState(false);
  const action=useConsoleAction(()=>{setConfirmCancel(false);load();});
  return <section aria-label="실행 진행 상세" className="flex min-w-0 flex-col gap-3 text-sm">
    <RunToolbar detail={detail} loading={loading} action={action} confirmCancel={confirmCancel} onToggleCancel={()=>setConfirmCancel(value=>!value)} onRefresh={()=>load()} runId={runId}/>
    {confirmCancel?<CancelConfirmation busy={action.busy} onConfirm={()=>void action.perform({action:"cancel",runId})} onClose={()=>setConfirmCancel(false)}/>:null}
    {error?<Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>:null}
    <ConsoleActionNotice action={action}/>
    {detail?<>
      <RunMetrics detail={detail}/>
      <RunWarnings events={detail.events}/>
      <RunSteps detail={detail} jobLabel={jobLabel}/>
      <RunEvents detail={detail} loading={loading} onNextPage={()=>load(true)} jobLabel={jobLabel}/>
      <p className="text-xs text-text-secondary">같은 작업 다시 실행은 현재 기본 설정으로 새 실행을 만들며, 실패 단계만 재실행하는 기능은 아닙니다.</p>
    </>:loading?<p role="status" className="text-xs text-text-secondary">실행 상세 확인 중…</p>:null}
  </section>;
}
