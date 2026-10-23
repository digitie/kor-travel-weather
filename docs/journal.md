# Weather 작업 기록




## 2026-10-23 Map export 구현 준비의 프록시·증거 경계 보강 — 문서만

- 최신 main `d028ea72e440b36a44744c304264e20c9ad1f0f0`에서 후속 문서 branch를 만들었다.
  기존 catchall 프록시가 모든 경로에 서버 관리자 token을 주입하는 코드를 읽고 service prefix의
  명시 차단과 upstream fetch 0회 수용 조건을 설계에 추가했다. guard 구현은 아직 하지 않았다.
- accepted replay/canonical 시각과 4단계 구현 의존을 이어 문서화한다. 코드·schema·운영 변경이나
  테스트/CI 실행은 없으며 문서 PR의 2인 리뷰 후 CI 검토 없이 머지 여부를 판단한다.


## 2026-10-23 W5 관측 범위 사용자 선택 완료 — 구현은 미착수

- 사용자가 “기상청 실황도 관측으로 포함(권장)”을 승인했다. W-MAP-03은 **선택 문서화만 완료**로
  닫고, 기존 OBSERVED + 실제 관측 기반 KMA NOWCAST를 설계에 고정했다.
- main `b31c33d4d5188810d52dd102d4e59346b4aacc33`의 `_nowcast_value`를 확인했다.
  provider `python-kma-api`, dataset/weather_domain `kma_ultra_short_nowcast`, source_metric_key와
  category 동일, payload `obsr_value`(원천 `obsrValue`), observed_at/target_at, typed unit·기존
  결측/범위 검증을 조건으로 삼는다. W5 지표 T1H/RN1/REH/WSD/PTY를 구체화했다.
- 일반 미래 예보·KMA forecast dataset·외부 예측 NOWCAST를 무차별 포함하지 않는다. 실황에
  없는 SKY는 예보에서 채우지 않으며 강수 interval/coverage·reset·경계 분해의 보수 규약은 유지한다.
- W-MAP-04는 성공 receipt/projection W-MAP-02가 계속 선행이며 구현·초기 backfill·배포는
  미착수다. COW artifact도 실측 gate 전 미채택이다. 아래 설계 보존 절의 pending은 당시 상태다.
- 수정 범위는 설계/tasks/journal/resume 문서뿐이다. `[skip ci]`, CI 실행/검토/대기 없이 문서 PR의
  독립 리뷰 2인을 거쳐 머지 여부를 판단한다. 코드/schema/운영 변경과 기능 완료 주장은 없다.


## 2026-10-23 ADR-114 연계 서비스 export 설계 보존 — proposed

- Weather main `12c3a4adfcd7a030bedaf5346371a5684656ea71`에서 문서 branch를 만들고
  [설계 정본](design/map-service-exports.md)을 보존했다. 코드·schema·운영 변경과 구현 착수는 없다.
- Weather의 기존 인증은 require_admin/x-admin-token/AdminToken이며 새 service route에도 재사용한다.
  Transport 기존 header/CIDR/path·#103과 #104의 병합 순서를 구분하고 소유권을 분리했다.
- 독립 초안 적대 리뷰 2인의 지적을 반영했다: failed chunk 공개 차단, 성공 재관측 0건/alias 수용,
  성공 scope barrier·checkpoint vector·상태 집계, 응답시점 미래/weak ETag, TTL과 durable checkpoint,
  collector+publisher+PG 합산 비용 gate, 강수 interval 증거와 temporal partial/준비 상태 분리.
- 당시 판정은 조건부 PASS였고 W5 관측 범위는 사용자 답변 대기였다. 위 후속 선택으로
  관측 범위 문서화는 완료했으며 기능 구현은 여전히 미착수다. COW artifact 채택은 실측 gate를 통과해야 한다. 문서 머지가 구현 승인은 아니다.
- Weather W-MAP-01~05를 미착수로 등록했다. Transport T1~T5는 외부 연계 설계이며 이 저장소의
  구현/완료로 세지 않는다. 문서 PR은 `[skip ci]`, CI 실행/검토/대기 없이 독립 2인 문서 리뷰 후 판단한다.
  후속 기능 PR은 정상 CI·2인 리뷰·Map 응답 예시 전달·live E2E/API/UI와 배포 수용을 거쳐야 한다.


