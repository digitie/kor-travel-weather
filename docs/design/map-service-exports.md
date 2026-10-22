# ADR-114 연계 Weather·Transport Map 서비스 export 설계

- 상태: **proposed, 구현 미착수**. 문서만 저장하며 코드·schema·운영 변경은 없다.
- 초안 독립 적대 리뷰 2인: 조건부 PASS. W5 관측 포함 범위는 사용자 답변 pending이며,
  COW artifact 채택은 구현 전 실측 exit gate 통과가 조건이다. 문서 머지가 구현 승인은 아니다.
- 보존 기준 Weather main: `12c3a4adfcd7a030bedaf5346371a5684656ea71`.
- 작성일: 2026-10-23.
- Map 정본: ADR-114(Valkey 현재/예보 + Weather 요약, PinVi는 Map REST).
- 입력 정본: coordinator가 전달한 W1~W5/T1~T5. 최신 사용자 명시 W5의 KST 3개 slot이
  Map ADR-114의 기존 오전/오후 요약 표현보다 우선한다. 문서 이행 때 양쪽을 함께 맞춘다.
- 기존 history·통계·dashboard·공개/admin API를 보존하는 additive 변경이다.
- 본 초안의 새 표/endpoint/query는 모두 제안이다. 기존 구현인 것처럼 표시하지 않는다.

## 1. 실제 코드에서 확인한 경계

### Weather 인증·경로

`packages/kor-travel-weather-api/src/kortravelweather_api/auth.py::require_admin`은
`x-admin-token`을 읽어 `WeatherSettings.require_admin_token()`과 상수 시간 비교한다.
설정은 `KOR_TRAVEL_WEATHER_ADMIN_TOKEN`이다. production은 누락/약한 token에서 기동을
거부하고 인증 실패는 401이다. 개발 환경의 token 미설정 허용은 기존 정책이며 이 작업에서
인증 정책을 임의로 바꾸지 않는다. OpenAPI scheme은 `AdminToken`이다.

현재 Weather에는 별도의 ServiceToken header·require_service·`/v1/service/exports`가 없다.
기존 `/v1/weather/{locations,locations/{id}/latest,locations/{id}/forecast,markers,...}`는
공개 read이고 `/v1/admin/*`는 require_admin을 쓴다. **새 요구의 경로는
`/v1/service/exports/{locations,current,forecasts,status,daily-summaries}`로 추가하되,
기존 require_admin / x-admin-token / AdminToken을 그대로 재사용한다.** 이름만 Transport와
같은 서비스 경로이며 실제 header는 Weather의 기존 관리자 header다. 신규 bearer/header/토큰
설정·서명키를 만들지 않는다. 이 불일치는 PR·Map 응답 예시에 명시하고 coordinator가 필요하면
사용자 선택을 확인한다. Weather 웹 프록시로 token을 브라우저에 노출하지 않는다.

현재 `app.py::custom_openapi`는 `/v1/admin/`만 AdminToken security/401을 명시하므로
새 service 경로에도 같은 scheme/에러 선언을 추가해야 한다. router dependency만 적용하고
OpenAPI에는 공개로 남기는 실수를 막는다.

`repository.py::WeatherCurrentValueRow`는 논리 point의 최신 fact `(value_id, known_at)`를
가리킨다. `_current_value_models_many`는 current projection부터 고르고 정확한 복합 PK로
fact를 읽는다. fact 전체 history를 정렬/집계해서 export하지 않는다. `known_at`은 원천 fact의
인식 시각이지 HTTP snapshot ID·커밋 순서나 수집 성공 여부가 아니다.

### Transport 인증·경로

`backend/app/service_exports.py`의 기존 `/v1/service/exports/*`를 확장한다.
`X-Kor-Travel-Transport-Service-Token` + 설정 `TRANSPORT_SERVICE_EXPORT_TOKEN`(32자 이상,
상수 시간 비교) + 실제 peer의 `SERVICE_EXPORT_ALLOWED_CLIENTS_CSV`를 유지한다. 실패는
기존처럼 404이며 Host/X-Forwarded-For를 신뢰해 우회하지 않는다. 공개 웹 프록시 allowlist나
브라우저에 서비스 token을 추가하지 않는다.

기존 fuel-stations/rest-areas/rest-area-fuel-prices/highway-incidents/active/airports가 있다.
각 요청의 `_begin_snapshot`은 최초 SQL 전에 REPEATABLE READ를 설정한다. **요청 간 snapshot은
고정되지 않는다.** 기존 20자 숫자 cursor·MAX_PAGE_SIZE=1000 계약은 유지하고 새 snapshot
pagination을 가산 파라미터/메타로 추가한다(새 경로의 limit 상한은 최대 5000, 기본 500).
기존 cursor를 별도 안내 없이 새 opaque JSON으로 바꾸지 않는다.

PR #103은 main `35c31ae79f930a683ebbaa5660d44d880929ecae`에 포함됐다. timeout 12600초와
export 참조 데이터 24시간 마지막 성공 유예를 회귀시키지 않는다. PR #104의 병행 저장·공개/집계
변경과 겹치는 service_exports/transport_collection은 **#103 main 및 #104 최종 머지 이후**를
기준으로 구현·재검토한다. 별도 price-history 초안 `1ebb50c`는 untested이고 OpenAPI 미커밋이라
완료 코드로 간주하거나 그대로 배포하지 않는다.

## 2. 공통 DTO·빈 응답·신선도

가산 공통 metadata는 `generated_at`, `source_collected_at`,
`source_status: ok|stale|failed`, `item_count`다. `generated_at`은 **각 응답을 생성한 현재 시각**이다. 고정된 export generation 생성 시각은
별도 `snapshot_generated_at`, 내용 선택 기준은 `as_of`로 제공한다. `source_collected_at`은 실제 마지막 성공
원천 수집 시각이며 API 읽기 시각/Map 저장 시각으로 갱신하지 않는다. `item_count`는 현재
페이지의 item 수, `total_item_count`는 선택 generation 전체 수(있을 때 가산)로 구분한다.
`next_cursor/has_more`, `snapshot_id`, `as_of`, generation/health revision을 함께 제공한다.

