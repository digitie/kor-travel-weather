# 첫 export 페이지 query 묶음 후속 — 2026-11-19

기준 source는 `a26f63a9b52ba834a6e59a84c5e108e99ca4e3f2`다. 이 변경은 일별 scalar
summary 조회 왕복과 현재 reference ID 바인딩만 줄인다. **1초 첫 페이지 목표는 아직 미달**이다.
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
기존 선택/페이지 승인 수명과 latest/fallback 판단도 바꾸지 않는다. 이 큰 경로는 별도 조사 중이다.

## 작은 변경

- `DailySummaryExports._daily_page`는 같은 RR에서 필요한 location ID를 최대8개씩 조회한다.
  scalar date/slot/summary만 고르고 비공개 hourly_winners/fact/raw는 읽지 않는다.
  공식 최대31일×3구간 기준 scalar 보관 범위는 최대8위치다. 출력은 조회 반환 순서가 아닌 기존
  frozen roster/date/slot 순서로 만들며 byte/limit 중단·cursor g/p·freshness/coverage 검산은 그대로다.
- PostgreSQL 현재 주소 target ID에는 기존 `any_of`의 단일 문자열 배열을 쓴다. provider/status,
  좌표 일치, legal code/region 투영과 비 PostgreSQL IN fallback은 유지한다.
- 새 TTL·본문 캐시·projection/schema/DDL·DTO/OpenAPI·수집·publication 경로 변경은 없다.

## 로컬 짝지은 fixture

실제 로컬 PostgreSQL, 승인된 KMA grid1개/128위치, daily limit2000/days14에서 기존 a26 함수를
별도 module로 읽어 같은 DB와 비교했다. 6회 교대 순서의 median이며 prod cold 상태/전체 원천 수를
재현한 fixture는 아니다. current 주소 변경의 작은 이득을 dominant freeze 해결로 확대하지 않는다.

| 경로 | 변경 전→후 wall ms | CPU ms | 관련 SELECT 수/ID bind |
| --- | ---: | ---: | --- |
| daily 전체 page | 107.222→72.855 | 61.140→45.877 | summary SELECT48→6 |
| current freeze | 23.240→22.026 | 10.889→10.102 | 주소 ID bind128→1, ID 집합128 그대로 |

모든 교대 비교에서 daily item SHA는
`ef4cb7a0adcca3a4943e55e6ef84166347f0bb2cfed65157c27d6a2edcd5b24f`로 같고 current manifest
digest도 변경 전후 같았다. 생성 UUID/승인 시각은 fixture별로 다르므로 실행 간 digest 같음을 주장하지 않는다.

## 회귀 확인과 남은 작업

새5시험은 변경 전 query 개선 조건3개가 RED였으며 나머지 의미 검사는 통과했다. 변경 뒤5개 모두
GREEN이고 전체 관련 export135시험 PASS, Ruff/diff check PASS다. 값·전체 cursor walk·누락/중복,
RR 도중 다른 연결 변경 차단/다음 요청 즉시 반영, 주소 status/좌표/새 주소 반영을 확인했다.
기존 daily/current/API cursor·승인·stale·재사용 시험도 함께 실행했다. frontend 입력은 변경하지 않았다.

Lovelace 오프라인 확인: forecast의 global `weather_export_publication_clock.published_at`은
`manifest.next_since`를 바꾸며 manifest digest→새 snapshot UUID→fresh cache identity 거절→ETag를
바꾸는 충분 원인이다. saved two-limit sample은 items SHA가 같고 next_since/snapshot/ETag가 다르다.
어느 publication/선택 membership이 바뀌었는지는 기록만으로 확정하지 않는다. global watermark의
의미를 없애거나304를 강제하지 않는다. 이 변경은 예보 identity를 수정하지 않는다.

다음 배포 준비는 actual Manager `efda42f8453acd5a13be21f3906442c928eb99c8` provenance에
바인딩해야 한다. 과거2c expected hash를 재사용하지 않는다. backend-only 후속은 frontend Docker
build 입력 전체 byte 동일성과 SDK 지원 provenance를 입증하면 실제 a26 frontend `2615b182…`를
원래 OCI/source/normal receipt 그대로 재사용하는 경로를 우선 검토한다. 새 image/label을 꾸미지 않는다.
