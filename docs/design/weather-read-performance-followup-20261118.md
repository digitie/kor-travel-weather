# Weather DB export 읽기 성능 후속 조치

기준 코드: `6dcdd725f2c64592ba0be792a03aebf2024dd016`. 운영 파일 정리 완료 후
읽기 전용 관측으로 병목을 확인했다. Transport 빌드 중에는 추가 운영 측정을 하지 않는다.
이 변경에는 DDL, 인덱스, 작업 정지, 데이터 주입, 의존성 변경이 없다.

## 관측 근거와 한계

- 기존 serving metric의 forecast 실패 1건은 artifact 128회 합계 111.499초,
  freeze 9.028초, queue 0.005초였다. 이는 과거 요청의 누적 metric이며
  파일 정리 후 새 HTTP 요청을 측정한 결과로 해석하지 않는다.
- 정리 후 같은 운영 이미지의 읽기 전용 메타데이터 프로파일은 forecast 915 scope,
  1,452 위치 선택에 8.983초였다. 별도 프로세스의 daily metadata 프로파일은
  34.806초였으며 profiler와 동시 DB 활동을 포함한다. HTTP 지연으로 환산하지 않는다.
- generation PK 조회 후 동일 attempt의 publication을 다시 읽었다. 해당 조회의 SQL
  합계는 forecast 0.578초, daily 3.436초였다. 같은 RR에서 이미 확인한 generation을
  재사용할 수 있다. 실제 계획은 기존 PK/인덱스를 사용하므로 새 인덱스를 추가하지 않는다.
- SQL hook이 public deadline 없는 서비스 export를 누락했다. 이 관측 누락을 보정한다.
  trace는 요청 종료 때 발행하므로 진행 중 stage 0을 queue 대기로 단정할 수 없다.

## 변경과 로컬 수용 기준

1. DB artifact를 요청의 다음 읽기 구간에서 최대 8개, canonical bytes 합계 8MiB씩
   같은 shared guard와 연결에서 읽는다. `preload`는 여전히 metadata 존재 확인이다.
   전체 915 scope payload를 선적재하지 않는다. 파일 backend는 기존 단건 읽기를 유지한다.
2. warm decoded cache에서도 실제 DB payload를 SELECT하고 SHA-256, byte count,
   point count를 검증한다. guard는 실제 읽기·해시·decode·복사까지 유지한다.
   페이지 내 재사용에도 receipt가 일치해야 한다.
3. 같은 RR에서 검증한 generation의 publication만 재사용한다. attempt_id UNIQUE가
   근거이며 새 active owner는 계속 조회한다. owner epoch, native run, cohort 상태 검사는
   유지한다. ETag, source_status, as_of, cursor, 고정 60초 metadata TTL은 바꾸지 않는다.
4. 실제 payload SELECT·chunk·byte counter를 mode=single/batch/other로 구분한다.
   artifact stage count 감소를 실제 읽은 chunk 감소로 계산하지 않는다.

| 검증 | 기존 단건 | 변경 후 | 판정 범위 |
| --- | ---: | ---: | --- |
| PG 915-scope fixture, limit 200/2000의 첫 128 scope | payload SELECT 128회 | 16회 | 왕복 87.5% 감소, 같은 결과·cursor state |
| 이미 검증한 active publication 재조회 | 1회 | 0회 | 같은 RR의 결과 동등성 |
| 새 실패 owner publication 조회 | 1회 | 1회 | 실패 상태 감지 유지 |
| API 첫 페이지·continuation | 기존 JSON·ETag | 동일 | current/ultra_short fixture |
| 조건부 304 | payload 읽기 없음 | 단건·batch 모두 금지 | immutable DB proof 유지 |

전체 시험 결과와 CI는 PR에 별도로 기록한다. 이 표는 운영 응답시간 개선을 보증하지 않는다.
배포 후 동일 source/image 증거를 확인한 뒤 current, forecast short, daily의 첫 페이지,
304, continuation 및 limit 200/2000을 다시 측정해야 한다. freeze 약 9초의 기존 비용은
payload 왕복 감소만으로 없어지지 않으며 현재 운영 성능 수용은 아직 미완료다.

## 메모리 예산

- engine별 decoded LRU의 기본 eviction 기준은 canonical bytes 67,108,864바이트(64MiB),
  최대 8,192 entry다. 환경 설정으로 변경할 수 있다. 이 숫자는 저장된 decoded Python
  객체의 크기를 측정한 값이 아니다. 여러 engine/worker의 cache는 별도다.
- 요청 read window는 최대 8 chunk와 canonical bytes 8,388,608바이트(8MiB)로 제한한다.
  chunk당 최대 5,000 point이므로 window의 최대 point 수는 40,000개다.
- DB driver/raw row buffers, hash 검증 중 byte 객체, decoded dict/list/string, 반환용
  dict/deepcopy, 페이지 조립·응답 객체 및 기존 request cache는 추가 메모리를 사용한다.
  flat point의 dict 복사는 중첩 값이 없는 경우에만 쓰고 그 외는 deepcopy를 유지한다.