- 마지막 시도가 실패해도 마지막 성공 데이터는 보존한다. 재확인 불가 empty/급감 응답이나
  마지막 성공 이후의 실패에서 이전 성공을 제공할 수 있으면 source_status=stale로 명시한다.
  `failed`는 초도 성공이 전혀 없거나 export 자체 비가용 등 응답을 정상 구성할 수 없는 실패로
  구분하고, 503 problem과 같은 status metadata/header를 계약에 고정한다. 정상 최신인 척하지 않는다.
- 성공이 stale age를 넘으면 stale. 정상 최신 성공일 때만 ok다. 상세 오류 원문/비밀은 내지 않는다.
- empty/급감한 원천 응답은 수집 계층에서 제한된 재조회로 확인한다. 재확인 실패 시 기존 데이터와
  마지막 성공 시각을 유지하고 **source_status=stale**로 표시한다. 2026-10-23 06:05KST KREX empty가 Map
  106개 사건을 닫은 사례를 회귀 fixture로 삼는다.
- 실제로 정상 종료된 빈 집합(예: 활성 돌발 0건)은 provider 의미·완전성·재확인 증거로 입증될 때만
  성공 empty generation으로 공개한다. 비율 하한에 걸렸다는 이유로 무조건 영구 정지하거나
  단순히 200 empty라는 이유로 Map이 전량 퇴역하지 않는다. 원천 확인이 불가능하면 이전 세대 유지다.
- **기존 Transport ADR-013 export 소비자는 200=current authoritative 집합**으로 해석한다.
  따라서 기존 endpoint의 503 정책을 stale items 200으로 조용히 바꾸지 않는다. 동일 path에 새
  가산 `snapshot=true` opt-in을 둔 Map Valkey 소비 계약에서는 last-success+status metadata를
  200으로 제공하며, 기존 non-opt-in 응답/실패 semantics는 그대로 둔다. Map 클라이언트가
  source_status != ok인 데이터를 새 정상 generation으로 publish하지 않도록 먼저 구현한다.
  이 opt-in은 기존 response failure semantics를 보존하는 계약 확장일 뿐 **T5 수집 guard의
  적용 예외가 아니다**. T1/T2/T3 및 기존 export가 사용하는 같은 collector 저장 경로에도 empty/
  급감 재확인·마지막 성공 유지 guard를 기본 적용한다. legacy caller도 빈 집합으로 파괴되지 않는다.
- 초도 성공이 없는 경우 503 problem이다. failed/stale는 304 조건을 따로 계산하며 status가 바뀌면
  이전 데이터 content가 같아도 200 metadata를 보내야 한다.

## 3. 묶음 cursor·ETag·304·snapshot 정합성

### 3.1 API 호출마다 전체 재생성하지 않는다

current pointer는 mutable이다. `known_at <= cutoff` 조건만 넣고 여러 HTTP 페이지를 읽으면
그 사이 새 pointer가 이전 fact를 대체해 누락이 생긴다. 긴 exported PG snapshot을 HTTP 요청 사이
유지하거나 전체 history로 과거 pointer를 복원하는 방법도 채택하지 않는다.

**실패 run의 chunk가 commit됐다는 사실만으로 export publisher가 이를 공개해서는 안 된다.**
Weather 현재 pointer는 run의 중간 chunk commit을 이미 볼 수 있으며 실패 run 이전 성공으로
되돌리는 기능이 없다. 그래서 mutable current pointer에서 dirty group을 나중에 읽어 묶는 방법은
성공 snapshot 보장의 근거가 될 수 없다.

권장 경로는 collector의 기존 bounded 정규화·게시 흐름에서 **attempt 전용 candidate artifact**를
stream serialize하는 것이다. 원천 raw를 복제하지 않고 정규화 결과/정확한 `(value_id, known_at)`/
source_record_key/run/attempt·logical point를 기록한다. NVMe 파일은 원자 rename 후 checksum을
확정하고, 해당 chunk가 실제 commit됐음을 증명하는 manifest row/commit receipt를 **그 chunk
DB transaction에 함께** 남긴다. 파일만 생기고 transaction이 실패하면 orphan GC 대상이며,
DB commit 뒤 파일 손실/불일치는 export 완료 실패다. 느린 파일 I/O와 PG transaction 중첩 시간은
실측·제한하고 source fetch 동안 transaction을 열지 않는다.

여기서 **accepted는 신규 insert 행 또는 source current CAS 성공 행만을 뜻하지 않는다**.
현재 attempt가 원문·logical identity·typed normalized value를 검증한 **정확한 persisted fact
복합 PK/lineage와 값 digest**의 집합이다. 이미 같은 값이 저장되어 duplicate skip·values_loaded=0인
재관측도 포함한다. 단, 정규화 reject·원문/identity/값 검증 실패는 accepted가 아니다.
실패 A가 X를 먼저 commit한 뒤 성공 B가 X를 동일하게 재관측하면, B는 기존 X의 정확한 PK와
원문을 검증하고 B가 재관측했다는 receipt를 남긴다. X를 처음 만든 run이 실패했다는 이유만으로
B까지 영구 제외하지 않는다. 새 행 수는 fact 저장량이고 성공 snapshot 완전성의 분모가 아니다.

또한 더 최신이지만 실패한 A 때문에 B의 source current CAS가0이어도, B의 검증된 persisted
point를 export **독립 마지막 성공 head**와 비교해 승격할 수 있어야 한다. source current에서
현재 winner가 아니라는 이유로 B의 유효한 성공 후보를 버리지 않는다. 공개 후보 승패는 source
current CAS 결과가 아니라 terminal 성공 receipt·owner·정책/관측시각·공개 성공 head CAS로 판정한다.
기존 공개 성공 head보다 낡은 B 값은 값 winner를 되돌리지 않지만 B의 재관측/완결 증거는 남긴다.
중복 원문이 source_record_key alias로 정본 키에 합쳐지는 경우에도 commit receipt는 **실제 저장된
canonical source key/정확한 fact PK**를 담아야 한다. proposal의 임시 source alias를 fact PK처럼
신뢰하지 않는다. B가 다른 alias로 같은 원문을 재관측한 경우에도 canonical key 매핑·값 digest·
B의 검증 receipt를 보존해 성공 후보로 인정한다.

