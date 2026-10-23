# 승인 source vector와 제한된 메타데이터

PR164는 위치 수와 원천 수의 상한을 분리하고 승인 참조·계획 JSON의 반복 적재를 줄인다.
PR162 `f50a4584f28aa7f796ce404c16da38576812ec8f`에서 시작했고, 제품 후보
`e54e6b83d449bea1cb4f3140013d354c864b1930` 뒤 PR163 main
`2adabca2c4a14eef6b2230d87bfe501ec62a9a0f`를 통합했다. PR163의 선택별 건강도·W5
fallback 판정을 보존하고 cohort namespace 계산만 위치 반복문 밖으로 옮겼다.
API DTO/OpenAPI/TS·schema·원천 호출·운영 설정은 바꾸지 않았다.

## 운영 count와 조건부 크기

작은 참조표를 READ ONLY TX, statement3초/lock1초로 조사했다. 활성 catalog1,450곳·
서로 다른 KMA grid1,076개, Air672/산악454/청정넷67/KREX187/KHOA49를 확인했다.
운영 schema는0022이고 export inventory/head 표가 아직 없다. **실제 승인 namespace·
쿼터 잔량은 미측정**이다. history/fact 전체 조회나 실제 원천 호출은 하지 않았다.

외부3종이 회전으로 전체 위치에 도달하고 SDK 후보도 승인된다는 조건에서 current는
1,076 + 3×1,450 + 672 + 454 + 67 + 187 = **6,806 namespace**, 위치 membership은
**7,180행**이다. KMA+외부3종만으로도5,426개다. 이는 namespace 실측이나 모든 SDK 응답의
승인 가능성 증명이 아니다. 이 조건부 크기가 기존 vector5,000을 넘으므로 별도 제한을 둔다.

## 적용한 제한

| 대상 | 상한 |
| --- | --- |
| 승인 source vector | 8,192개 |
| catalog/page·개별 provider/dataset cohort | 기존5,000 유지 |
| inventory 작은 scalar projection | 기존20,000행 유지 |
| plan descriptor | attempt마다1회, JSON각256KiB/전체8MiB |
| generation refs | 전체65,536행/JSON전체8MiB, location_ids JSON각64KiB |
| 새 frozen manifest | 정규화 JSON8MiB에서 상태 표시용128B 예약 |
| artifact/page/readers | 기존 chunk8MiB·5,000points/page4MiB/동시 reader2개 유지 |

current/forecast refs와 별도 계획 JSON은 같은 RR에서 named server cursor로64행씩 읽는다.
SQL CASE가 과대한 JSON을 먼저 NULL로 투영하고 overflow flag를 반환해 드라이버의 대량 복원을
막는다. 정상 legacy refs.location_ids=NULL은 보존하며 cursor는 예외에도 닫는다.
inventory 첫 조회는 작은 scalar와 plan 존재성만 읽는다. 활성 위치가 참조하는 서로 다른
attempt의 descriptor를 한 번씩 읽고 예산 확인 후 parse/cache한다. plan 유실·namespace
불일치·좌표/anchor 변경·epoch/원천 건강도 판정은 fail-closed다. 파일 IO 중 TX 연결 반환과
공유 guard 수명을 유지한다.

예산은 digest와 같은 ensure_ascii=False/sort_keys/compact separators/default=str/
allow_nan=False JSON의 UTF-8 길이다. scope·전체 manifest는 iterencode로 누적하며 중기
reader가 추가하는 위치 index도 최종 검사한다. SQL로 개별64/256KiB가 확인된 ref/plan만
같은 encoder의 C encode 경로로 빠르게 센다. 개별·누적 제한과 잘못된 JSON/nonfinite 거절은
두 경로가 같다. 일반 scope나 임의의 큰 manifest에는 이 빠른 경로를 사용하지 않는다.

capacity 초과 시 새 정상 snapshot 대신 이전 값에 capacity_limited 상태를 붙인 **별도 불변
fallback handle**과 generation refs를 한 TX에서 저장한다. 같은 최신 fallback은 TTL 동안
재사용하고 그 커서의 다음 페이지도 stale다. 이전 healthy ETag는200 stale 응답으로 바뀐다.
정상 freeze가 다시 성공하면 표시 없는 새 issue_order/handle로 회복한다. 원래 generation·
원천 시각·healthy handle은 변경하지 않는다. 128B 예약은 fallback 상태의 추가 공간이다.

## 격리 검증

- 후보 e54e6b8에서 capacity·실제 current/forecast API fallback·기존 frozen API·중기·
  inventory·reader 수명/파일 IO pool 반환 **65 PASS /208.46초**.