- 따라서 64MiB 또는 64+8MiB를 Python heap/RSS 상한으로 표시해서는 안 된다.
  이번 변경은 wire-byte/entry/window 상한이며 strict Python heap 또는 RSS 상한은 제공하지
  않는다. 운영 RSS 수치는 별도 측정 대상이다. 신규 전체-snapshot payload cache는 없다.

## 안전성 회귀

실제 PostgreSQL batch/단건 결과 동등성, 반환 객체 독립성, receipt 불일치·행 유실,
잘못된 SHA·line count, batch 개수/byte 초과의 SQL 전 거절, shared guard와 GC exclusive
경합, window 내 receipt 재검증을 시험한다. 기존 page proof/liveness, TTL과 만료 cursor,
새 source prepare/cohort 실패 직후의 200/stale 계약도 기존 assertion 그대로 검증한다.

## 단일 scope window 보충 후속 수정

기준 main은 배포된 PR252 merge `cb7ac7fb`다. cb7 배포 후 current 첫 페이지는
17.134초, 재사용 304는 49.692ms였으나 forecast limit=200은 client 60초 timeout이었다.
이 수치는 부모 QA 근거이며 이번 수정 후 운영 수용 결과가 아니다.

추가 관측은 export GET 없이 기존 metrics 두 번과 작은 읽기 전용 pg_activity 표본만
사용했다. 관측 구간에서 export counter 변화는 0, GET in-flight는 metrics 자신을 포함한
1이었으며 마지막 activity 표본에 활성 쿼리가 없었다. 누적 전체 payload 계수는 batch
69회/524 chunk, single 3회/3 chunk다. 원천별 귀속이 없는 전역 계수이므로 특정 forecast
요청의 single 비중으로 환산하지 않는다. batch가 이미 대부분이므로 아래 재현된 갭만으로
운영 forecast timeout 해결을 보증하지 않는다. 실패 first-page metric의 artifact 합계는
65.732초, 전체 SQL 합계는 8.701초, freeze는 8.243초, queue는 13ms다. 단계는 중첩될 수
있으며 SQL hook 범위 밖의 fetch/decode/복사 비용을 이번 관측만으로 분리할 수 없다.

- cb7의 forecast는 scope 진입 때만 window를 준비했다. 한 scope에 256 ref가 있는 PG
  fixture에서 처음 128 chunk를 읽을 때 실제 SELECT는 첫 batch 1회와 singleton 120회,
  합계 121회였다. 기존 915-scope × 1-ref fixture가 놓친 경로다.
- 현재 위치 g/p/ref에서 새 chunk를 읽기 직전에 window를 보충한다. 같은 fixture의 첫
  페이지와 g=0/p=128 continuation 모두 128 chunk/16 SELECT로 처리하며 출력·state가 같다.
  limit 17은 17 chunk/3 SELECT, limit 200/2000은 기존 128-chunk 상한을 유지한다.
- current도 각 ref 앞에서 보충한다. 후보 generator에 첫 scope의 ref offset을 전달해
  이미 소비한 앞부분을 다시 읽지 않는다. 128-ref scope에서 실제 chunk 128개/SELECT
  16회와 고정 clock의 body·ETag·source_status·as_of·cursor·순서 동등성을 검사한다.
- canonical 8MiB를 두 chunk가 넘는 실제 PG payload fixture에서도 singleton fallback
  없이 크기 제한된 batch를 보충한다. 8-chunk/8MiB window와 128-chunk page 상한, receipt,
  SHA/byte/point count 검산, GC guard, fixed metadata TTL/source-health 규칙은 유지한다.
- 로컬 재현·집중 회귀 134개가 128.21초에 통과했다(Ruff/diff 검사 포함).
  전체 검증은 새 PR의 CI에 맡기며 중복 장기
  로컬 전체 pytest, 추가 운영 export 요청, DDL 또는 이미지 배포는 하지 않는다.

## 실제 canonical chunk의 로컬 CPU 후속 분석

운영에서는 기존 PK 순서의 metadata 최대 64개만 확인하고 불변 payload **1개**를 읽었다.
1,641,070바이트·1,888-point chunk의 SHA/byte/line count를 확인해 비공개 로컬 파일로
보존했다. export GET과 운영 CPU/대형 SQL 프로파일은 추가하지 않았다. 아래 수치는 모두
이 동일 chunk를 로컬에서 측정한 결과이며 운영 forecast 전체의 남은 57초를 분해한 결과가 아니다.

모든 point에 nested `observation_evidence`가 있어 기존 cache의 flat=false 경로는
point 전체를 deepcopy했다. 5회 cProfile에서 deepcopy cumulative는 0.569초,
cold 경로 전체는 0.682초였고, json.loads cumulative는 0.088초였다. 프로파일러 오버헤드가
있으므로 이 시간으로 운영 지연을 외삽하지 않는다. validation/grouping 별도 median은
약 1.35ms였으므로 이번 변경에서 membership·ref 탐색 알고리즘은 추가 변경하지 않았다.