precommit serialization은 proposal일 뿐이고, chunk receipt의 accepted key/값 hash/원문 digest와
일치함을 검증한 immutable candidate만 terminal 승격 대상이다. 같은 PK의 fact가 upsert/교정으로
나중에 바뀔 수 있는 경로라면 **나중 fact PK 재조회만으로 값을 신뢰하지 않는다**. B가 실제로
검증한 normalized input의 immutable artifact/value hash와 commit receipt를 묶어 보존한다.
후속 fact 변경·같은 PK의 correction이 발생해도 B candidate의 내용은 바뀌지 않는다. artifact hash
불일치는 재생성 성공으로 조용히 넘어가지 않고 export 실패/재검증으로 처리한다.

대안은 persisted fact가 불변이라고 **코드/DDL로 입증된 경로에만** receipt의 정확한 복합 PK/
lineage를 bounded 조회해 immutable candidate를 만드는 것이다. 조회 값 digest가 receipt와
달라지면 거부한다. current table 기반 요구는 원천의 최신 논리 point 선택과 current read model을
사용한다는 의미로 지키되, 실패값 격리/멱등 재관측에는 성공 receipt 재사용 또는 이러한 bounded
exact-PK lookup을 명시적 예외로 둔다. 전체 fact history scan·`WHERE created_by_run=B`만으로
성공 재관측을 복원하기·mutable current를 성공 snapshot으로 간주는 모두 금지한다. provider
재호출도 없다. 구현 PR에서 두 경로의 collector+publisher+PG 합산 비용을 비교해 확정한다.

candidate는 **직전 공개 성공 artifact + 해당 attempt의 검증된 변경분**으로 만든다. 성공 전용
current head와 run별 staging을 분리한다. 직전 공개 group hash·source monotonic revision을
기록하고 동시 성공 attempt가 먼저 공개하면 CAS 실패→검증된 변경분을 새 성공 head에 rebase한다.
실패 run이 앞서 mutable pointer를 바꿨어도 candidate의 기반은 공개 성공 head다. 초도 snapshot은
완전한 성공 입력이 준비되기 전 publish하지 않으며, 기존 current pointer에서 bootstrap하려면
각 값이 성공 attempt에서 실제 검증·재관측됐다는 receipt와 immutable value 증거를 입증하는
전용 작업이 필요하다(원래 insert한 run의 성공 여부만으로 고르지 않는다).

### 3.1.1 성공 barrier·scope 완결과 generation 공개

한 source run의 expected scope/목록·입력 hash·chunk count/검증된 completeness를 attempt manifest에
먼저 고정한다. 무한 stream을 끝내지 못한 상태는 complete가 아니다. exporter는 bounded 처리하되
**모든 expected group/chunk**를 확인한 뒤에만 scope 완료 barrier를 세운다. 300 group 중32개만
처리한 staging 상태를 새 `source_status=ok` generation으로 공개하지 않는다. 예상 개수뿐 아니라
scope key digest와 누락/중복/retire 집합도 검증한다.

manifest 승격 transaction은 다음을 함께 검증한다: terminal success receipt, 현재 owner epoch/
attempt, cancellation, 완결 barrier, 원천 성공 시각/순서, artifact checksum 및 성공 head CAS. W1/W2/W3/W5 공개 데이터에 동일하게 적용한다.
실행 시작 때 `execution_mode=dagster|native`와 owner/attempt를 확정·영속화한다. 여기서 native는
명시적인 독립 CLI 실행이며 Dagster native launcher로 실행된 job은 여전히 dagster mode다.
Dagster context인데 run ID가 누락되면 configuration/identity 오류로 거부하고 **native로 강등하지
않는다**. 재시도/reconciliation이 mode를 바꿀 수도 없다.
Dagster run은 SyncRun success와 실제 Dagster terminal SUCCESS를 실행 종료 뒤 bounded
reconciliation이 확인해 승격한다. **수집 job은 승격 완료를 기다리지 않고** SyncRun/attempt 결과를
남긴 뒤 종료하므로 자기 terminal status 대기 deadlock이 없다. native 실행만 해당 owner의 terminal
SyncRun success receipt를 사용한다. manifest 공개와 checkpoint/성공 export watermark 전진은
같은 commit이다. failed/canceled/stale-owner/부분 run은 이전 공개 성공 generation을 유지하며
source_status만 stale로 알린다. runner가 끝나기 전 candidate는 API에서 보이지 않는다.

순환 수집 300격자 run이 전국1450곳 전체 snapshot인 것처럼 표시하지 않는다. 해당 run의 명시
scope만 완결되는 incremental update이며 기존 성공 group과 병합한다. source별 완전성과 freshness
범위를 metadata에 표시하고, 지역/격자 밖 과거 group을 absent로 삭제하지 않는다. 원천이 전국
완전 집합이라고 선언한 경우에는 전체 집합 barrier를 통과해야 retire/removal을 확정한다.

상태/성공 generation/실제 source_collected_at/last failure와 export watermark는 **provider +
dataset + forecast_style + collection_scope**별로 보존한다. 다른 provider 성공이나 KMA의 다른
회전 slot 성공으로 앞 scope의 실패를 지우거나 그 scope watermark를 전진시키지 않는다.
요청 envelope는 선택 scope 목록과 각 generation/수집시각/status/checkpoint 범위를 제공한다.
집계 source_collected_at은 모든 선택 scope가 알려져 있으면 그 **최소 수집시각**, 하나라도
초도 미확정이면 null이다. status는 failed > stale > ok의 worst-status로 고정한다. 초도 성공 없는
필수 scope가 있으면 전체 요청은503/준비되지않음이고, 이전 성공을 제공할 수 있는 실패 scope는
stale다. max collected_at이나 최근 성공 하나로 전체가 신선하다고 표현하지 않는다.

checkpoint 승인도 scope별이다. opaque checkpoint는 선택 scope 각자의 **승인 완료한 generation/
change watermark vector**를 가리킨다. A 성공/B 실패라면 A만 전진하고 B는 이전 승인에 남으며,
전체가 전진했다는 단일 timestamp로 바꾸지 않는다. Map이 all-or-nothing generation 교체를
요구하면 모든 요청 scope가 ok·완결인지 확인한 뒤 그 vector를 한 번에 승인한다. 부분 승인은
명시된 독립 scope에 한해서만 가능하며, 실패 scope를 다음 delta 대상에서 빼지 않는다.

### 3.1.2 COW artifact와 비용 상한

