# 새 source의 불필요한 기존 fact 조회 생략

수집 배치가 같은 transaction에서 처음 삽입한 source에는 기존 fact가 없다.
`weather_values.source_record_key` FK가 source를 참조하고 fact identity에도 canonical
source key가 들어가기 때문이다. 기존 코드는 이 경우에도 최대1000개 복합 PK씩
`weather_values` 전체 ORM 컬럼을 조회했다. 이번 변경은 `_existing_facts_session`의
조회 대상에 `fact.replayed` 조건만 추가한다. 스키마·정렬·API·주기·수집량·timeout과
전역 PostgreSQL JIT 설정은 바꾸지 않는다.

## 생략이 가능한 경계

- `_record_source_session`이 새 canonical source를 삽입한 배치와 새 fallback source만
  제외한다. 외부 입력의 이름이나 발표 시각으로 새 source를 추정하지 않는다.
- DB에 이미 있던 canonical source, 과거 key에 합쳐진 alias, 별도 transaction에서
  미리 저장한 source는 계속 조회한다. 이전 chunk에서 저장한 source도 마찬가지다.
- 같은 배치에서 처음 삽입한 fact는 기존 `stored_facts` 사전에 들어간다. 따라서 뒤의
  동일 identity는 엄격하게 비교하고 서로 다른 typed 값이면 transaction을 취소한다.
- 보존 기간 아래의 replay는 기존 조회와 재일자 계산을 유지한다. 처음 도착한 오래된
  source를 새 시각으로 임의 승격하지 않는다. 기존 alias의 payload-only 허용 범위도 같다.
- value ID는 SHA256의 앞32hex를 쓴다. 서로 다른 새 source의 강제 PK 충돌은 DB unique
  constraint에서 실패하고 source/fact/current projection 전체를 rollback한다. 기존의
  사전 비교 `ValueError`와 달리 이 경우 `IntegrityError`가 될 수 있으며 자동 재시도나
  기존 fact 덮어쓰기를 하지 않는다.

## 검증

기존 구현에서 새 source의1001facts 게시를 관측하면 불필요한 fact SELECT2개가 발생해
새 회귀시험이 실패했다. 후보에서는 이 조회가0이고 같은 입력의 replay는 조회2개와
loaded0을 유지한다. PostgreSQL에서 새 source/혼합 배치/동일 배치 alias/저장된 alias/
fallback 중복/typed 값 충돌/강제 PK 충돌 rollback을 확인한다. 기존 core와 retention
검증을 포함한 focused58건이 통과했다.

동일5000facts·10sources를 자기 UUID 로컬 PG에서 구 구현/후보3쌍 비교했다.
실행 순서는 구/후, 후/구, 구/후로 번갈았고 입력 시각·객체를 고정했다.
source/fact/current projection의 내용 digest는6회 모두 같았다.

| 측정 | 구 구현 중앙값 | 후보 중앙값 |
| --- | ---: | ---: |
| fact SELECT 수/회 |5|0|
| Python process CPU |1.911초|1.566초|
| ingest wall |2.284초|1.877초|
| tracemalloc peak |37797836B|37683317B|

입력 객체 생성·결과 digest 조회는 측정 밖이다. 메모리 차이는 작으며 입력5000개와
resolved batch를 보관하는 구조를 바꾸지 않았다. PG CPU/RSS/37GB 실데이터 비용은
측정하지 않았다. raw6회는 `reviews/evidence/2026-10-16-fresh-source-probes-benchmark.json`에
보존한다. 초기 측정 준비는 retention 시험이 남긴 DEFAULT row와 새 partition이
충돌해 중단됐다. 자기 DB만 비운 후 별도 r2 측정을 했고 원 실패 로그는 보존했다.

ruff 및 frontend type-check/140tests도 통과했다. 전체 Python 회귀는 실행 중이다.

## 남는 문제와 한계

2026-10-16 운영의 복합 `(value_id, known_at) IN` SELECT가100초 이상 실행된 관측이
있다. 이 패치는 해당 query를 새 source 배치에서 줄이며, replay의 느린 조회까지
해결하지 않는다. 현재 PostgreSQL37GB 데이터·Odroid 부하에서의 효과는 미측정이다.
원격 수치와 로컬 소형 PG 결과를 같은 성능으로 해석하지 않는다.

후속은 대표1000key의 기존 query에 `EXPLAIN`만 적용해 OR 전개/partition pruning/JIT
계획을 확인하고, 필요하면 clock별 grouping 또는 typed `unnest` join 후보와 동일
키 집합·NULL/UTC 시각·중복·실제 plan을 비교하는 것이다. 전역 JIT를 성급히 끄거나
replay의 immutable 검사·lineage·transaction 경계를 생략하지 않는다.