## 2026-10-21 외부 provider 긴 sweep run group 분리

- odroid 공용 Dagster는 CPU 포화로 `kortravelweather/run_group=external_weather`를 동시 1개로
  묶었다. 그 결과 2~4초에 끝나는 run(weatherstack·weatherbit·accuweather·wttr_in·tomorrow_io·
  visual_crossing)이 73~90분짜리 `open_meteo_weather_job` 뒤에서 최대 한 시간 기다렸다.
- 같은 태그 키에 값 `external_weather_slow`를 추가했다. 소속은 실행 측정이 아니라 코드의 요청
  수로 명시한다(`SLOW_EXTERNAL_PROVIDER_KEYS`): `open_meteo` 380곳 × 2 dataset = 760 요청,
  `openweathermap` 350 × 2 = 700 요청 + 1.25초 간격, `weatherapi` 150 × 2 = 300 요청. weatherapi도
  같은 종류의 sweep이라 fast에 두면 포화 CPU에서 sweep 둘이 겹친다. phase가 +7분으로 가장 앞서고
  (관측 약 9분), 늦어지면 openweathermap이 그 뒤에서 기다린다; 세 sweep 관측 합은 3시간 주기 안이다.
  나머지는 기본 location 상한이 없어 기본 설정에서 꺼져 있거나 credential 없이 skip하므로 빠르다.
  태그는 정의 시점 값이라 admin credential만으로 켜도 fast에 남는다 — 켜기 전에 이 집합에 넣는
  규칙을 runbook에 적었다.
- `deploy/dagster.yaml`에 `external_weather_slow` `limit: 1`을 추가했다(기존 `external_weather` 3은
  그대로). 공용 instance 설정은 Manager 쪽에서 같은 항목을 넣어야 효과가 난다. max_runtime 등 다른
  태그는 바꾸지 않았다. 배포는 하지 않았다.

## 2026-10-21 회수 sensor tick 예산·정체 경보(Map ADR-112 교훈)

- Common pin을 main `e746e4b81b9b62dd1a74ca90ba0a1204951c2885`로 바꿨다. 이전 pin `89291f3`은
  common main의 조상이 아니었다(squash 병합 전 브랜치 커밋). src 차이는 추가뿐이다
  (daemon pool·pg_errors·stall·`reconciliation_sensor`의 `budget_seconds`/`on_stall`).
- `weather_sync_recovery`는 tick마다 engine을 새로 만들고, 100행 page를 끝까지 돌며 행마다
  Dagster run을 조회했고, 예산·cursor·정체 경보가 없었다. 이제 새 모듈
  `kortravelweather_dagster.sync_recovery`가 `budget_seconds=40`·`on_stall`로 등록한다.
  keyset 순회는 예산 안에서 멈추고 위치를 code-server 메모리에 두어 다음 tick에 이어 간다
  (끝에 닿으면 처음부터; 위치를 잃어도 처음부터 돌 뿐이라 빠지는 행이 없다). engine은 DSN마다
  한 번 만들어 재사용한다. 트랜잭션별 lock 5초·statement 10초는 그대로다.
- 정체 판정: 두 회수 뒤에도 heartbeat가 lease(3시간)보다 오래된 running 행이 남으면
  `SyncRunsStuck`. `deploy/dagster.yaml`의 18시간 정체(프로세스가 살아 멈춘 run 10개가
  `max_concurrent_runs`를 채움)는 예전 sensor에서 매 tick "0건 회수" 성공이었다. 지금은
  heartbeat 정지 약 3시간 15분 뒤 `weather_sync_recovery.stalled` 오류 로그와
  `ktw_sync_recovery_stalled=1`(규칙 `KorTravelWeatherSyncRecoveryStalled`)을 남긴다. DB 장애·
  예산 초과도 같은 시계로 15분 뒤 경보한다. 실제 DB·실제 sensor 재현 시험이 고정한다.
- 운영 배포·daemon 재개는 하지 않았다(daemon OFF 유지).

## 2026-10-18 영속 주소 예약 후속 검토 완료