group artifact는 content hash로 재사용하고 원문 raw를 복제하지 않는다. NVMe의 원자 rename
또는 기존 S3 경계를 사용하며 서비스 프로세스/다중 worker가 공유한다. API worker의 임시 dict만으로
cursor 정합성을 만들지 않는다. 작은 group-key→artifact hash manifest를 고정하고 이미 검증된
1.8M 값을 매번 복사하지 않는다. manifest 고정은 작은 pointer 집합에 대한 짧은 일관 DB snapshot이다.

publisher 처리 단위는 초안상 32 group 또는 raw 8MiB, 한 개 worker, 제한된 DB/CPU deadline이다.
이는 staging progress의 상한이며 **새 공개 generation의 완결 단위가 아니다**. scope 전체 완료
전까지 공개 head/watermark는 전진하지 않는다. group 하나는 raw 512KiB/값5000개 기준으로 먼저
측정한다. 유효 group이 더 크면 명시적인 segment 계약을 리뷰하고, 조용히 truncate하지 않는다.
API는 `limit<=5000` 개수와 raw body8MiB 상한 중 먼저 도달하는 완전 group 수만 stream한다.

일반 요청은 이미 준비된 artifact page를 읽고 전체 rebuild를 동기 실행하지 않는다. 준비된 성공
artifact가 없으면503/Retry-After, 이전 성공이 있으면 그 generation과 정확한 stale 상태를 낸다.
export 실패가 원천 수집의 이미 성공한 fact commit을 되돌리지는 않는다.

규모 추정: W3의1.8M scalar가 숫자당8~16바이트라면 값 배열만 약14~28MiB다(실측 아님,
null/target/metadata/gzip·Python overhead 제외). group 수×manifest pointer bytes, 변경 group
artifact bytes, attempt 동시개수×미완성 candidate bytes, 보존 checkpoint에 필요한 change log/
artifact bytes를 각각 계측한다. 값 전체를 세대마다 복제하는 접근의 숨은 디스크/RAM 비용을 피한다.

COW 채택 여부는 **baseline indexed current-query 방식**과 같은 입력으로 비교한다. baseline은
변경0/소수group/전체scope 갱신 세 경우에서 기존 성공 snapshot을 indexed query/작은 manifest로
고정하는 최소 대안이며, 무조건 전국 full history scan하는 나쁜 대조군으로 만들지 않는다.
같은 normalized fixture·cold/warm·호스트부하에서 각각 최소3회 collector+serializer+publisher+
PostgreSQL **합산** CPU core-seconds, wall time, peak RSS/cgroup, WAL/temp bytes, I/O·buffers,
export ready 지연·backlog group/bytes·API p95를 잰다.

초안 exit gate: 정확성/failed-partial 공개0, bounded count/bytes0위반, change0 작업은 새로운
대형 artifact 재생성0; baseline 대비 합산 CPU·WAL·peakRAM·API p95 10% 초과 악화면 채택/확대
중단하고 원인을 해결한다. dirty backlog가 한 poll 주기를 넘겨 누적되거나 완료 예상시간이
source stale budget을 넘으면 worker 수를 즉시 늘리지 않고 처리량/serialize 전략을 조정한다.
수치는 구현 전 baseline에서 확정하며, 단지 API가 빨라졌다는 이유로 collector 비용 증가를 숨기지 않는다.

페이지 snapshot TTL 초안10분 + active reader lease 상한5분은 **페이지 재시도용**이다.
**durable delta checkpoint/change log 보존은 별도**이며 Map 최대 poll 간격 + 허용 장애 복구기간 +
안전 여유 이상으로 설정한다. 예를 들어 poll8시간+복구72시간이면80시간보다 길게 보존해야 하며
초안7일을 측정·설정한다. log/checkpoint가 만료돼도 아직 lease 중인 page artifact를 GC하지 않는다.
checkpoint 구간 전체 artifact를 복제할 필요는 없지만 삭제 tombstone·group revision change와
복원에 필요한 immutable artifact/최종 상태는 보존한다. 실제 보존 상한과 storage budget을 함께
검증한다. TTL 만료 page나 checkpoint는410/명시 full restart이며 empty 성공이 아니다.

### 3.2 페이지·조건부 요청

cursor는 `snapshot_id + filter digest + 마지막 완전 group key + schema revision + limit + byte-cap revision`을 담은
불투명 값이다. 기존 설정 secret을 cursor 서명키로 재활용하지 않는다. DB에 저장한 opaque random
snapshot handle 및 조회 검증으로 위변조/타필터 혼용을 거부한다(새 인증 방식이 아니다).
페이지 순서는 location/provider/style의 안정 자연키, group 내부 target_at은 오름차순이다.
페이지 경계에서 group을 찢지 않는다. cursor 생성 때 limit와 byte-cap 정책 revision을 고정하며,
중간 요청의 변경된 limit·정책과 혼용하면422로 거부하거나 cursor에 고정된 값을 쓴다는 계약을
하나로 고정한다. 기본안은 명시 변경 거부다. 요청 시각 경계가 같으면 동일 cursor는 같은 group/
순서/내용을 낸다. forecast의 시간 경계 변화는 아래 representation epoch로 명시한다.

`generated_at`은 요청마다 바뀌므로 semantic validator 입력에서 제외한다. 응답 바이트 전체가
같다는 strong ETag 주장을 하지 않고 **weak semantic ETag**를 사용한다. representation schema·
snapshot/filter·page key/고정limit·byte-cap revision·artifact hash·health 상태 revision·시간 epoch의
digest다. `Vary: Accept-Encoding`, gzip, private cache 정책을 명시한다. gzip/identity는 동일 semantic weak validator를 쓸 수 있고 Vary로 encoding을 분리한다.
304의 volatile generated_at 본문은 없으며 응답시각은 Date header로 확인한다. 인증은 If-None-Match보다 먼저 검사한다. 304는 본문 없음이며 같은 ETag와 freshness
header를 반환한다. source failure나 stale threshold 경과는 health validator를 바꾸어 304가
잘못된 최신 상태를 숨기지 못하게 한다. 데이터 generation은 고정해도 health는 현재 상태임을
명시하고, Map은 페이지 중간 failed/stale가 나오면 새 generation publish를 보류한다.

