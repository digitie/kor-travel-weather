# 공개 위치 목록의 최소 컬럼 조회

## 문제와 범위

GET /v1/weather/locations는 공개 DTO에서 metadata를 빈 객체로 숨기면서도
목록 조회에서 ORM 전체 행과 JSON metadata를 읽었다. 위치1000개를 읽을 때
큰 내부 JSON·생성/수정 시각을 전송·디코딩하고 ORM 인스턴스를 만드는 비용이 불필요했다.

운영의 위치 조회 지연은 정렬·동시 수집·CPU 경쟁도 포함한다. 읽기 전용 같은1000개
순차 비교는 unordered scalar0.104초, ordered scalar1.559초, ordered ORM1.732초,
ordered scalar0.998초, unordered scalar0.069초였다. 다른 부하와 cache warmup은
통제되지 않아 이 변경의 예상 개선율로 쓰지 않는다. 특히 이름 정렬 비용은 유지된다.

## 변경

repository 목록의 기본값은 include_metadata=True를 유지한다.
공개 목록 endpoint만 False를 전달하고 location_id/name/위도/경도/grid2개/
region_code/enabled의8개 컬럼을 읽는다. 기존 WeatherLocation 검증과 공개 DTO 변환을
그대로 거치며 metadata는 이전과 같은 빈 객체다. 공개 DTO에는 created_at/updated_at이
없다. 날짜의 UTC 변환 계약이나 OpenAPI/TypeScript 스키마 변경은 없다.

이름·ID 순서, enabled 필터, 공백을 다듬은 이름/ID 검색, limit/offset,
별도 count와 envelope는 유지한다. admin 목록과 수집·단건·주변 위치 경로는 바꾸지 않는다.
DB schema/index/COLLATE/timeout/pool/수집량·스케줄은 바꾸지 않는다.

## 확인할 회귀

- 동명이름 ID 순서, null grid/region,6자리 좌표, 검색/페이지/count/빈목록.
- 공개 enabled=false를 보내도 비활성 위치가 나오지 않는 기존 계약.
- 실제 SQL 목록+count2회, ORM load0 및 반환 컬럼에 metadata/날짜 부재.
- 관리자 응답의 metadata 보존.

공개 API 회귀6개를 실제 PostgreSQL에서 통과했다. 같은 테스트는 고정 원본e25에서
ORM load가 발생해 실패했고, 변경 후에는 SQL2회·ORM load0과 기존 DTO를 확인했다.
전체 Python·frontend 검증은 후속이다. 테스트는 기존15432 PG의 UUID 전용 새 DB를 써
기존 weather_test 데이터를 건드리지 않는다.
초기 새 schema 검증은 기존 partition catalog가 schema를 한정하지 않아
다른 schema의 같은 이름 부모를 읽고 empty range를 만드는 문제로 거절됐다.
기존 데이터나 partition을 수정하는 방식으로 우회하지 않는다. 이 문서는 운영 배포,
동일 부하 성능 개선, live UI 통과를 주장하지 않는다.
