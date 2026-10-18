# 수집 카탈로그 DTO 로드 비용 개선

## 변경

KMA와 외부 provider 수집이 사용하는 list_locations의 metadata 포함 경로는
SQLAlchemy ORM 전체 행 목록을 만든 뒤 WeatherLocation 목록을 만들었다.
읽기 전용 DTO에 필요한 컬럼을 직접 조회하여 ORM identity map과 생성·수정 시각
역직렬화를 제거한다. 공개 API의 metadata 제외 경로와 같은 projection 방식을 확장했다.

metadata(중첩 measurement_point 포함), JSON null의 빈 dict 처리, 좌표 정밀도,
이름·ID 정렬, 활성 필터, 검색과 페이지 경계는 유지한다.
원천 raw payload, lineage, 수집 upsert, heartbeat, provider 호출, 주소 갱신 정책은
변경하지 않는다. DB schema 변경이나 장기 캐시도 없다.

## 동일 조건 로컬 측정

tools/benchmark_location_catalog.py는 실제 repository와 DTO를 import하고, 인메모리
SQLite에 metadata가 포함된 가상 측정소 1,450행을 넣는다.
DB 조회→WeatherLocation 생성→ProviderLocation 생성까지 측정한다.
legacy는 기존 ORM 조회 구현, candidate는 실제 변경 메서드다.
각 모드는 새 프로세스에서 5회씩 교대 실행했고, 모든 1,450 DTO의 JSON SHA256이 일치했다.
원시 결과는 collector-catalog-benchmark-20261018.json에 보관한다.

| 중앙값 | 기존 | 변경 |
| --- | ---: | ---: |
| 프로세스 CPU 측정 구간 | 124.812 ms | 112.296 ms |
| wall time | 124.814 ms | 112.322 ms |
| tracemalloc 측정 구간 peak | 6,444,025 B | 5,413,616 B |
| 프로세스 전체 max RSS | 97,660 KiB | 98,672 KiB |

이 fixture에서 CPU는 약 10.0%, Python 추적 할당 peak는 약 16.0% 감소했다.
전체 프로세스 max RSS는 감소하지 않았으므로 RSS 저감 효과를 주장하지 않는다.
공유 개발 호스트의 다른 테스트 부하가 timing에 영향을 줄 수 있다.
이 결과는 PostgreSQL 운영 쿼리, Odroid CPU, 전체 수집 job 측정이 아니다.
실제 worker의 고정 fixture/단일 수집 구간 전후 CPU·RSS 실측은 배포 후 별도로 수행해야 한다.
Dagster daemon은 켜지 않았고 외부 provider와 운영 DB는 호출하지 않았다.

## 검증 및 남은 수용

- 회귀 테스트: 정렬/활성/검색/페이지/중첩 metadata/JSON null/독립 DTO와 ORM 미생성 확인.
- Ruff 통과. frontend type-check 통과, 214 tests 통과.
- 전체 pytest: 전용 로컬 PostgreSQL에서 실행 중. 초기 기본 로컬 test DB는
  기존 weather_values가 비파티션 테이블이라 collection 단계 실패; 해당 DB를 수정하지 않고
  새 테스트 컨테이너로 전환했다.
- 두 명의 독립 적대 리뷰, merge, 운영 배포 및 live E2E는 root 후속 작업이다.