forecast는 **각 응답 시점의 지금 이후 target만** 내보낸다. 고정 snapshot_as_of가 과거였다는
이유로 이미 지난 target을 보내고 Map에 prune 책임을 넘기지 않는다. snapshot artifact의 logical
내용은 고정하되 응답의 temporal projection을 별도로 정의한다. `next_change_at`은 현재 페이지
후보 target 중 다음 만료 경계(및 health stale 전환)의 최소 시각이고, 그 경계를 넘으면 temporal
representation epoch/weak ETag를 바꾼다. 실제 serialize 직전에 aware 응답시각을 고정하고
`target_at >= response_generated_at`인 target/모든 metric aligned 배열만 남긴다. 엄격한 미래
`>`인지 같음 허용`>=`인지는 OpenAPI에 일관되게 고정한다(기본은 같음 허용).

페이지 cursor는 시간 필터 전의 stable group key를 기준으로 전진한다. 시간 경과로 group 전체가
빈 미래 배열이 되면 그 group을 생략하고 뒤의 group을 byte/count cap 안에서 읽어 next_cursor를
만든다. 같은 시간 epoch 내 retry는 같고 경계가 지난 retry는 더 적은 미래 값을 낼 수 있음을
명시한다. temporal filtering은 source 수정/삭제로 간주하지 않는다. group key 기준 페이지 순서를
되돌리지 않고 cursor가 소비한 과거 group을 다시 보내지 않는다. 긴 수집 도중 앞 페이지 target이
Map 도착 전에 지난 경우 Map도 자연 만료 처리를 할 수 있지만 이는 API가 응답 시점 과거 값을
내보내도 된다는 허용이 아니다. `If-None-Match`는 serialize 시점 epoch와 health를 계산한 뒤
판정해 만료 경계 뒤 잘못된304를 막는다. 응답생성시각/generated_at과 next_change_at의 관계를
테스트 clock으로 고정해 검증한다.

### 3.3 known_at 증분

W3 `since`는 변경된 scalar만이 아니라 **변경된 group 전체를 교체**하는 결과다. 그래야 metric
삭제/시각 소실·provider 수정의 aligned 배열을 정확히 갱신한다. group removal tombstone과
예보 시각 경과는 별도 expiry metadata/Map prune으로 처리한다.

`known_at > since`만으로 늦게 commit된 예전 known_at·동일 known_at 수정·삭제를 완전하게
잡을 수 없다는 한계를 숨기지 않는다. 초기 RFC3339 `since`는 current group max known_at에 대한
필터로 제공하되, 완전 증분 동기화에는 **직전 export snapshot checkpoint**를 함께 사용한다.
checkpoint는 snapshot handle이며 publisher가 실제 공개한 group revision change set을 기준으로
한다. known_at은 원천 인식 시각으로 DTO에 그대로 남긴다. checkpoint 이후 dirty group의 old
known_at 수정도 포함하고, TTL/원장 retention을 넘은 checkpoint는 410→전체 snapshot 재시작이다.
시간 since만 쓰면 다음 누락이 가능함을 OpenAPI/응답 예시에 명시한다: (1) 이전 watermark 이하
known_at이 늦게 commit, (2) 동일 known_at의 payload/metric correction, (3) group/metric 삭제,
(4) 시각 경과로만 사라진 target. 현재 pointer에 없는 예전 revision은 known_at cutoff를 다시
검색해도 복구되지 않는다. since는 **일치하는 변경 group의 전체 교체**이지 scalar patch가 아니다.
클라이언트가 시간 since만 저장한 경우에는 정확한 누락 없는 delta를 보장한다고 표현하지 않는다.
Map 구현은 반드시 checkpoint를 영속화하고 성공 generation publish 뒤에만 전진시킨다.

## 4. Weather W1~W5

공통 prefix는 `/v1/service/exports`이며 require_admin/AdminToken을 그대로 붙인다.

### W1 `GET /locations`

활성 location 약1450곳의 id/name/coordinates/legal code/sido/sigungu(있는 값만)를 keyset으로
제공한다. 위치 원천 출처와 실제 날씨 provider를 혼동하지 않는다. 위치 메타 변경/비활성화도
export dirty/tombstone에 반영한다. 좌표가 없으면 null, (0,0) fallback은 금지한다.

### W2 `GET /current`

location×provider×metric마다 관측/nowcast 최신 1개(약32000개). value/unit/target_at/known_at/
style(observed|nowcast)를 낸다. source current pointer의 최신 논리 point 선택 규약을 따르되,
공개값은 §3의 **성공 export projection/artifact**에서 관측시각 최신을 고른다. 실패 attempt의 현재
pointer를 직접 읽어 성공 head를 덮지 않으며 zero-new-row 재관측도 성공 receipt로 반영한다.
일반 forecast·alert가 최신 대표를 차지하지 않게 필터한다. 동일 target revision은 기존 compare-and-set
우선순위를 그대로 쓰며 value_id+known_at 복합 PK로 필요한 fact만 읽는다.

### W3 `GET /forecasts?style=ultra_short|short|mid|external&provider=&since=`

group item은 `{location_id, provider, forecast_style, known_at, target_at:[], metrics:{metric:[...]}}`.
`target_at`은 group의 고유 target 합집합 정렬, 모든 metric 배열 길이가 같고 없는 값은 null이다.
known_at은 group 안 실제 source revision의 max이며 배열별 개별 시각이 모두 같다는 뜻이 아니다.
동일 target/metric에 복수 dataset이 있으면 typed provider/dataset 정책으로 winner를 정하고
임의 마지막 fetch 순서로 덮지 않는다. 단위/정규화 revision이 다른 metric을 한 배열로 섞지 않는다.
외부 provider forecast의 style 매핑은 기존 normalized 계약을 확인하고 명시한다.

완전 group key는 location/provider/style에 representation schema를 더한다. group cursor와 gzip으로
1.8M 값을 나눠 낸다. 원천 current 논리 point에 기반한 성공 receipt/immutable artifact를 읽고,
필요한 예외는 검증된 bounded exact-PK lookup뿐이다. mutable current pointer를 실패 run 필터 없이
직접 페이지하는 방식이나 history 전체 scan은 금지한다. 한 provider/style만의 group을 여러 forecast style로
다시 라벨해 중복 export하지 않는다. since/checkpoint는 §3.3, snapshot은 §3.1 규약이다.

### W4 `GET /status`