- PR163 통합 뒤 SDK discovery/일간 source/capacity/fallback **24 PASS /44.24초**.
- 마지막 SQL-bounded fastpath의 동일 JSON/개별·누적 상한/NaN/Unicode 및 fallback
  **19 PASS /33.10초**, 전체 Ruff PASS. 위 실행 사이에 겹치는 시험이 있으므로 합산하지 않는다.
- 경계는 실제 named cursor/64행·legacyNULL·과대 refs·plan 유실/중복 attempt·정확한 JSON
  예산/128B 예약·최초503·stale 커서·ETag·반복 handle 재사용·정상 회복을 포함한다.
- 앞선 구형 KHOA venv collection 실패와 신규 fixture의 metadata 속성 오류2건은 통과로
  세지 않았다. pinned SDK 제품 소스와 같은 venv에서 재실행했다.
- 정확 CI·최종 두 독립 리뷰·실제 격리 HTTP/UI 결과는 PR에 남긴다. 운영 수용은 아니다.

### 정확 CI의 경합 시험 보정

722a42d의 CI39978620239는1269PASS/1FAIL(584.35초)이었다. 실패는 기존 일간 reducer
동시성 시험에서 processed=[0,1]은 만족했지만 race=1을 강제하지 못한 것이다. PR162 이후
공개 SH guard가 CAS까지 감싸므로 guard 종료 뒤의 barrier는 두 worker가 같은 checkpoint를
읽도록 강제하기에 늦었다. 제품 코드는 변경하지 않고 실제 artifact read를 모두 소진한 직후,
SH guard 내부/CAS 이전에서 두 reader를 만나게 수정했다. processed=[0,1]·race1·후속
processed0 단언은 모두 유지했다. 새 소유 PG에서 같은 회귀를5회 연속 실행해 각각 PASS했다.
기존 CI 실패를 통과로 세지 않으며 새 정확 CI가 필요하다. 제품 tree가722a42d와 같으므로
그 제품으로 검증한 실제 격리 HTTP/UI의 근거는 유지한다.

## 합성 크기와 비용

별도 소유 PG 임시표에서 실행했다. 운영 ARM·전체 API 비용이 아닌 단일 표본이다.
refs 조회→scope/manifest 생성→digest/JSON bind 상당 직렬화의 결과:

| shape | 정규화 manifest | 시간(계측 없음) | Python peak(tracemalloc) | ref SQL |
| --- | ---: | ---: | ---: | ---: |
| 6,806scope/6,806refs | 5,234,815B | 0.484초 | 38,848,296B | 1 |
| 8,192scope/17,792refs | 8,375,125B | 0.905초 | 63,537,026B | 1 |
| 8,192scope/17,992refs | 예산 초과 거절 | 별도 미측정 | 21,894,824B | 1 |

첫 두 shape의 tracemalloc 활성 시간은2.798/4.850초다. cursor는 모두 named/64행이다.
두 번째는 최대 source 수이고8MiB 예약 경계에 가까운 허용 shape다. 임의의 모든 입력에서
전체 API peak가 이 값 이하라는 증명은 아니다. inventory/catalog/head/health SQL·파일·
원천 HTTP는 이 실험에 포함하지 않는다.

별도 plan projection+parse/namespace cache 비교:

| shape | JSON 텍스트 길이 합 전→후 | Python peak 전→후 | 시간 전→후 |
| --- | ---: | ---: | ---: |
| 중기1,000곳×2part | 80,743,000→80,743B | 175,544,515→758,537B | 1.032→0.018초(계측 포함) |
| 6,806plan/7,180membership | 2,776,256→2,643,500B | 24,317,775→24,309,753B | 0.126→0.242초(계측 없음) |

중기 실험의 위치명은 UUID36이다. JSON 길이는 해당 열만의 합이며 전체 네트워크 전송량이
아니다. 분리 조회는1→2 SQL로 늘었다. 큰 공유 계획은 반복 복원이 줄지만 작은 계획 다수는
검사 비용으로 시간이 늘었다. 대표6,806 shape의 tracemalloc 활성 시간도0.564→1.078초다.
SQL-bounded C encode 전 대표 분리 조회는0.384초였다. **전체 수집/API CPU 저감이나 전반적인
속도 개선으로 주장하지 않는다.** 승인 크기를 수용하면서 메모리를 제한하는 변경이다.

원시 합성 측정 JSON:

- [refs/manifest](../reviews/evidence/weather164-capacity-bench.json)
- [중기 공유 계획](../reviews/evidence/weather164-plan-projection-bench.json)
- [대표 source 계획](../reviews/evidence/weather164-representative-plans-bench.json)

운영 activation·ARM CPU/RSS/latency 비용·실제 Map consumer 수용·GC/unlink/terminal 메타데이터
보존 정리는 별도 gate다. 기본 flag OFF를 유지하며 실제 GC나 원천/이력 정리는 구현하지 않는다.
