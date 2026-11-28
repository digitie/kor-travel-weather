# KMA grid lineage 중복 조회 제거

## 원인과 변경

KMA grid 원천을 공유하는 fact마다 `_validate_source_lineage`가 위치 ORM row를
`Session.get`으로 조회했다. SQLAlchemy identity map은 row를 약한 참조로 보유한다.
함수가 반환한 뒤 강한 참조가 사라져 같은 위치라도 다음 fact에서 다시 조회했다.
기존 query-budget 회귀는 generic 원천만 사용해 이 경로를 놓쳤다.

배치마다 위치 ID에 대응하는 `(nx, ny)` 두 값만 보관한다. ORM row와 metadata는
보관하지 않는다. 같은 배치 안에서도 각 원천의 격자는 계속 비교한다. 캐시는
`_ingest_batch_session`의 지역 변수이며 다음 배치·재시도·rollback으로 넘어가지 않는다.
먼저 획득한 위치의 shared advisory / KEY SHARE 잠금과 catalog 쓰기의 exclusive
잠금이 검증 중 좌표 변경을 막는다. 원천 payload·lineage·identity·upsert·게시 경계는 같다.

격자가 아닌 원천은 이 캐시를 채우지 않는다. 캐시 크기는 해당 배치의 grid 위치 수에
비례하며, 수집기의 기본 게시 단위는 50개 위치다. 저장소를 직접 호출하는 사용자의
더 큰 배치에 별도 상한을 추가하거나 전체 수집량을 줄이지 않는다.

## 실제 PostgreSQL 비교

2026-10-15 Linux x86 로컬의 소유 테스트 PostgreSQL에서 이전 코드 `974a76b`와
수정본을 별도 Python 프로세스로 교차 실행했다. 3쌍, 동일한 300개 fact·한 위치·한
grid 원천·고정 시각·동일 DB를 사용했다. 각 실행 전 테스트 테이블만 초기화했다.
입력 생성과 schema 준비는 측정에서 제외하고 `ingest_batch`만 측정했다.
tracemalloc은 양쪽 모두 활성화했다. 같은 호스트의 다른 작업은 완전히 통제하지 못했다.

| 항목 | 이전 | 수정 |
| --- | ---: | ---: |
| 신규 저장 SQL | 311 | 12 |
| 동일 입력 재수집 SQL | 310 | 11 |
| 위치 SELECT (잠금 조회 포함) | 301 | 2 |
| 신규 저장 wall 중앙값 | 0.6228초 | 0.1804초 |
| 신규 저장 Python CPU 중앙값 | 0.4836초 | 0.1408초 |
| 재수집 wall 중앙값 | 0.4720초 | 0.0934초 |
| 재수집 Python CPU 중앙값 | 0.3687초 | 0.0758초 |
| 신규 저장 Python 추적 peak 중앙값 | 2,008,147B | 2,001,839B |
| 재수집 Python 추적 peak 중앙값 | 1,389,625B | 1,358,673B |

세 쌍 모두 source/fact/current projection 개수와 정렬한 내용 digest가 일치했다.
생성·수정·import·projection 시각 같은 관리 시각만 digest에서 제외했다.
각각 최초 300개 저장, 재수집 0개 신규 저장으로 중복이 없었다.
프로세스 peak RSS 중앙값은 신규 저장 89,300→89,196KiB, 재수집
89,940→89,836KiB였다. 이 작은 차이로 유의미한 RSS 감소를 주장하지 않는다.
DB 서버 CPU나 ARM 운영 CPU·전체 job 완료 시간·외부 HTTP 병렬 처리 성능은 이 실험으로
측정하지 않았다.

## 회귀와 남은 운영 검증

- 실제 grid 원천 300개 fact의 query-budget 시험은 이전 코드에서 311 SQL로 실패했고,
  generic 원천은 통과했다. 수정 후 두 경로 모두 통과했다.
- 다른 원천의 잘못된 격자와 다른 위치의 격자는 거부한다. 배치 전체 rollback,
  동일 입력 재수집, 같은 Session rollback 이후 catalog 변경·재검증을 확인했다.
- 위치 잠금·projection·lineage 관련 실제 PostgreSQL 회귀 52개가 통과했다.
- 전체 Python·frontend 확인, 독립 적대 리뷰 2인, ARM 배포와 운영 수집·복구·UI E2E는
  별도 gate다. 위 로컬 측정을 해당 운영 gate의 통과로 집계하지 않는다.