provider/style별 마지막 성공·마지막 실패 여부/시각·next schedule을 낸다. SyncRun 상태와 Dagster
schedule timezone/disabled 상태를 구분한다. success 없이 단지 run이 종료됐다고 정상 처리하지
않는다. next schedule이 미설정/정지면 null이며 HTTP 호출이 Dagster run 전체 이력을 순회하지
않도록 작은 상태 projection/cache를 이용한다. 실패 원문이나 credential은 내지 않는다.

### W5 `GET /daily-summaries?days=14&location_id=`

별도 작은 사전 집계 표를 Weather가 소유한다. Map Valkey 비교용이며 Weather 기존 history는
삭제하거나 retention을 줄이지 않는다. days 기본14/최대31, 요약 자체 retention도 최대 요청31일을
충족하도록31일 + 지연 재처리 여유를 설정해 명시한다.

KST 기준 morning `[05:00,11:00)`, noon `[11:00,17:00)`, evening `[17:00,23:00)`다.
23~05시는 요구 slot 밖이라 자동으로 이전/다음 slot에 합치지 않는다. UTC 저장 시 KST 날짜/구간을
한 번 계산해 반열린 구간으로 변환한다. item은 location_id/date/slot/temp_min,max,avg/
humidity_avg/precip_sum_mm/precip_occurred/sky_code/wind_speed_avg/sample_count/
source_providers[]/partial이다. 관측이 없으면 수치·sky·precip bool은 null, sample_count=0이며
false/0으로 관측 성공을 조작하지 않는다. `partial`은 **현재 시각이 해당 slot 종료 전인가**만 뜻하는 시간적 미완료다. 오늘 미래 slot도
종료 전이면 true이고, 과거 slot의 집계 실패를 partial=true로 숨기지 않는다. 별도
`summary_status=ready|not_ready|failed`와 coverage metadata를 가산한다. 집계 미완/초도 미집계는
not_ready, 집계 장애는 failed, 완료된 정상 무관측은 ready+sample_count0/null이다. 모든 원천을
확인하지 못한 source 상태와 `partial`은 별도다. 과거 slot의 failed/null을 정상 무관측처럼 내지 않는다.

**관측 포함 범위는 사용자 답변 대기 중인 결정**이다. A안은 실제 관측 의미의 KMA NOWCAST까지
포함(권장), B안은 normalized enum `forecast_style=observed`만 포함한다. KMA T1H/RN1 등이 실제
관측이지만 enum nowcast인 코드가 있어 B안은 주요 KMA 자료를 제외할 수 있다. 선택이 오기 전
종속 구현·backfill·공개 summary 의미를 확정하지 않는다. 두 안 모두 미래 forecast·alert는 제외하며
provider/dataset/metric별 실제 관측 eligibility를 명시적으로 고정한다. 테스트 fixture도 선택 후 확정한다. raw RN1/누적강수/강수율을 모두 더하지
않고 provider의 시간 구간·단위 계약으로 관측량을 먼저 맞춘다. 동일 위치·metric·관측구간에서
복수 provider와 수정 revision을 중복 합산하지 않는다. 검토된 provider rank+관측 최신성으로
대표를 고르고 source_providers는 실제 사용한 원천만 담는다. 강수는 관측 interval `[start,end)`와 quantity/
rate/cumulative 종류·원천 reset 증거를 보존한다. 겹치는 provider/누적 interval을 이중 합산하지
않으며, cumulative 차분은 같은 sensor/연속성·reset 검증이 있을 때만 사용한다. reset/음수차분은
0으로 덮지 않고 해당 interval을 미확정 처리한다. slot 경계를 넘는 interval은 원천의 sub-interval
자료로 정확 분해 가능한 때만 나눈다. **시간 비례 임의 배분은 금지**한다. 분해할 수 없으면
precip_sum_mm은 null/coverage partial(시간적 partial과 별도)을 내고 관측된 강수유무도 실제 증거가
있을 때만 true/false로 정한다. 완전히 slot 내부이고 coverage가 입증된 비중복 interval의 합만
확정 합계다. interval coverage/missing duration과 적용 policy revision을 summary 증거로 남긴다.
sky 대표는 정규화된 code의 최빈값,
동률은 명시적인 안정 우선순위다. sample_count는 사용한 고유 관측시점 수이며 metric별 결측을
평균 분모에 넣지 않는다. metric별 sample count를 가산해 의미를 검증 가능하게 한다.

사용자 관측 eligibility 결정 후, 수집 chunk가 commit될 때 변경된 관측 logical point가 영향을 주는 `(location, KSTdate,
slot)` dirty key를 같은 transaction으로 적는다. worker는 slot당 **성공 receipt로 검증된 관측 point**만 인덱스로
읽어 대표 재계산한다. 실패 run이 바꿔 놓은 mutable current pointer를 그대로 신뢰하지 않는다.
§3의 성공 attempt immutable artifact/재관측 포함 accepted receipt를 사용하며, 성공 전용 관측 projection이 필요하면
최대 요청31일 범위로 한정한 인덱스 구조를 비교한다. 이 구조도 원문 history의 대체/삭제가 아니다. 수정/지연 도착은 기존 slot을 다시 계산하므로 단순 누적합의 중복 문제가
없다. dirty version CAS로 처리 중 새 쓰기를 잃지 않고 query-time history 전체 집계를 금지한다.
초기 backfill은 명시된 최대31일/위치단위·slot단위/bytes와 DB시간 제한의 관리 job으로만 수행한다.
성공 관측 projection/receipt에 필요한 과거 point가 없으면 제한 날짜 partition의 정확한 PK/index만
읽는 일회 backfill을 별도 검증하고 full history scan으로 fallback하지 않는다. 준비 전 API는
summary_status=not_ready와 null/coverage를 내며 temporal partial과 구분한다. 실패는 failed이고,
요청이 알아서 전체 이력을 재집계하지 않는다.

## 5. Transport T1~T5

### T1 주유소·휴게소 + T3 참조 admin

fuel-stations/rest-areas 및 T3 rail-stations/bus-terminals/ferry-ports에 nullable `admin`을 가산한다.
legal_dong_code(10자리), admin_dong_code, sido_name, sigungu_name, legal_dong_name,
road_address, parcel_address, postal_code, provider(vworld|kakao), resolved_at을 준다.
`reverse_address_entries`의 target_key를 각 entity와 join하고 **저장 coordinate_key가 현재 좌표의
동일 canonical key일 때만** 붙인다. common의 기존 key 생성 함수를 직접 쓰고 반올림/좌표순서
규칙을 복제하지 않는다. 옛 좌표의 주소·provider/코드를 새 좌표에 붙이지 않는다. 불일치/미해결은
admin=null이고 HTTP에서 reverse API를 새로 호출하지 않는다. name/code/coords는 해당 참조 정본이다.