검증된 수정은 두 가지다. 기존 의존성인 pydantic-core의 행별 `from_json(cache_strings="keys")`
을 사용하고, cache miss 때 mutable 값의 key를 기록해 반환 시 그 값만 stdlib deepcopy한다.
immutable JSON scalar와 key는 공유해도 변경할 수 없으며 top-level dict는 항상 새 객체다.
UTF-16/BOM·surrogate 등 parser가 지원하지 않는 기존 유효 입력은 json.loads로 fallback한다.
Python 정수 자릿수 정책이 기본값과 다르면 기존 parser를 사용한다. 행을 큰 array로 합치지
않아 잘못된 JSONL 행을 우연히 정상 JSON으로 받아들이는 문제도 피한다.

전후 코드를 별도 engine/cache로 격리하고 순서를 교대로 바꾼 15회 median:

| 로컬 1-chunk 비용 | 기존 | 수정 | 범위 |
| --- | ---: | ---: | --- |
| cold decode + cache 저장 + 반환 복사 | 36.57ms | 15.64ms | SHA/DB I/O 별도 |
| warm 반환 복사 | 20.65ms | 5.33ms | 실제 DB payload 검증은 계속 필요 |
| cache + 반환 객체의 traced Python retained allocations | 9,212,494B | 6,347,182B | tracemalloc, RSS 아님 |

같은 실제 chunk에서 top-level key 객체는 기존 49,088개, 키 cache 사용 시 26개였다.
SHA/byte/line count 검증 시간은 별도 약 1.4ms였으며 검증을 생략하지 않는다.
원본 값·토큰·DSN은 보고서나 코드에 저장하지 않는다. 실제 payload는 private 로컬에만 있다.
새 의존성·새 cache 예산·RSS 보장은 추가하지 않는다. 기존 64MiB LRU와 8MiB window는
여전히 canonical wire-byte 기준이다.

회귀 범위는 기존 유효/무효 JSONL·NaN/Infinity·float 경계·duplicate keys·인코딩·빈 chunk,
비객체 point 거절, Python 정수 한도, 5,000-point chunk의 cold/warm nested copy 독립성,
SHA/byte/point/GC 및 기존 API/ETag/source-health 계약이다. 운영 전체 응답시간과 RSS 수용은
부모의 배포 후 실제 검증으로 남긴다.

추가 수정의 최종 집중 회귀는 서로 다른 두 시험 묶음 74개 + 91개, 총 **165개 통과**다.
로컬 전체 suite는 반복하지 않고 PR253 새 head의 CI를 실행한다. 화면 QA에서는 위치 카탈로그
대기 중/정보없음 마커를 콘텐츠 로딩 완료로 판정하지 않는다. 다음 배포에서는 카탈로그가
안정될 때까지 기다리거나 데이터 timeout으로 명시하고 별도로 콘텐츠 상태를 기록해야 한다.

## forecast 반환 필드 복사 축소

기준 main은 PR254 merge 33b34533da9f19cf80d1b1d5d669993a0c52ef81이다.
limit은 point가 아니라 위치 수다. 각 위치의 chunk 전체를 검산한 뒤 미래 target을
선별하고 중복 metric의 마지막 값·target 정렬·전체 point의 known_at 최댓값을 구한다.
전역 정렬 때문에 128 chunk를 선적재하는 구조는 아니지만, 현재 metadata만으로 chunk
안의 만료 point를 미리 제외할 수는 없다. corrupt tail 검증도 생략하지 않는다.

DB batch reader에 검산·전체 JSON decode 이후의 선택적 필드 복사를 추가했다. forecast는
출력과 scope 검증에 필요한 8개 필드만 복사해 미사용 observation_evidence의 deepcopy를
제거한다. 원본 decoded cache와 기본 전체 읽기, 선택된 mutable 값의 반환 객체 격리,
SHA/bytes/point count·모든 JSON 행·provider/style/location 검증과 GC shared guard는 유지한다.
파일 backend, DTO, cursor, ETag, TTL, cache 예산, schema와 의존성은 바꾸지 않는다.

로컬 SQLite의 실제 DB artifact store와 publisher 형태의 합성 fixture로 비교했다.
128 chunk·147,200 point·canonical 123,450,880바이트·limit=200을 사용했고, cache를
비운 뒤 기존 전체 복사와 선택적 복사를 교대 실행해 각각 3회 측정했다. 페이지 조립 중앙값은
1.631초에서 1.193초로 줄었다. 6회 모두 응답 SHA와 cursor state가 동일했으며 payload
SELECT는 양쪽 모두 16회였다. SQL 수·payload 전송량 감소나 운영 PG 성능으로 해석하지 않는다.
운영 forecast의 60초 timeout이 해결됐다는 증거는 아직 없으며 실제 배포 후 수용이 필요하다.

새 회귀 12개와 기존 관련 68개, 총 80개가 통과했다. 데이터·중복/정렬·만료/cursor 동등성,
cold/warm mutable 격리, corrupt tail, JSON codec, API ETag/만료·head 삭제 및 GC 경합을
확인했다. 루트 Ruff와 diff 검사도 통과했다. 전체 suite와 frontend 검증은 PR CI에 맡기며
중복 로컬 전체 시험이나 운영 요청·배포는 실행하지 않았다.
