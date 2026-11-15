import { describe, expect, it, vi } from "vitest";
import { DAGSTER_LOCATION_NAME } from "./dagster-scope";
import { consoleRequestId, handleConsoleRequest, parseConsoleRequest, sanitizeConsoleEvents, sanitizeConsoleRun } from "./dagster-console-server";

const id="11111111-1111-4111-8111-111111111111", other="22222222-2222-4222-8222-222222222222", requestId="33333333-3333-4333-8333-333333333333";
const tag=()=>({key:String(DAGSTER_LOCATION_NAME)==="kor-travel-transport"?"dagster/code_location":".dagster/repository",value:String(DAGSTER_LOCATION_NAME)==="kor-travel-transport"?DAGSTER_LOCATION_NAME:`__repository__@${DAGSTER_LOCATION_NAME}`});
const rows=(values:unknown[]=[])=>({runsOrError:{__typename:"Runs",results:values}});
const run=(status="STARTED")=>({runId:id,jobName:"fixture_job",status,creationTime:100,startTime:110,endTime:null,canTerminate:true,hasTerminatePermission:true,hasReExecutePermission:true,stats:{stepsSucceeded:1,stepsFailed:0,materializations:1,enqueuedTime:101},executionPlan:{steps:[{key:"fetch"},{key:"publish"}]},stepStats:[{stepKey:"fetch",status:"SUCCESS",startTime:110,endTime:111,attempts:[{startTime:110,endTime:111}]}]});
function fixture() {
  let prior:unknown[]=[];let active:unknown[]=[];let nextRun=run();let eventFails=false;let launchFails=false;let launchForeign=false;
  const catalog={repositoryOrError:{__typename:"Repository",jobs:[{name:"fixture_job",hasLaunchExecutionPermission:true},{name:"__ASSET_JOB",hasLaunchExecutionPermission:true},{name:"private_job",hasLaunchExecutionPermission:false}],schedules:[{name:"fixture_schedule",pipelineName:"fixture_job",cronSchedule:"*/5 * * * *",defaultStatus:"RUNNING",scheduleState:{id:"owned-schedule-id",status:"STOPPED",hasStartPermission:true,hasStopPermission:true}}],sensors:[{name:"fixture_sensor",defaultStatus:"STOPPED",sensorState:{id:"owned-sensor-id",status:"RUNNING",hasStartPermission:true,hasStopPermission:true}}]},__schema:{mutationType:{fields:["launchIdempotentRun","terminateRun","startSchedule","stopRunningSchedule","startSensor","stopSensor"].map(name=>({name}))}}};
  const graphql=vi.fn(async(query:string,variables:Record<string,unknown>):Promise<Record<string,unknown>>=>{
    if(query.includes("AdminDagsterCatalog"))return catalog;
    if(query.includes("AdminDagsterRequest"))return rows(prior);
    if(query.includes("AdminDagsterActive"))return rows(active);
    if(query.includes("AdminDagsterEvents")){if(eventFails)throw new Error("private-provider-url");return rows([{...nextRun,eventConnection:{events:[],hasMore:false,cursor:"done"}}]);}
    if(query.includes("query AdminDagsterRun")||query.includes("AdminDagsterOwnedRun"))return rows(nextRun?[nextRun]:[]);
    if(query.includes("AdminDagsterLaunch")){
      if(launchFails)throw new Error("private-upstream-message");
      return {result:{__typename:"IdempotentLaunchRunSuccess",replayed:false,run:{runId:other,status:"QUEUED",tags:launchForeign?[{key:"foreign",value:"foreign"}]:[tag()]}}};
    }
    if(query.includes("AdminDagsterCancel"))return {result:{__typename:"TerminateRunSuccess",run:{runId:id,status:"CANCELING"}}};
    if(query.includes("AdminDagsterStartSchedule")||query.includes("AdminDagsterStopSchedule"))return {result:{__typename:"ScheduleStateResult",scheduleState:{status:query.includes("Start")?"RUNNING":"STOPPED"}}};
    if(query.includes("AdminDagsterStartSensor"))return {result:{__typename:"Sensor",sensorState:{status:"RUNNING"}}};
    if(query.includes("AdminDagsterStopSensor"))return {result:{__typename:"StopSensorMutationResult",instigationState:{status:"STOPPED"}}};
    throw new Error(`unexpected test query ${variables}`);
  });
  return {graphql,catalog,setPrior:(value:unknown[])=>{prior=value;},setActive:(value:unknown[])=>{active=value;},setRun:(value:ReturnType<typeof run>|null)=>{nextRun=value as ReturnType<typeof run>;},eventFailure:()=>{eventFails=true;},launchFailure:()=>{launchFails=true;},foreignLaunch:()=>{launchForeign=true;}};
}

