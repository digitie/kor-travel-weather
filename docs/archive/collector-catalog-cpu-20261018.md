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
import/fixture 준비는 측정에서 제외하고 tracemalloc을 켠 상태의 CPU를 비교했다.
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
Odroid ARM 이미지의 고정 fixture 측정은 아래에 별도로 기록한다. 실제 provider 수집 구간의
CPU·RSS 실측은 여전히 별도 수용이다.
Dagster daemon은 켜지 않았고 외부 provider와 운영 DB는 호출하지 않았다.

## Odroid ARM 이미지의 격리 fixture 측정

배포 전 기존 CS 이미지와 새 ARM64 CS 이미지의 실제 repository 메서드를 각각 실행했다.
양쪽 모두 동일한 가상 측정소 1,450행과 인메모리 SQLite를 사용했고, 새 프로세스로
기존/신규를 교대로 5회씩 측정했다. 모든 DTO JSON SHA256이 같았다.
[원시 10개 표본과 이미지 ID](collector-catalog-arm-benchmark-20261018.json)에 최종
PASS_ARM_ISOLATED 결과를 보관한다. 운영 DB·외부 provider는 호출하지 않았다.

| 중앙값 | 기존 ARM 이미지 | 신규 ARM 이미지 |
| --- | ---: | ---: |
| 프로세스 CPU 측정 구간 | 2,099.349 ms | 1,844.048 ms |
| wall time | 2,127.938 ms | 1,863.569 ms |
| tracemalloc 측정 구간 peak | 6,449,005 B | 5,419,994 B |
| 프로세스 전체 max RSS | 97,048 KiB | 96,640 KiB |

이 고정 fixture에서 CPU는 약 12.2%, Python 추적 할당 peak는 약 16.0% 감소했다.
RSS 차이는 약 0.4%로 작아 실질적인 RSS 저감으로 해석하지 않는다.
첫 r1 실행은 readonly rootfs의 임시 SQLite disk I/O 오류로 실패했다.
r2는 양쪽 컨테이너에 동일한 RAM /tmp를 추가해 재실행한 결과다.
r2 동안 build·UI·rollout은 없었지만 prepare-only SDK 읽기가 잠깐 겹쳤을 수 있는
공유 호스트 측정이다. 서비스 runtime 불변과 daemon OFF를 확인했다.
이 결과는 전체 수집 job, PostgreSQL 운영 workload, code-server idle CPU 또는
장시간 OOM/swap 개선의 증거가 아니다.

## 검증 및 남은 수용

- 회귀 테스트: 정렬/활성/검색/페이지/중첩 metadata/JSON null/독립 DTO와 ORM 미생성 확인.
- Ruff 통과. frontend type-check 통과, 214 tests 통과.
- 최종 코드 전체 pytest: 전용 로컬 PostgreSQL에서 663 passed (204.49초).
  초기 기본 로컬 test DB는
  기존 weather_values가 비파티션 테이블이라 collection 단계 실패; 해당 DB를 수정하지 않고
  새 테스트 컨테이너로 전환했다.
- 제품 소스 8c503fa8c41fefc8b42cdc94d61ff108ceb23a15: 두 명의 독립 적대 소스 리뷰 PASS.
  exact-head GitHub CI python/frontend SUCCESS (run39115767123).
- ARM64 API/CS native build 및 설치된 소스 67개 파일 대조 PASS.
  API 이미지 sha256:3f67ff4dbe8e4d406db16416bc583479f953c307813547412c6a8946923beb58,
  CS 이미지 sha256:48d7bb9253ffdd5e3bd73b82698b2bd44155a00a39af32f5b119fe83adb08205.
  API에는 Dagster 미설치, CS에는 Dagster 설치를 확인했다.
- r4 배포 prepare PASS (93feee3a). 이는 실제 컨테이너 교체 완료 증거가 아니다.
- API/CS 실제 운영 교체 attempt065661ca-7065-484d-979d-7e5f90dd46fa는 PASS다.
  위 native 이미지와 제품 소스를 배포했고 web 이미지는 유지했다.
- 배포 후 WSL Chromium1243 live UI는 36 PASS/0 FAIL/exit0이다.
  로그인·보호 경로·메뉴·날씨 지도 marker 완료·위치/데이터셋·Dagster 개요/실패 상세/
  검색·503/네트워크 오류 복구·320/375/414/768/1440px·로그아웃을 포함한다.
  UI 증거의 source는 위 제품 commit이며 API/CS 이미지 ID도 위 native 이미지와 같다.
  실행 중 role lifecycle 불변과 daemon OFF를 확인했다.
  원문 result.safe.json SHA256:
  abf74859d65220cebf0c695ffbd6de698d1b25d08c39e3e8ec39cfc3f7df72ce.
- 이 수용 기록은 PR #117에 포함한다. 위 UI 오류 주입은 실제 제공자 장애나 자동 수집 복구 시험을
  대체하지 않는다. Kakao 실제 fallback과 전체 주소 갱신 sweep도 이번에 시험하지 않았다.
- Track 1/2 전체 완료를 선언하지 않는다. 실제 수집 workload CPU/RSS와 장시간 안정성,
  지역별 freshness/누락 정산 및 자동 스케줄 수용은 별도다. daemon OFF 정책을 유지한다.
