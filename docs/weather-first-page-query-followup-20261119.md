# 첫 export 페이지 query 묶음 후속 — 2026-11-19

기준 source는 `a26f63a9b52ba834a6e59a84c5e108e99ca4e3f2`다. 이 변경은 일별 scalar
summary 조회 왕복, 같은 RR의 중복 metadata 조회와 현재 reference ID 바인딩을 줄인다. **1초 첫 페이지 목표는 아직 미달**이다.
큰 current/daily freeze를 해결했다고 주장하거나 이 작은 배열 변경만으로 재배포하지 않는다.

## 실제 관측과 조사 범위

기존 a26 운영 관측 6프로필은 191.379초/timeout0/inflight없음으로 완료됐고 source/image/container가
전후 동일했다. 모든 first sourceStatus는 stale이며 forecast 조건부는 두 limit에서 실제200이었다.

| 자원/limit | first ms | freeze 관측 합계 s | SQL 관측 합계 s/수 |
| --- | ---: | ---: | ---: |
| current/200 | 33565.745 | 29.195 | 17.743/82 |
| current/2000 | 22182.104 | 18.824 | 9.059/138 |
| daily/200 | 6320.624 | 5.921 | 1.826/31 |
| daily/2000 | 14989.797 | 10.206 | 7.923/76 |

stage는 자원별 전역 aggregate context이며 중첩된다. 자기 요청별 귀속이나 합산 wall 분해가 아니다.
current/200 render 약9ms/artifact 약446ms, daily artifact0은 큰 첫 비용의 freeze/query 조사를
뒷받침하지만 개별 SQL 계획·CPU 병목을 확정하지 않는다. 새 prod profiler/GET/DB조사는 하지 않았다.

현재 freeze는 같은 RR에서 활성 catalog/승인 inventory/불변 plan·generation·refs/주소를 읽어
전체 manifest를 검산하고 digest를 만든다. 기존 page-liveness epoch는 DELETE/TRUNCATE만
포착하므로 이것만으로 publication·catalog·주소 변경을 숨기는 전체 freeze 캐시를 만들 수 없다.
기존 선택/페이지 승인 수명과 latest/fallback 판단도 바꾸지 않는다. 이번 변경은 같은 RR의 catalog/progress 중복만 제거하며 전체 freeze를 재사용하지 않는다.

## 작은 변경

- `DailySummaryExports._daily_page`는 같은 RR에서 필요한 location ID를 최대8개씩 조회한다.
  scalar date/slot/summary만 고르고 비공개 hourly_winners/fact/raw는 읽지 않는다.
  공식 최대31일×3구간 기준 scalar 보관 범위는 최대8위치다. 출력은 조회 반환 순서가 아닌 기존
  frozen roster/date/slot 순서로 만들며 byte/limit 중단·cursor g/p·freshness/coverage 검산은 그대로다.
- PostgreSQL 현재 주소 target ID에는 기존 `any_of`의 단일 문자열 배열을 쓴다. provider/status,
  좌표 일치, legal code/region 투영과 비 PostgreSQL IN fallback은 유지한다.
- 일별 승인 catalog 전체 투영은 같은 RR/connection에서 한 번 읽고 key 목록과 coverage에 함께 쓴다.
  성공한 freeze progress만 같은 page에서 재사용하고 cursor/fallback은 자체 checkpoint를 검산한다.
  요청 시작/종료에 memo를 비우며 다음 요청 변경을 숨기지 않는다. 선언 target도 기존 PG 배열을 쓴다.
- 기존 요청별 `ReadTrace.timings`를 `Server-Timing`으로 내보낸다. worker_queue/pool_admission/sql/
  freeze/metadata/artifact/render 7종 duration/count만 성공200/304에 포함한다. SQL 본문·키는 내보내지
  않으며 중첩 시간은 합산 wall이 아니다. SQL cursor 계측이 전체 fetch/materialization을 뜻하지 않는다.