describe("관리자 고정 명령·범위",()=>{
  it.each([
    {operation:"catalog",query:"mutation{}"},{operation:"detail",runId:id,filter:{tags:[]}},
    {operation:"action",action:"launch",name:"fixture_job",requestId,runConfigData:{}},
    {operation:"action",action:"cancel",runId:"foreign-id",requestId},
    {operation:"action",action:"stopSensor",name:"../../foreign",requestId},
    {operation:"requestStatus",requestId:"invalid"},{operation:"detail",runId:id,cursor:"x".repeat(513)},
  ])("브라우저의 임의 query/config/scope/잘못된 식별자를 거부한다",value=>{expect(parseConsoleRequest(value)).toBeNull();});
  it("요청 UUID는 location별로 결정적으로 분리한다",()=>{
    expect(consoleRequestId(requestId)).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(consoleRequestId(requestId.toUpperCase())).toBe(consoleRequestId(requestId));expect(consoleRequestId(requestId)).not.toBe(requestId);
  });
  it("실행 가능한 명시 job과 서버 권한만 catalog로 보낸다",async()=>{
    const f=fixture();const result=await handleConsoleRequest({operation:"catalog"},f.graphql);
    expect(result).toMatchObject({jobs:["fixture_job"],capabilities:{launch:true,cancel:true,retry:true,schedule:true,sensor:true}});
    expect(f.graphql.mock.calls[0][1]).toEqual({selector:{repositoryLocationName:DAGSTER_LOCATION_NAME,repositoryName:"__repository__"}});
    expect(JSON.stringify(result)).not.toContain("owned-schedule-id");
  });
  it("scope에서 찾지 못한 run은 상세·취소를 조회/실행하지 않는다",async()=>{
    const f=fixture();f.setRun(null);
    await expect(handleConsoleRequest({operation:"detail",runId:id},f.graphql)).rejects.toMatchObject({status:404});
    await expect(handleConsoleRequest({operation:"action",action:"cancel",runId:id,requestId},f.graphql)).rejects.toMatchObject({status:404});
    expect(f.graphql.mock.calls.some(([query])=>/^\s*mutation\b/.test(query))).toBe(false);
    for(const [query,variables]of f.graphql.mock.calls.filter(([query])=>query.includes("OwnedRun")||query.includes("query AdminDagsterRun"))){expect(query).toContain("$filter");expect(variables.filter).toMatchObject({runIds:[id],tags:[tag()]});}
  });
});

describe("실행 상세·자료 경고",()=>{
  it.each(["QUEUED","STARTED","SUCCESS","FAILURE","CANCELED"])("%s도 현재 단계와 시간을 조회한다",async state=>{
    const f=fixture();f.setRun(run(state));const result=await handleConsoleRequest({operation:"detail",runId:id},f.graphql);
    expect(result).toMatchObject({runId:id,status:state,steps:[{key:"fetch",status:"SUCCESS",attempts:1},{key:"publish",status:"NOT_STARTED"}]});
    expect(f.graphql).toHaveBeenCalledTimes(2);
  });
  it("이벤트 실패는 이미 읽은 단계 상태를 실패시키지 않는다",async()=>{
    const f=fixture();f.eventFailure();const result=await handleConsoleRequest({operation:"detail",runId:id},f.graphql);
    expect(result).toMatchObject({stepsSucceeded:1,events:[{kind:"UNAVAILABLE",warning:true}]});expect(JSON.stringify(result)).not.toContain("private-provider-url");
  });
  it("진행 상태만 갱신하면 이벤트를 다시 전수 조회하지 않는다",async()=>{
    const f=fixture();await handleConsoleRequest({operation:"detail",runId:id,includeEvents:false},f.graphql);expect(f.graphql).toHaveBeenCalledTimes(1);
  });
  it("부분완료·캐시 지연·안전한 숫자를 표시하고 원문은 제거한다",()=>{
    const secret="fixture-console-private-value";
    const events=sanitizeConsoleEvents([{__typename:"ExecutionStepOutputEvent",timestamp:"1780000000000",stepKey:"publish",message:secret,metadataEntries:[
      {label:"status",text:"partial_success"},{label:"collection_outcome",text:"partial"},{label:"cache_refresh_failed",boolValue:true},{label:"previous_data_preserved",boolValue:true},{label:"snapshot_count",intValue:100},{label:"raw",text:secret},{label:"error_code",text:"DERIVED_CACHE_DEFERRED"},
    ]},{__typename:"LogMessageEvent",message:`kma_collection_progress run_id=fixture counts={'fetch_ms': 12, 'published_values': 200} ${secret}`,timestamp:"1780000000000"}]);
    expect(events[0]).toMatchObject({warning:true,counts:{snapshot_count:100}});expect(events[0].detail).toContain("정상 저장분 보존");expect(events[0].detail).toContain("캐시");
    expect(events[1]).toMatchObject({title:"KMA 처리 진행",counts:{fetch_ms:12,published_values:200}});
    expect(JSON.stringify(events)).not.toContain(secret);
  });
  it("실패 원인도 고정 코드만 남기고 임의 로그는 전달하지 않는다",()=>{
    const secret="fixture-private-message";
    const result=sanitizeConsoleEvents([{__typename:"ExecutionStepFailureEvent",message:secret,error:{className:"RuntimeError",message:`${secret} https://private.invalid`},failureMetadata:{metadataEntries:[{label:"error_code",text:"PROVIDER_AUTH"}]}},{__typename:"LogMessageEvent",message:secret}]);
    expect(result).toHaveLength(1);expect(result[0].detail).toContain("PROVIDER_AUTH");expect(JSON.stringify(result)).not.toContain(secret);
  });
  it("실행 취소·재실행 가능 여부는 native 권한과 상태를 따른다",()=>{
    expect(sanitizeConsoleRun({...run(),hasTerminatePermission:false}).canCancel).toBe(false);
    expect(sanitizeConsoleRun(run("CANCELING")).canCancel).toBe(false);
    expect(sanitizeConsoleRun(run("SUCCESS")).canRetry).toBe(false);
    expect(sanitizeConsoleRun(run("FAILURE")).canRetry).toBe(true);
  });
});