### T2 교통·주차 current

`GET /highway-traffic/current`: latest/conzone 약8300, identity_key/route_no,name/conzone_id,name/
direction/speed/free_flow_speed/congestion_level/observed_at.
`GET /parking/current`: latest/lot 약44, coords/total/available/observed_at.

확인한 현재 model에는 HighwayTrafficSnapshot·ParkingSnapshot history는 있지만 FuelLatestPrice
외에 전용 traffic/parking latest 표는 없다. 따라서 source write transaction에서 함께 갱신하는
작은 latest read model을 추가하거나 현재 공개 API의 이미 검증된 indexed latest query를 재사용한다.
전국 history DISTINCT 전체 scan을 요청마다 수행하지 않는다. current pointer는 원본 snapshot PK를
참조하고 observed_at→collected_at→stable PK tie-break로 오래된 관측이 최신을 되돌리지 못하게 한다.
기존 history·5분 통계·주차 통계는 보존한다. 신규 latest bootstrap은 수집 성공 후/한정 backfill로
하며 초도 없을 때 empty 정상처럼 내지 않는다. 좌표가 없는 parking lot은 검증된 airport anchor를
사용했다는 precision/source를 명시하고 lot 실측 좌표라고 표시하지 않는다.

### T3 참조

기존 subway/KRIC 역명·코드 매핑 JSON/catalog와 버스터미널·항구 정본을 재사용하고 Map용
export route만 추가한다. identity는 station/운영기관/노선의 기존 안정 키, 코드 문자열의 선행0을
보존한다. 여러 운영기관의 같은 역명은 임의 하나로 합치지 않는다. T1과 같은 admin 증거를 붙인다.

### T4 주유소 가격 이력 예외

`GET /fuel-stations/{station_key}/price-history?product=&from=&to=&limit=`.
기본30일이며 station 하나/product 하나에 범위를 제한한다. `from/to`는 aware 시각, 최대조회기간과
limit 상한(<=5000)을 OpenAPI에서 고정한다. indexed FuelPriceSnapshot의 station/product/
observed_at 범위만 읽고 `[{observed_at,price}]`를 시간 순으로 낸다. pagination은 timestamp+stablePK
keyset, 같은 observed_at 수정은 기존 unique/upsert 계약을 따른다. 전량 history를 메모리에 읽은 뒤
Python slice하지 않는다. T4만 과거 응답 예외이며 current export에 이력을 끼워 넣지 않는다.

휴게소 이력은 현재 저장 자료가 없으면 **만들지 않는다**. 별도 휴게소 history 경로가 없으면404
problem, 명시된 route가 있지만 미지원이면501 problem 중 한 정책을 OpenAPI·Map 계약·PR 본문에
고정한다. empty200으로 구현 완료인 척하지 않는다. 준비 안 된 `1ebb50c` 초안은 검증 후 필요한
범위만 #104 이후로 옮기고 docs/openapi.json·TS 타입을 함께 생성한다.

### T5 수집 실패/급감 guard

KREX incident와 유사한 전국 집합에 §2의 bounded retry+완전성 확인을 적용한다. 오래된 성공
집합을 새 observed_at으로 덮거나 빈 수집을 성공 상태로 전진시키지 않는다. source fetch/DB저장/
후처리 실패는 구분하고 기존 수집 save-only retry/rollback·#103 timeout/grace·#104 원자 publish를
보존한다. 장애 주입 후 이전 generation/current read model이 유지되고 Map106사건이 사라지지
않는지 실제 collector→export→Map sample contract까지 검증한다.

## 6. 소유 파일·구현 분할 제안

Transport #104 closure가 끝나기 전 겹치는 파일을 다른 worker가 수정하지 않는다.
이 문서는 Weather의 제안 계약 정본이며 Transport 절은 **외부 연계 설계**다. Transport의 코드·
ADR/작업원장·OpenAPI를 이 Weather 문서 PR이 변경하거나 완료 처리하지 않는다. 구현 소유권은
Weather W1~W5와 Transport T1~T5를 분리하며, 각 저장소의 별도 feature PR·검증으로 수용한다.
원래 dirty 작업 경로를 복사/되돌리지 않고 각 소유자가 안전한 전용 branch/worktree를 사용한다.

1. Weather 문서 전용 소유: `docs/design/map-service-exports.md` + 관련 ADR/작업원장 포인터.
   기존 auth/path 사실·W1~5/증분·snapshot·slot 수용 조건을 명시하고 doc-only PR을 따로 리뷰한다.
2. Weather contract/repository 구현 한 담당: `api/routers/service_exports.py`(신규), app OpenAPI,
   auth 재사용, DTO·export script·TS타입·repository current queries·export artifact/dirty state.
   W1/W2/W4부터 작게, W3 group snapshot은 다음 PR. Python provider client 자체는 수정하지 않는다.
3. Weather W5 한 담당: summary migration/model·dirty-slot publisher·Dagster 집계/retention·bounded
   초기 backfill·요약 endpoint와 tests. repository 공통 쓰기 hook은 2와 사전 합의해 한 담당만 편집.
4. Transport 한 담당(root 이후): service_exports.py/schemas.py/models.py/migration·기존 collectors의
   current projection/admin join·T1/T2/T3/T5. #104 최종 커밋을 먼저 흡수한 뒤 ownership 인수.
5. Transport T4 한 담당: 기존 fuel snapshot indexed history와 endpoint/schema/OpenAPI/TS/tests.
   service_exports.py를4와 동시에 수정하지 않고 순서대로 인계한다. 휴게소 history 표 신설 금지.
6. 공통 bounds/cursor 유틸이 정말 중복될 때만 kor-travel-common에 작게 둔다. Weather/Transport
   payload schema·provider 정책·DB 도메인 집계를 common에 섞지 않는다.

## 7. 수용 조건·배포 순서

