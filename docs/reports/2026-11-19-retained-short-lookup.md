# 2026-11-19 — 보존된 단기예보 재진입 조회의 정렬 폭 축소

단기예보의 원 수신 시각이 보존 구간에서 사라졌을 때 기존 fact의 최초 retained
(value_id, known_at)을 찾는 경로만 변경한다. 전체 26열과 payload를 먼저 정렬하던
DISTINCT ON 대신, 요청 ID의 복합 PK 두 열만 materialized CTE에서 고르고 전체 행을
같은 SELECT 안에서 결합한다. 안쪽과 바깥쪽 모두 동일한 ID 조건을 적용한다.

한 문장·한 snapshot, 최초 시각·ID 정렬, 1,000개 ID 상한, 전체 payload와 불변 fact 검증,
원천 clock·lineage, partition/default 재입장 판단과 GC 규칙을 유지한다. 두 SQL 사이에서
행이 삭제될 수 있는 새 간격을 만들지 않는다. 스키마·설정·주기·quota·TTL은 바꾸지 않는다.

## 원인 근거와 범위

운영8a4의 서로 다른 두 시점은 구분한다. 5초 비중단 샘플은 current projection 단계의
DB 대기를 보였다. 이후 소켓과 backend를 맞춘 읽기 전용 조회는 blocker 없는
IO/DataFileRead와 잘린 1,023바이트 query prefix의 MD5를 남겼다. 후자의 prefix는
동일 source를 SQLAlchemy/psycopg로 오프라인 컴파일한 retained-short 조회와
3c1a67ff184b5042cace72a82e96d11b로 일치했다. projection upsert의 prefix와는
일치하지 않았다. 이 사실은 해당 순간의 조회 종류를 특정하며 장시간 수집 전체의
비용 비중이나 모든 DB 대기가 같은 문장이라는 뜻은 아니다.

## 로컬 비교

실제 PostgreSQL의 합성 32일 partition·128,000행·전체 26열/JSON으로 3개 교대 쌍을
측정했다. 최종 안쪽/바깥쪽 조건을 포함한 제품 함수의 컴파일 SQL과 측정 SQL은
CTE 이름 외 동일하며 binding 1,000개도 같다.

| 요청 형태 | 기존 중앙값 ms | 후보 중앙값 ms |
| --- | ---: | ---: |
| 모두 없음 | 122.93 | 44.24 |
| 최근 한 버전 | 127.22 | 88.46 |
| 보존된 여러 버전 | 1023.78 | 101.98 |
| 없는 ID·최근·여러 버전 혼합 | 408.03 | 90.92 |

각 결과의 모든 열·JSON·최초 clock·순서 해시가 일치했다. 여러 버전의 temp read/write는
2019/2023블록에서 0/0, 혼합은 481/482에서 0/0으로 줄었다. 모든 shared read가 0인
warm 비교이고 hit 경우 shared hit는 늘기도 했다. 운영 물리 I/O 감소나 전체 수집 시간의
개선율은 아직 검증하지 않았다. 합성 schema는 시험 뒤 삭제했다.

실제 PG 회귀는 없는 ID·단일/다중 버전·1,001개 batch의 전체 행 동등성과 추가 lazy
조회 없음, 기존 retained 재입장·멱등성·변경된 typed fact 충돌 및 보존 정리 계약을
확인한다. 최종 실제 PG 5건은 70.16초, 순수 보존 단위 71건은 1.72초에 통과했다.
전체 Ruff와 diff 검사도 통과했다. 앞선 통합 실행은 14개 통과 표시 후 180초 제한에
도달했으므로 전체 PASS로 세지 않고 원시 로그를 보존했다.
정확 실행 결과와 원시 계획은 별도 검증 기록에 남긴다. 전체 CI와 운영
정상 이미지 적용 후 자연 수집 관측은 후속이다.
