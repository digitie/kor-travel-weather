# 2026-11-03 Weather 읽기 반복 비용 개선

## PR199 CI의 0035 전용 시험 후속

[원 CI41543400028](https://github.com/digitie/kor-travel-weather/actions/runs/41543400028)의 source539d6는
Python 시험 1개 실패·1,647개 통과였고 frontend와 PostgreSQL migration 단계는 성공했다.
유일한 실패는 0035 하루 단위 commit·재시도 시험이 `head`까지 올리면서 마지막 버전은 0035로
기대한 내부 불일치다. 로컬 PG16에서도 같은 기대값 실패를 재현했다.
해당 시험의 upgrade target 두 곳만 0035로 한정했다. 제품 코드·0036 writer는 변경하지 않았다.
최신 head0036은 별도 전체 schema 계약 시험이 계속 검증한다. 관련 세 시험은 CI와 같은
Python3.12/PG16에서 3개, WSL Python3.13/PG16에서 3개, Docker Python3.13/PG16에서 3개 통과했다.
시험 파일 ruff도 통과했다. 원 실패 CI는 보존하며 전체 시험을 새로 통과했다고 보고하지 않는다.
로컬 원문은 WSL `/var/tmp/weather-read-performance-20261103/pr199-*.log`에 보존했다.

현재 통합 소스의 기능을 보존한 `codex/weather-read-performance-20261103`에서 구현했다.
이 문서는 로컬 검증 결과다. 운영 이미지·DB·수집 일정은 변경하지 않았고 push·PR·배포도 하지 않았다.

## 재현과 변경

W1 위치 reference의 첫 페이지는 매번 가장 최근 `locations` snapshot을 찾는다. 기존 조건은
`manifest.selection.kind` JSON 경로식이었다. 큰 JSON은 TOAST에 저장되어도 행마다 다시 해석됐고,
65행 시험에서 다른 종류 64행까지 읽었다. 경로식 인덱스만 추가한 별도 시험에서도 작은 heap의
비용이 싸게 계산되어 순차 읽기를 선택했고, 반복 조회는 77.2ms에서 79.0ms로 개선되지 않았다.
planner를 강제로 바꾸는 방법은 쓰지 않았다.

0036은 `selection_kind` stored generated column을 추가한다. 종류는 manifest에서 발급 시 한 번
계산되며 `(selection_kind, issue_order DESC)` 인덱스를 갖는다. 조회 조건만 작은 컬럼으로 바꾼다.
JSON 원문·digest·발급 순서·만료·nonce·handle은 그대로다. 기존 행도 자동 계산되므로 옛 발급분을
빼는 별도 backfill 완료 시점이나 이중 쓰기 상태가 생기지 않는다. 만료 fallback을 복제할 때에는
DB 계산 컬럼을 INSERT 값에서 제외한다(current·forecast·reference·daily·capacity 공통).

응답에서는 DTO를 검증한 후 ETag가 일치하면 직렬화를 생략한다. 200은 Pydantic의 직접 JSON
직렬화로 중간 전체 dict와 Python JSON 재인코딩을 없앤다. 검증은 304 전에도 수행하며 weak ETag,
상태·cache header·gzip·worker thread는 유지한다. 작은 실수의 지수 표기 `1e-07`/`1e-7`은 달라질 수
있지만 JSON 숫자 값은 같다. cursor나 ETag 계산에 쓰는 원래 항목은 바꾸지 않는다.

일반 예보는 위치의 같은 point 목록을 metric마다 다시 훑던 부분을 한 번의 분류로 바꾼다.
metric·target 정렬, 결측 null, 같은 metric/target의 마지막 값, 지난 예보 생략과 stale 표시는 유지한다.
artifact 무결성 확인이나 공유 잠금을 건너뛰는 캐시는 추가하지 않았다.

## 실제 로컬 측정

WSL의 Python 3.13.14와 격리 Docker PostgreSQL 18에서 동일 fixture를 전후 비교했다.
아래 첫 호출은 OS/DB 캐시를 비우지 않은 첫 함수 호출이며 진짜 cold 운영 요청을 뜻하지 않는다.
운영 Odroid 지연으로 환산하지 않는다. SQL·파일 읽기는 실제 실행이고 원천 내용은 합성 자료다.

| 경로·자료 | 이전 첫 호출 | 이후 첫 호출 | 이전 반복 중앙값 | 이후 반복 중앙값 |
| --- | ---: | ---: | ---: | ---: |
| W1 최신 handle: 65 snapshot, 각 1,451위치·약363KB JSON | 80.8ms | 10.6ms | 77.2ms | 6.2ms |
| 실제 artifact 예보: 25위치×16metric×48시각, 19,200point | 77.1ms | 73.6ms | 74.8ms | 66.2ms |
| 5,000행·약1.03MB 응답 200 직렬화 | 20.4ms | 20.5ms | 18.2ms | 13.9ms |
| 같은 5,000행 조건부 304 | 15.1ms | 7.5ms | 12.1ms | 7.5ms |

W1은 psycopg prepared statement를 사용하고 `force_generic_plan`으로도 검증했다. 실제 EXPLAIN
실행 시간은 73.564→0.039ms, shared hit buffer는 2,212→2였다. 선택한 snapshot_id는 같았다.
일반 예보 전체 출력 SHA256도 같았고, 위 200 fixture는 실제 응답 bytes/SHA256까지 같았다.
숫자·문자·null·Unicode·gzip의 의미 동등성은 별도 회귀에서 확인했다.

측정 원문과 재현 스크립트는 로컬
`C:/Users/digit/.codex/tmp/weather-read-performance-20261103/`의 `before.json`, `after.json`,
`benchmark.py`에 보존했다. QA 실행 로그는 WSL `/var/tmp/weather-read-performance-20261103/`에 있다.
fixture DB 접속 문자열은 그 소유 QA 환경 전용이며 운영 자격 증명은 사용하지 않았다.

## 검증 범위

- WSL 전체 `ruff check .` 통과.
- 기존 export freeze/API·daily 묶음 Docker 67개 통과.
- 최종 추가 회귀 16개는 WSL과 Docker 각각 통과: 다른 종류와 섞인 최신 발급,
  동시 발급의 각 cursor, 만료·catalog 소실 fallback, JSON 값·304 검증과 직렬화 생략,
  generated column의 기존 행 투영·재실행·downgrade·짧은 잠금 상한/GUC 복원.
- 전체 Alembic schema 계약과 daily fallback을 포함한 WSL 집중 묶음 14개 통과.
- 공개 마커의 generic plan·최종 승자 수화·metric 배치·CPU 기존 회귀 23개 통과.
  현재 경로는 요청 위치 PK/배치 조회와 작은 projection을 이미 사용하므로 측정 없는 변경을 섞지 않았다.
- Starlette의 TestClient/httpx 사용 중단 예정 경고 한 종류는 기존 의존성 경고로 남는다.

전체 시험·프론트엔드 빌드·운영 cold UI·실제 수집 처리량은 이번 변경으로 재수행하지 않았다.
UI·API DTO·OpenAPI 계약은 바뀌지 않는다. 원천 스캔, catalog/inventory, publisher 승격의 운영 부하는
이 로컬 개선 수치만으로 해결됐다고 판단하지 않는다.

## 적용 순서와 한계

Root가 정상 배포 경로에서 0036 migration을 API 교체 전에 적용해야 한다.
generated column 추가는 기존 snapshot 표를 한 번 계산하며 table 잠금을 필요로 한다.
잠금 대기는 최대 5초, 문장은 최대 180초이며 호출자가 더 짧게 설정한 상한은 유지한다.
작은 복합 인덱스는 concurrent로 생성한다. 기존 이름·정의·소유 표·상태가 충돌하면 실패로 남기고
기존 객체를 자동 삭제하지 않는다. 원본/이력/기존 cursor를 정리하는 작업은 없다.

현재 운영 성능과 migration 완료는 미관측이다. 이후 실제 이미지 반영·동일 선택의 첫/반복 페이지·304
관측은 Root의 운영 후속이다. 새 백업·복원·독립 리뷰를 선행 조건으로 추가하지 않는다.
