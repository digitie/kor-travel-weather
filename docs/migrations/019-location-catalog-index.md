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
대상 DB와 기존 revision 0018_sync_run_owner를 확인한 후 별도 migration 명령으로 적용한다.
PostgreSQL CREATE/DROP INDEX CONCURRENTLY와 뒤따르는 Alembic revision 기록은 하나의
트랜잭션이 아니다. 중간 중단 후에는 원 실행의 종료와 다른 migration의 부재를 확인하고,
같은 대상 DB에서 revision과 신규 인덱스의 존재·정의·indisvalid·indisready를 읽어 정산한다.
관측이 불확실하거나 정의가 다르면 stamp/drop/재실행 없이 원인을 확인한다.

이 revision이 추가하는 인덱스는 public.weather_locations의 비고유 btree이며,
key는 기본 collation·기본 오름차순의 (name, location_id), predicate는 enabled IS TRUE,
INCLUDE는 latitude, longitude, nx, ny, region_code, enabled다.
pg_get_indexdef와 pg_index·pg_attribute·pg_collation을 대조해 테이블·키 순서·collation·
predicate·INCLUDE·valid/ready가 모두 이 정의와 일치하는지 확인한다. 이름만으로 승인하지 않는다.

- revision18 + 인덱스 없음: upgrade를 다시 실행한다.
- revision18 + 정확한 신규 인덱스 VALID/READY: CREATE는 완료됐고 revision 기록이
  남은 상태다. 데이터와 정의를 확인한 뒤 이 인덱스만 추가하는 revision19에 한해
  alembic stamp 0019_location_catalog_index로 정산한다. 정의가 다른 인덱스를 stamp로 숨기지 않는다.
- revision18 + 신규 인덱스 INVALID: 진행 중인 build가 없고 이 migration의 정확한
  테이블·키·predicate·INCLUDE 정의임을 확인한 뒤 해당 신규 인덱스만
  DROP INDEX CONCURRENTLY public.ix_weather_locations_enabled_name_id로 정리하고 upgrade를 재실행한다.
  IF NOT EXISTS로 INVALID index를 성공 처리하지 않는다.
- downgrade 중 revision19 + 인덱스 없음: DROP은 완료됐고 revision 기록이 남은 상태다.
  이 revision의 유일한 변경이 해당 인덱스임을 확인한 뒤
  alembic stamp 0018_sync_run_owner로 정산한다.
- downgrade 중 revision19 + 정확한 인덱스가 남음: 원 명령의 종료를 확인한 뒤
  downgrade를 다시 실행한다. revision18 + 인덱스 없음이면 이미 downgrade가 완료됐다.

stamp는 위 정확한 revision 쌍과 정의·상태가 확인된 경우에만 수동으로 사용한다.
다른 revision을 건너뛰거나 다른 인덱스·fact/source/위치 데이터를 삭제하지 않는다.
성공 후 기본 query 실행 계획, 응답 시간과 이름/ID 순서·row 수를 확인한다.

이 변경은 운영 504 전체 원인이나 다른 수집의 CPU 병목을 해결했다고 주장하지 않는다.
운영 인덱스 적용 후 같은 쿼리와 UI를 측정하기 전에는 효과 미검증이다.
