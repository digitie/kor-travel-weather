# 재수집 fact의 정확한 복합 키 조회 성능

상태: PR95 위의 최소 조회 변경과 로컬 회귀·계측 완료. 운영 적용·효과 검증 전이다.

## 문제와 변경 범위

기존 source를 재수집할 때 (value_id, known_at) 최대 1,000쌍의 tuple IN을
사용했다. partition마다 대량 OR 조건이 계획에 펼쳐질 수 있다. 실제 운영의
read-only EXPLAIN에서 같은 시각 1,000쌍의 원 쿼리는 6.374초 뒤 취소됐고,
시각 equality와 ID IN 후보는 0.476초에 계획을 반환했다. 운영 SELECT 실행
시간이나 이 변경의 실제 개선율을 측정한 결과는 아니다.

이번 변경은 같은 시각이면 known_at equality와 ID IN으로, 시각이 다르면
typed VALUES를 primary key 두 칼럼으로 JOIN하여 정확한 pair만 조회한다.
혼합 시각은 명시적 known_at min/max 조건도 넣어 범위 밖 partition을 제외한다.
deduplicate한 재수집 키 1,000개마다 쿼리는 한 번이다. 시각별 N회 조회나
ID와 시각 cross product의 불필요한 행 읽기는 하지 않는다. NULL known_at은
NOT NULL PK와 일치할 수 없어 조회 대상에서 제외한다.

선행 PR95의 새 source 조회 생략, 기존 ORM 행의 모든 필드·payload 검증,
source alias, 동일 배치 중복, retention 재시각화, lineage와 upsert는 보존한다.
API DTO, schema/index, pool/timeout/provider quota, 수집 횟수·동시성은 바꾸지 않는다.

## 로컬 비교와 메모리 경계

PostgreSQL 16.14의 독립 임시 컨테이너에 65일 partition, 32,500행,
192자 payload 본문을 만들었다. CPU 1개, 메모리 512MiB, shared_buffers 64MB
제한이다. 같은 시각은 요청 1,000개 중 500행, 다양한 시각은 1,000행을 반환했다.
두 경로 모두 예상 pair 집합과 digest가 일치했다. 운영 전체 WeatherValueRow의
폭·payload 분포·동시 부하를 재현한 fixture는 아니다.

| 입력/후보 | 계획 중앙값 | 실행 중앙값 | Python traced peak 최대 | SQL/인수 |
| --- | ---: | ---: | ---: | --- |
| 같은 시각/VALUES JOIN | 0.290ms | 0.324ms | 2,544,608B | 68,362B/2,002 |
| 같은 시각/equality + ID IN | 0.358ms | 0.120ms | 756,519B | 29,135B/1,001 |
| 다양한 시각/VALUES JOIN | 1.347ms | 6.612ms | 2,543,160B | 68,362B/2,002 |

각 3회, PostgreSQL EXPLAIN ANALYZE로 로컬 후보만 측정했다. 같은 시각은
1개 partition, 다양한 시각은 해당 범위의 65개 partition을 읽었다.
Python traced peak는 SQL 구성과 계측 중의 Python allocation이며 backend RSS와
같은 지표가 아니다. 클라이언트 wall time에는 compile/tracemalloc 계측도 들어가므로
서버 실행 시간·운영 지연의 개선율로 해석하지 않는다.

별도 후보 1회씩의 backend CPU는 VALUES 두 입력에서 각각 1 tick(해상도 10ms),
같은 시각 ID IN에서 0 tick이었다. 이는 CPU가 0이라는 의미가 아니다.
동일 connection의 누적 VmHWM은 마지막 혼합 시각 조회 후 35,168KiB였고,
컨테이너 누적 memory.peak는 211,529,728B, oom/oom_kill/max counter는 모두 0이었다.
전후 RSS·누적 high-water이므로 순간 allocation 감소율을 뜻하지 않는다.

### 원 쿼리의 로컬 실패를 별도로 보존

처음 공유 로컬 512MiB PostgreSQL의 별도 database에서 다양한 시각의 기존
대형 tuple IN을 실행했을 때 backend가 memcg OOM으로 SIGKILL됐고 postmaster가
다른 backend를 종료한 뒤 재초기화했다. 컨테이너 재시작은 없었다.
이를 확인한 뒤 공유 서버를 다시 사용하지 않고 전용 컨테이너로 전환했다.
위 후보 성공 측정은 그 이후 독립 서버에서 수행했으며, 위험한 기존 쿼리는 반복하지 않았다.
운영 PostgreSQL은 별도 3GiB 제한과 별도 관측에서 OOM counter 0이므로 이 로컬
실패로 운영 OOM이나 전체 장시간 작업의 원인을 단정할 수 없다.

## 검증

안전한 SQL 형태 모형으로 기존 구현 3 FAIL/1 PASS를 확인했고, 변경 후
실제 PostgreSQL 정확 pair를 포함한 신규 6개 시험과 Ruff가 통과했다.
같은 value_id의 다른 known_at, UTC/KST 동등 시각·일 경계, NULL clock,
중복·새 source 필터 및 1,000개 chunk를 검사한다.

전체 Python 회귀의 첫 시도는 측정 서버의 max_connections=10 및
statement_timeout=2s 때문에 연결 슬롯과 의도적 lock 대기 시험에 실패하여
중단했다. 이를 제품 회귀로 판정하거나 PASS로 집계하지 않는다.
독립 시험 환경에서 전체 검증을 마친 결과를 후속 기록한다.
frontend type-check와 시험은 통과했다.

실제 Odroid의 수집 중 DB 부하, 지도 목록 지연, OWM 장시간 실행 개선은
아직 미검증이다. 운영 비교에서는 같은 입력의 planning/execution, DB CPU/메모리,
배치 완료 시간과 다른 서비스의 지연을 각각 수집해야 한다.