- W1~5/T1~5 항목별 정상/초도없음/정상빈집합/급감후재확인실패/오래된성공/수집실패/DB실패,
  잘못된cursor/다른filtercursor/TTL만료/304/auth/gzip/경계5000/bytecap을 테스트한다.
- 페이지 사이 수집 commit·지연된 known_at·동일known_at수정·group삭제·forecast만료를 주입해
  frozen generation/complete group/체크포인트 불전진/재시작을 검증한다.
- W5 관측 eligibility는 사용자 선택 후 확정한다. 05/11/17/23시 경계·누락구간23~05·provider중복·
  관측revision수정·늦은도착·noobs·금일partial/과거failed/not_ready 분리·metric결측·강수 overlapping/
  reset/slotcrossing과 coverage null·dirtyCAS·retention·indexed bounded query를 검증한다.
- 실패 run chunk가 이미 current pointer를 바꾼 뒤 run FAILURE/cancel되는 fixture, owner epoch
  탈취·commit 응답 유실·artifact 손실·300 group 중32개만 처리·success scope digest 누락을
  주입해 이전 공개 성공 generation과 watermark/checkpoint가 유지되는지 검증한다.
- 실패 A가 X commit→성공 B가 같은 X 재관측(values_loaded0), 더 최신 실패 A로 인해 성공 B의
  currentCAS0, B receipt 뒤 같은 factPK 교정 fixture를 각각 실행한다. B의 완전성 barrier는 신규
  insert 수가 아닌 validated accepted 집합으로 통과하고 독립 성공 head 규약으로 값을 반영한다.
  mutable fact 변경이 B artifact를 바꾸지 않으며 W2/W3/W5에도 같은 규칙이 적용됨을 검증한다.
  다른 source-key alias의 동일 원문 재관측도 canonical key 매핑/정확 PK와 receipt를 보존한다.
- provider A 성공/B 실패와 KMA 회전 slotA 실패/slotB 성공 fixture에서 envelope는 최소 수집시각/
  worst-status이고 B/slotA checkpoint가 전진하지 않는지 검증한다. 필수 초도 scope 누락은503이며
  all-or-nothing Map publish가 부분 vector를 전체 성공으로 승인하지 않음을 검사한다.
- Dagster 수집 job이 승격을 기다리지 않고 terminal로 끝난 뒤 reconciliation이 publish함을 검증한다.
  실행시작 mode 불변·Dagster ID 누락시 native강등금지·잘못된 mode/owner receipt 거부도 검사한다.
- page TTL보다 긴 정상 poll/장애복구 간격의 durable checkpoint, TTL만료·limit변경·bytecaprevision
  변경, 응답시점target만료/next_change_at/ETag304·배열alignment·COW baseline3패턴 합산비용을 검증한다.
- T1/T3 좌표 변경 직후 stale reverse entry가 null인지, 동일코드 다른기관/선행0·nullable 필드,
  T2 오래된관측 역전 방지, T4 범위/limit/미지원휴게소, T5 KREX106사건 보존을 검증한다.
- OpenAPI·TS타입·docs/ADR/journal·tests를 함께 갱신하고 CI·독립 적대 리뷰2인을 통과시킨다.
  최신 사용자 지시에 따라 이 기능 구현 PR은 CI가 필요하다. 옛 doc-only CI 생략을 전용하지 않는다.
- 배포 **이전** Map Claude 담당에게 W1~W5/T1~T5 실제 응답 예시·OpenAPI·PR URL과 source_status/
  cursor/checkpoint/304/미지원휴게소 의미를 전달한다. root가 사용자 메시지 발송 권한 범위에서 수행한다.
- root #104 closure→API 작은 PR들→Map 소비자 opt-in→live E2E/API/UI 및 동일 입력 CPU/RAM/지연
  실측→머지/배포를 순차 수행한다. 원천 collector/Map이 이전 성공 generation을 보존하는 rollback을
  검증한다. API read가 provider를 새로 호출하거나 수집 global run 한도를 늘리지 않는다.

## 8. 구현 전 확정할 공개 계약

1. Weather 새 service route가 기존 x-admin-token/AdminToken을 쓰는 사실을 Map에 명시한다.
   기존에 없는 Weather 서비스 인증 header를 있다고 가정하지 않는다.
2. 정확한 delta에는 opaque checkpoint를 함께 쓴다. ISO since만으로 late commit 보장이 불가능한
   점을 사용자/Map 계약에서 숨기지 않는다.
3. forecast future는 응답시점 기준이다. source snapshot과 시간적 representation을 분리하고
   next_change_at/time epoch/ETag를 사용한다. source snapshot 고정이 지난 target 허용의 근거가 아니다.
4. Transport 기존 export의 503/current-set 의미는 opt-in 이전까지 보존한다. 새 last-success/stale
   metadata를 받아들이는 Map opt-in과 배포 순서를 함께 변경한다.
5. W5 3slot이 최신 요구이며 Map ADR114의 반나절 표현/14일 저장과 충돌하는 시간 단위는 문서 PR에서
   맞춘다. 실제 KMA NOWCAST 포함 관측 범위는 **사용자 답변 pending**이며 종속 구현은 대기한다.
   Weather 요약 최대31일 조회, Map 보관14일, Weather 원본 history는 별도 보존이다.

## 9. 문서 보존과 현재 진행 상태

이 문서는 설계 proposed 상태로 보존한다. W1~W5/T1~T5의 기능 구현·API 배포·실측 수용은
아직 시작하지 않았다. 현재 code/DTO/OpenAPI/TS를 바꾸지 않았으므로 명세 예시는 제안 계약이며
운영 endpoint가 존재한다고 해석하지 않는다. Weather 기존 `/v1/service/exports` 부재와 기존
AdminToken 재사용은 §1의 사실/제안 구분을 따른다.

현재 문서 PR은 사용자 지시로 `[skip ci]`를 사용하고 CI를 실행·검토·대기하지 않는다.
독립 리뷰 2인 후 문서 머지 여부를 별도로 판단한다. **기능 구현 PR에는 §7의 CI·2인 리뷰·
Map 담당 응답 예시 전달·live E2E·배포 게이트가 그대로 필요**하다. W5 관측 범위 결정 전에는
해당 eligibility·집계·초기 backfill 종속 구현을 진행하지 않는다. COW는 실측을 통과하지 못하면
채택하지 않는다. 이 문서 수납만으로 두 조건이 해제되지 않는다.