최종 제품7f549f1의 두 독립 SOURCE PASS·CI와 UI/지연 재시도 결과를
[후속 종결 기록](reviews/2026-10-18-native-address-final.md)에 보존했다.
Common41은 머지됐으며 공용 호스트·소비자 운영 배포는 후속이다. daemon OFF를 유지한다.
아래는 각 구현 시점의 이력이며 현재 결과를 소급 적용하지 않는다.


## 2026-10-18 영속 예약의 잠금 연결 장애 보강

현재 Common pin은 a68d0495acc38906c31b95b1d0db163bd8f96f5b다.
동일 요청 ID와 NOT_STARTED 제출 복구를 유지하며, PostgreSQL 잠금 연결이 끊긴 뒤
늦은 제출이 STARTED를 QUEUED로 되돌리지 않도록 native run 저장도 같은 연결에 묶는다.
이전 23e4775의 동시 제출 문제와 2704830의 잠금 연결 소실 반례는 이력으로 보존한다.
새 pin에서 실제 native 지연 요청·재시도 시험은 앱별 1건 통과했다. 전체 Python·UI
회귀와 두 독립 최종 리뷰는 진행 중이며, 이 기록은 운영 수용 완료가 아니다.
공용 webserver를 먼저 배포하고 소비자 Admin을 갱신한다. Daemon OFF를 유지한다.


## 2026-10-18 — 주소 갱신 날짜·예약 복구 후속

- 브라우저 UTC/미국 서부 시간대 PC·모바일 4건에서 날짜 표시 오류를 재현하고 Asia/Seoul 표시로 고정했다.
- 앱별 빌드·타입 검사·단위 211건 및 로컬 실제 API/native Dagster 연동 UI 17건 통과. 이 결과는 시간대 수정까지의 결과이며 운영 배포 증거가 아니다.
- 적대 리뷰에서 발견한 응답 유실 후 늦은 예약 중복 문제를 Common 영속 요청 ID mutation으로 연결 중이다. NOT_STARTED 실행은 재조회 완료로 처리하지 않고 제출 복구하며, 새 mutation 미지원 서버에서 기존 무작위 launch로 우회하지 않는다.
- 신규 예약 복구의 실제 SDK·재시작·지연 커밋 검증, 두 독립 리뷰, CI 및 운영 배포는 미완료다. daemon OFF를 유지한다.


## 2026-10-18 주소 확인 집계 후속 종결

Common40·Weather113은 두 독립 FULL SOURCE PASS/CI 후 머지했다. 제품2bd779의 전체661·CI661,
새 pin의 loopback UI13과 원천 보존을 [최종 원문·수용 범위](reviews/2026-10-18-address-summary-final.md)에
보존했다. 운영 migration/provider/배포·수집 CPU/RSS와 Transport 별도 gate는 후속이다.
아래 구현 당시 대기 문장은 당시 이력이며 Daemon OFF를 유지한다.

## 2026-10-18 — 비활성·이동 지점의 주소 확인 집계 보정

Transport86 r2에서 찾은 공통 MAX 집계 문제는 Weather 비활성 위치와 좌표 이동에도 해당한다.
실제 WeatherLocationRow를 갱신하는 3개 반례를 이전 Common c067에서 3 FAIL로 확인하고,
Common40의 522525a pin과 잠금을 갱신한 뒤 주소 회귀 4 PASS를 확인했다.
파생 과거 행을 보존하고 source 지역·KMA 격자·관리자 metadata 불변도 검증한다.
전체 CI·두 독립 리뷰·UI 및 운영 수용은 각각 별도다. 앞선111/112 이력을 덮지 않는다.

## 2026-10-18 주소 갱신 구현·로컬 검증

VWorld 우선·Kakao fallback, 위치별 달력 6개월 자동 갱신과 Admin 수동 실행을 구현했다. 두 앱은 같은 Common Python 엔진을 고정 commit으로 사용한다. [설계·설정 및 검증 범위](reverse-address-refresh.md)를 참조한다. 원천 주소·좌표는 보존한다.