describe("관리자 변경 요청",()=>{
  it("새 실행은 fixed selector/default config와 namespace UUID만 보낸다",async()=>{
    const f=fixture();const result=await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql);
    expect(result).toMatchObject({state:"accepted",runId:other,requestId});
    const [,variables]=f.graphql.mock.calls.find(([query])=>query.includes("mutation AdminDagsterLaunch"))!;
    expect(variables).toMatchObject({requestId:consoleRequestId(requestId),executionParams:{selector:{pipelineName:"fixture_job",repositoryLocationName:DAGSTER_LOCATION_NAME,repositoryName:"__repository__"},runConfigData:{}}});
  });
  it("이전 요청은 다시 launch하지 않는다",async()=>{
    const f=fixture();f.setPrior([{runId:other,jobName:"fixture_job",status:"QUEUED"}]);
    expect(await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql)).toMatchObject({state:"accepted",replayed:true});
    expect(f.graphql.mock.calls.some(([query])=>/^\s*mutation\b/.test(query))).toBe(false);
  });
  it("예약 전 단계의 동일 요청만 같은 UUID로 복구한다",async()=>{
    const f=fixture();f.setPrior([{runId:other,jobName:"fixture_job",status:"NOT_STARTED"}]);f.setActive([{runId:other,jobName:"fixture_job",status:"NOT_STARTED"}]);
    const input={operation:"action" as const,action:"launch" as const,name:"fixture_job",requestId};
    expect(await handleConsoleRequest({operation:"requestStatus",requestId},f.graphql)).toMatchObject({state:"unknown",runId:other});
    expect(await handleConsoleRequest(input,f.graphql)).toMatchObject({state:"accepted"});
    const calls=f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query));expect(calls).toHaveLength(1);expect(calls[0][1].requestId).toBe(consoleRequestId(requestId));
  });
  it("진행 중인 동일 작업은 기존 실행으로 안내한다",async()=>{
    const f=fixture();f.setActive([{runId:other,jobName:"fixture_job",status:"STARTED"}]);
    expect(await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql)).toMatchObject({state:"blocked",runId:other});
    expect(f.graphql.mock.calls.some(([query])=>/^\s*mutation\b/.test(query))).toBe(false);
  });
  it("불명확한 mutation 결과는 자동 재전송하지 않는다",async()=>{
    const f=fixture();f.launchFailure();const result=await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql);
    expect(result).toMatchObject({state:"unknown",requestId});expect(JSON.stringify(result)).not.toContain("private-upstream-message");expect(f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query))).toHaveLength(1);
  });
  it("다른 location의 native 반환 run을 노출하지 않는다",async()=>{
    const f=fixture();f.foreignLaunch();const result=await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql);
    expect(result).toMatchObject({state:"unknown"});expect(result).not.toHaveProperty("runId");
  });
  it("취소는 SAFE_TERMINATE만 쓰며 종료·취소 중에는 재요청하지 않는다",async()=>{
    const f=fixture();expect(await handleConsoleRequest({operation:"action",action:"cancel",runId:id,requestId},f.graphql)).toMatchObject({state:"accepted"});
    expect(f.graphql.mock.calls.find(([query])=>/^\s*mutation\b/.test(query))![0]).toContain("SAFE_TERMINATE");
    for(const state of ["SUCCESS","CANCELING"]){f.graphql.mockClear();f.setRun(run(state));expect(await handleConsoleRequest({operation:"action",action:"cancel",runId:id,requestId},f.graphql)).toMatchObject({state:"unchanged"});expect(f.graphql.mock.calls.some(([query])=>/^\s*mutation\b/.test(query))).toBe(false);}
  });
  it("같은 작업 재실행은 옛 config를 복사하거나 실패 단계 재개로 가장하지 않는다",async()=>{
    const f=fixture();f.setRun(run("FAILURE"));expect(await handleConsoleRequest({operation:"action",action:"retry",runId:id,requestId},f.graphql)).toMatchObject({state:"accepted"});
    const [,variables]=f.graphql.mock.calls.find(([query])=>query.includes("mutation AdminDagsterLaunch"))!;
    expect(variables.executionParams).toMatchObject({runConfigData:{},executionMetadata:{tags:expect.arrayContaining([{key:"kortravel/admin-retry-of",value:id}])}});
    expect(JSON.stringify(f.graphql.mock.calls)).not.toContain("runConfigYaml");
  });
  it("스케줄·센서 식별자는 서버가 읽은 자기 repository 값만 쓴다",async()=>{
    const f=fixture();expect(await handleConsoleRequest({operation:"action",action:"startSchedule",name:"fixture_schedule",requestId},f.graphql)).toMatchObject({state:"accepted"});
    expect(f.graphql.mock.calls.at(-1)![1]).toEqual({selector:{repositoryLocationName:DAGSTER_LOCATION_NAME,repositoryName:"__repository__",scheduleName:"fixture_schedule"}});
    expect(await handleConsoleRequest({operation:"action",action:"stopSensor",name:"fixture_sensor",requestId},f.graphql)).toMatchObject({state:"accepted"});
    expect(f.graphql.mock.calls.at(-1)![1]).toEqual({id:"owned-sensor-id"});
  });
});


