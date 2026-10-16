# Weather104 marker CPU 독립 적대 리뷰 D

- 판정: SOURCE_PASS. 열린 P0/P1/P2/P3 = 0/0/0/0.
- 대상 HEAD: 568ae8544ecf7296e430cfbd396c4b1765e321de
- base: 37b3dd58632094af64a180644bde25160eabe807
- 전체 5파일 diff SHA256: c2920422e02611afb10ab0623b847204f8d979ce0ee559940d0e366e937b8906
- 본인은 이 제품의 저자가 아니다. 다른 리뷰 원문 미열람. 원본/공유 소스 수정 0.

## 검토 범위와 결과

repository.py 및 API marker_summaries의 기존·변경 본문, 두 테스트 파일 전체, 한국어 측정 문서 전체를 immutable Git archive로 읽었다. typed VARCHAR 배열 bind는 위치 수/문자열을 SQL 구조와 분리한다. 기존 alert expanding IN도 SQLAlchemy cache key의 구조는 유지한다. 요청별 bind 추출 값이 서로 다르고 인용문자·Unicode가 SQL literal에 들어가지 않음을 직접 확인했다. SQL cache가 실제 production engine에서 hit하는 횟수나 CPU 절감량을 측정한 것은 아니다.

observed의 위치×고정 metric 조합은 유지된다. fallback의 다중 unnest는 같은 Python missing_pairs에서 생성한 동길이 두 배열의 zip이며, 일부 observed pair만 존재하는 모형에서 정확한 나머지 pair만 남았다. 복합 fact key(value_id, known_at) 재결합과 고정 partial-index literal/observed predicate/내부 LIMIT도 보존된다. 입력 빈 목록은 DB 경로에 진입하지 않는다. PostgreSQL 실행·generic plan의 결과 동등성은 이번 리뷰에서 재실행하지 않았다.

API는 shared mapping identity일 때 같은 immutable 목록을 한 번 분리하고, 별도 alert mapping인 호환 경로에서는 기존 filtered_alerts-or-latest_alerts 선택을 유지한다. latest에 이전의 동일 분리 결과를 재사용하므로 DTO·권한·취소·보존 정책을 바꾸지 않는다. active_alert_values의 시간 경계 두 번 평가가 한 번으로 줄지만 원래 동일 묶음이어야 하므로 새로운 데이터 손실 반례를 찾지 못했다.

## 직접 검증

- 독립 archive + --noconftest/-c /dev/null: test_marker_cpu/test_marker_generic_plan 중 순수 **12 PASS, 실제 PG 1 deselect**, 1.22초.
- 자체 실제 함수/SQLAlchemy 모형: partial pair zip, composite PK, partial literal, request cache key, 새 bind 값, quoted/Unicode 입력, empty-no-query **7경계 PASS**.
- 로그: weather104-review-d-pure-r1-20261016.log / weather104-review-d-model-r1-20261016.log.
- 모형: /home/digitie/dev/.weather104-review-d-568ae85/own_adversarial_d.py.
- production 설정과 서로 다른 임의 admin/metrics token을 사용했다. 원 conftest/DB/SDK instance/HTTP/provider/SSH/운영 Docker 접근 0. 병행 Transport Docker 시험은 별도 승인된 작업이다.

## 증거 한계

저자 16순수/3RED1PASS는 본인 재검증 수와 합산하지 않았다. 문서의 3.28~3.94초는 프로파일 오버헤드가 있는 Python 측정이며 HTTP 전후 효과가 아니다. 운영 marker 25초/504 해결, 실제 PostgreSQL custom/generic 실행 결과, 최종 전후 CPU·메모리·동시성 효과는 **미검증**이다. 이 소스 승인은 운영 배포·성능 개선 증명이 아니다.