로컬 실브라우저 최종 재검증은 앱별 13개(합계 26개) 통과했고 실제 인증·상태 API·Dagster 큐 예약·중복 차단·모바일/키보드·오류 복구를 포함한다. 제공자는 주입 fixture다. Python 전체 검증은 Common 509개, Weather 658개, Transport 838개 통과·환경 조건 10개 skip이다. Common39·Weather111은 두 독립 리뷰/CI 통과 후 머지했다. [최종 리뷰 기록](reviews/2026-10-18-address-refresh-final.md)을 참조한다. Transport 정식 gate는 별도이며 운영 migration·실제 제공자 실호출·배포·운영 UI 수용은 미실행이다. Daemon은 중지 상태를 유지한다.

## 2026-10-17 — 최신109와 남은 운영 수용 문서 정산

- 이 문서 PR 생성 전 main39aafe0/OPEN0, #108/#109 exact head·MERGED·PR/main CI 성공을 공개 GitHub 읽기로 확인했다. 코드/config/tests 변경0.
- root 공개 safe의 CS71ff/139f 배포·API8d/d48c 유지, UI36PASS, native run8d87945 complete_snapshot/영속8필드1Observation, daemonOFF·queue3 보존을 [감사](audits/2026-10-17-remaining-work.md)에 구분했다.
- 11:58 RO의 health90/90·77/16, Weather checkout probe 부재와 대체 source 경로/전체 module 미확인 한계를 기록했다. source 소실이나 미배포로 단정하지 않았다.
- [tasks](tasks.md)/[resume](resume.md)를 새 현행 인덱스로 추가했다. 기존 deploy/n150의 계획/실패/체크와 vendored provider 원장은 보존했다.
- 지역 freshness·운영복구·장시간 CPU/RSS·consumer/정식 build 경로 수용은 잔여다. Manager source/runtime probe drift는 공통 소유 후속이며 Weather에 Geo token 소비를 추가하지 않는다.
- 사용자 승인 문서 commit [skip ci]. 링크·diff·사실 대조만 수행, full suite/이미지 build/새 live UI/서비스 제어0. 독립2리뷰/PR merge는 root가 맡는다.


## 2026-10-18 수집 카탈로그 CPU/할당 peak 개선

metadata 포함 카탈로그를 ORM 대신 DTO 컬럼 projection으로 조회한다. 실제 DB→DTO→target
1,450행 로컬 비교와 한계를 [측정 기록](collector-catalog-cpu-20261018.md)에 기록했다.
두 명의 독립 소스 리뷰·exact-head CI·ARM64 API/CS native build·r4 prepare는 통과했다.
ARM 격리 fixture 10표본에서 CPU 중앙값 약 12.2%, Python 할당 peak 약 16.0% 감소했고
RSS의 실질적 저감은 주장하지 않는다. API/CS 배포065661ca PASS와 배포 후 WSL
Chromium1243 UI36 PASS/exit0를 확인했다. 이 수용 기록은 PR #117에 포함한다.
실제 provider 전체 수집 CPU/RSS·장시간 안정성·Kakao 실제 fallback/주소 전체 sweep 및
Track 1/2의 미검증 수용은 별도로 남긴다. 이전 pending 문장은 각 기록 시점의 이력이다.
Dagster daemon OFF를 유지한다.


## 2026-10-18 PR #118 비특보 예보 배치 메모리 개선 — 배포·UI 수용

제품 a8949778은 다음 위치가 상한을 넘기기 전에 기존 묶음을 게시하고,
격자·중기 예보의 staging·게시 상한을2500으로 분리했다. 특보 staging과
다른 provider의 공용 게시 상한은5000을 유지하며 한 위치는 나누지 않는다.
격리 PostgreSQL 전체667개·Ruff·최종 Python/frontend CI·독립 소스 리뷰2건이 통과했다.

Odroid의 고정 합성 입력(792fact×10위치)에서 기준/후보 각3회 비교를 두 측정군으로
수행했다. PostgreSQL을 포함한 측정군은 worker RSS 중앙값 약14.99% 감소,
합산 CPU 약1.29% 감소, 처리 시간 약2.02% 증가였다. CPU는 작은 표본에서 동등
수준으로 판단한다. SQL은107→125이며 전체 provider·전체 job·호스트의 개선율은 아니다.
실측은6421a4b에 상수2500을 주입한 결과이고, 실제 a8949778 기본값은 별도로
rollback+alias 및 commit 후 응답 오류의 복구2건을 통과했다.
각각2376+5544,4752+3168건으로 총7920건을 복구하고 정상 기준의 원천·fact·current
의미 digest 일치와 다음 재수집0건을 확인했다.

