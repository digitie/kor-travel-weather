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
