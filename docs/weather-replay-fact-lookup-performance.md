# 재수집 fact의 정확한 복합 키 조회 성능
상태: 설계·로컬 비교 준비. 제품 변경 및 운영 적용은 아직 하지 않았다.

## 문제와 범위
기존 source를 재수집할 때 한 배치의 (value_id, known_at) 최대1000쌍을
복합 tuple IN으로 조회한다. 실제 운영 read-only EXPLAIN에서 대량 OR planning
시간 초과가 관측됐지만 이 문서의 새 후보 효과는 아직 로컬에서도 미측정이다.
선행 PR95의 이번 transaction에 새로 삽입된 source 조회 생략은 보존한다.

## 비교할 최소 후보
SQLAlchemy의 typed VALUES를 정확한 키 관계로 사용해 primary key 두 칼럼으로
JOIN하고 같은 chunk의 known_at min/max 경계를 명시한다.
1000개마다 한 번 조회하며 시각별로 쿼리를 늘리거나 ID/시각의 cross product를
가져오지 않는다. 기존 ORM 행·payload 검증·source alias·retention 재시각화·upsert는 유지한다.

## 측정과 검증
공유 로컬 PostgreSQL의 별도 UUID database만 사용한다. 기존 DB나 운영 DB는 건드리지 않는다.
동일 synthetic fixture에서 원 tuple IN과 후보의 SQL bytes, 계획/실행 시간, 반환 정확한 pair,
행 수, partition pruning, CPU/메모리를 비교한다. 동일 시각과 다양한 시각을 모두 다룬다.
그 뒤 정확 pair·중복·경계·1000개 chunk·payload 충돌·retention 회귀를 추가한다.
로컬 측정은 Odroid 효과 또는 3시간 작업 해결의 증거가 아니다.

운영 DDL/timeout/pool/provider quota/동시성 변경은 포함하지 않는다.
구현과 측정 결과는 같은 문서의 후속 커밋에 기록한다.