[상세 측정·원시 증거·복구 및 한계](reviews/collector-bounded-stage-20261018.md)를 따른다.
이번 제품의 ARM64 API/CS native 이미지 빌드와 각 이미지의 설치 소스67파일 검증이
통과했다. 운영 전환 f5cc3880에서 API/CS READY·기존 큐와 env 파일 보존·버전 외 runtime
환경 동일·daemon OFF를 확인했다. 배포 후 WSL Chromium1243 live UI는36/36 PASS,
exit0이며 Weather 세 역할의 실행 상태가 유지됐다.
[배포·UI 수용 증거](reviews/collector-bounded-stage-deployment-20261018.json)를 따른다.
PR #118은 아직 머지 전이며 daemon OFF를 유지한다. 전체 수집·24시간 OOM/swap·자동 복구·Track1/2의 미검증
운영 수용을 완료로 바꾸지 않는다. PR #117의 RSS 미입증 문장은 당시
카탈로그 조회 범위의 기록이며 이번 격자 저장 실측과 구분한다.

## 2026-10-19 지역 관측 제외 사유 보강

W-OPS-01 최근 native 실행·run-source 연결을 읽기 전용으로 대조했다. 유효값 없는 지점의 skipped_without_values를 추가해 좌표 부재와 구분한다. 격리 PostgreSQL 전체 668개·frontend 214개·type-check·Ruff 및 CI39170492571이 통과했다. 두 독립 코드 리뷰의 열린 지적은 0개다. 공개 좌표의 실제 VWorld 호출과 오류 주입 후 실제 Kakao fallback도 통과했다. 운영 API/CS 전환5c8db5f8과 배포 후 UI36이 통과했으며 daemon OFF를 유지한다.

현재 상태 정정: 앞선 #118의 머지 전 표시는 당시 이력이다. #118은 4757df0c0b5090b22142535c623b77abc657f382로 머지됐고 a8949778 제품 배포와 UI36을 통과했다.


## 2026-10-19 지역 수집 수동 수용 초안

#119 이후 제품683e446d를 그대로 사용한 두 asset 제한 수집의 기록을 분리했다. metrics 사전 probe 실패와 실제 DB6403건 저장 후 Dagster 결과 경로 실패를 구분해 보존하고, 소유 metrics·NVMe state 경로와 시간 역전/정상 noop 감사 반례를 보강했다. 세 번째 실행 결과는 아직 정산 전이며 daemon OFF를 유지한다.


### 지역 수동 수용 최종 정산

앞선 초안 이후 r3 두 asset native SUCCESS·DB complete를 확인했다. 연결6403건 중
휴게소1147건은 정상 재수집으로 신규0, 산악5256건은 신규 저장이었다.
좌표59·빈값16 제외를 직접 기록하고 중복·빈 raw0을 대조했다. worker CPU66.304초와
절대 cgroup peak261.15MiB는 단일 수동 실행값이며 성능 개선율을 주장하지 않는다.
수집 후 UI36도 PASS, 제품683e446d·daemon OFF·큐3개는 유지됐다.
실패 r1/r2와 기존 DB 저장은 보존했다. 전체 주소 수동 갱신 및 daemon 자동 운영·
장시간 전체 수집은 별도 수용이며 추가 성능 튜닝보다 남은 Track 수용을 우선한다.


## 2026-10-19 전체 주소 수동 cycle 수용 완료

전체 1,450개를 30개 native SUCCESS 배치로 갱신했다. DB 감사에서 원천 SHA 불변,
유효 주소 1,450개, due/pending/failed 0, cycle owner 해제를 확인했다. desktop/mobile
완료 UI 6개 검사도 통과했다. 첫 50개 판정기의 INFO 로그 부재는 기록에 남기고,
이미 저장된 행은 재호출하지 않았다. 상세 결과와 daemon 재가동 뒤의 별도 queued/started
관측은 [전체 주소 수동 갱신 수용](audits/2026-10-19-address-full-cycle.md)에 기록했다.
예약 작업과 장기 자동복구는 이번 수용 범위가 아니다.