it("코드 위치 카탈로그가 잠시 없어도 소유 실행의 정상 취소는 제공한다",async()=>{
 const f=fixture();Object.assign(f.catalog.repositoryOrError,{__typename:"PythonError"});
 expect(await handleConsoleRequest({operation:"action",action:"cancel",runId:id,requestId},f.graphql)).toMatchObject({state:"accepted",runId:id});
 expect(f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query))).toHaveLength(1);
});

it("집계 조회가 불명확하면 실제 0건으로 표시하지 않는다",()=>{expect(sanitizeConsoleRun({...run(),stats:{__typename:"PythonError"}})).toMatchObject({stepsSucceeded:null,stepsFailed:null,materializations:null});});

it.each([{action:"cancel",runId:id},{action:"startSchedule",name:"fixture_schedule"},{action:"stopSensor",name:"fixture_sensor"}] as const)("$action의 native 내부오류는 확정 거절로 꾸미지 않는다",async action=>{
 const f=fixture(),original=f.graphql.getMockImplementation()!;
 f.graphql.mockImplementation(async(query,variables)=>/^\s*mutation\b/.test(query)?{result:{__typename:"PythonError"}}:original(query,variables));
 expect(await handleConsoleRequest({operation:"action",requestId,...action},f.graphql)).toMatchObject({state:"unknown"});
 expect(f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query))).toHaveLength(1);
});

it.each(["InvalidStepError","InvalidOutputError","RunConfigValidationInvalid","PipelineNotFoundError","UnauthorizedError","InvalidSubsetError","PresetNotFoundError","ConflictingExecutionParamsError","NoModeProvidedError"])("실제 union의 %s는 원문 없이 확정 거절로 표시한다",async type=>{
 const f=fixture(),original=f.graphql.getMockImplementation()!;
 f.graphql.mockImplementation(async(query,variables)=>query.includes("AdminDagsterLaunch")?{result:{__typename:type,message:"private-canary"}}:original(query,variables));
 const result=await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql);
 expect(result).toMatchObject({state:"rejected",requestId});expect(JSON.stringify(result)).not.toContain("private-canary");
 expect(f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query))).toHaveLength(1);
});
it.each(["PythonError","RunConflict","UnverifiedType"])("%s는 미예약으로 단정하지 않고 UNKNOWN을 유지한다",async type=>{
 const f=fixture(),original=f.graphql.getMockImplementation()!;
 f.graphql.mockImplementation(async(query,variables)=>query.includes("AdminDagsterLaunch")?{result:{__typename:type,message:"private-canary"}}:original(query,variables));
 expect(await handleConsoleRequest({operation:"action",action:"launch",name:"fixture_job",requestId},f.graphql)).toMatchObject({state:"unknown",requestId});
 expect(f.graphql.mock.calls.filter(([query])=>/^\s*mutation\b/.test(query))).toHaveLength(1);
});
