# 위치 목록 정렬 인덱스

Odroid 운영 DB의 활성 위치 1,450개, heap 770,048 bytes에서 공개 목록과 같은
8개 scalar 컬럼 조회를 측정했다. 기본 en_US.utf8 정렬은 같은 연결에서 두 번 모두
6초 statement timeout을 넘겼다. 진단용 C 정렬은 0.557초(서버 실행 527ms)였다.
기본 계획에는 Seq Scan + Sort가 있었고 JIT는 없었다. 별도 무정렬 조회는
서버 실행 16ms였고 count는 0.378초였다. 서로 다른 시각의 요청을 성능비로 합산하지 않는다.

공개 목록의 정렬 순서와 pagination을 유지하기 위해 DB/컬럼 collation이나 API 쿼리를
바꾸지 않고 기본 collation의 (name, location_id) 부분 covering index를 추가한다.
predicate는 기존 공개 쿼리와 같은 enabled IS TRUE이며 payload/metadata는 포함하지 않는다.
위치 갱신의 추가 인덱스 비용은 있지만 fact/source/projection 테이블의 쓰기 경로는 바꾸지 않는다.

운영에서는 새 이미지가 API/수집 시작 시 migration을 자동 실행하게 하지 않는다.
기존 head를 확인한 후 별도 migration 명령으로 적용한다. PostgreSQL CREATE INDEX
CONCURRENTLY를 사용하므로 트랜잭션 밖에서 실행된다. 중간 실패하면 INVALID index가
남을 수 있다. 상태를 확인하고 해당 신규 INVALID index만 정리한 후 수동 재실행한다.
IF NOT EXISTS로 INVALID index를 성공 처리하지 않는다. 성공 후 indisvalid/indisready와
실제 query의 기본 실행 계획, 응답 시간 및 이름/ID 순서·row 수를 확인한다.
downgrade는 해당 인덱스만 CONCURRENTLY 삭제한다.

이 변경은 운영 504 전체 원인이나 다른 수집의 CPU 병목을 해결했다고 주장하지 않는다.
운영 인덱스 적용 후 같은 쿼리와 UI를 측정하기 전에는 효과 미검증이다.