- 새 TTL·본문 캐시·projection/schema/DDL·DTO/OpenAPI·수집·publication 경로 변경은 없다.

## 로컬 짝지은 fixture

실제 로컬 PostgreSQL, 승인된 KMA grid1개/128위치, daily limit2000/days14에서 기존 a26 함수를
별도 module로 읽어 같은 DB와 비교했다. 초기 query 변경(3f1d45f2)의 6회 교대 순서 median이며 이후 metadata 중복 제거의 추가 속도 수치가 아니다.
 prod cold 상태/전체 원천 수를
재현한 fixture는 아니다. current 주소 변경의 작은 이득을 dominant freeze 해결로 확대하지 않는다.

| 경로 | 변경 전→후 wall ms | CPU ms | 관련 SELECT 수/ID bind |
| --- | ---: | ---: | --- |
| daily 전체 page | 107.222→72.855 | 61.140→45.877 | summary SELECT48→6 |
| current freeze | 23.240→22.026 | 10.889→10.102 | 주소 ID bind128→1, ID 집합128 그대로 |

모든 교대 비교에서 daily item SHA는
`ef4cb7a0adcca3a4943e55e6ef84166347f0bb2cfed65157c27d6a2edcd5b24f`로 같고 current manifest
digest도 변경 전후 같았다. 생성 UUID/승인 시각은 fixture별로 다르므로 실행 간 digest 같음을 주장하지 않는다.

## 회귀 확인과 남은 작업

초기 query 개선 조건3개와 metadata 중복 개선 조건3개는 이전 구현에서 RED였고 의미 검사는 유지됐다.
Server-Timing 추가도 기존 구현에서 부재 RED를 확인했다. 최종 관련 export/API/요청 trace **171시험 PASS
(154.59초)**, Ruff/diff check PASS다. 값·전체 cursor walk·누락/중복, RR 도중 다른 연결 변경 차단/
다음 요청 즉시 반영, cursor/fallback checkpoint와 memo 정리, 주소 필터, trace 동시 요청 분리를 확인했다.

이전 CI44394586817의 두 TTL-only 실패는 첫 forecast target 경계가 completion+60초보다 빠른 경우였다.
TTL-only 시험 clock을 다음 시간05분으로 고정하고 기존 contiguous forecast fixture는 유지한다.
`page_valid_until > completed_at+60` 전제를 확인한 뒤 기존 정확한 deadline 등식도 그대로 검사한다.
controlled clock으로 원래 실패를 재현했으며 별도 target/snapshot 경계 시험과 production guard는 바꾸지 않았다.

Lovelace 오프라인 확인: forecast의 global `weather_export_publication_clock.published_at`은
`manifest.next_since`를 바꾸며 manifest digest→새 snapshot UUID→fresh cache identity 거절→ETag를
바꾸는 충분 원인이다. saved two-limit sample은 items SHA가 같고 next_since/snapshot/ETag가 다르다.
어느 publication/선택 membership이 바뀌었는지는 기록만으로 확정하지 않는다. global watermark의
의미를 없애거나304를 강제하지 않는다. 이 변경은 예보 identity를 수정하지 않는다.

다음 배포는 actual Manager `efda42f8453acd5a13be21f3906442c928eb99c8`와 현재 a26 image baseline에
바인딩한다. SDK609는 모든 role의 새 source SHA를 요구하므로 기존 frontend image를 retag/혼합하지 않는다.
frontend 전체 COPY 입력·recipe·public args와 materialized context mode가 같을 때 기존 persistent builder의
정상 cached build를 사용하고 실제 CACHED timing을 확인한다. Python 두 role도 정상 경로로 만든다.
현재 수집을 임의 취소하지 않고 자연 종료/기존 quiet 절차에 맞춰 적용한다. 첫1초 달성이나 forecast304를
약속하지 않는다. 다음 승인된 실측에서는 새 요청별 Server-Timing과 DB 검증 input byte/ref를 저장하고,
전역 metrics는 background 요청이 포함될 수 있는 문맥 자료로만 남긴다.
