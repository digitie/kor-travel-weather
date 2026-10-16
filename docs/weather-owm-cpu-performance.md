# OWM forecast 키 정규화 CPU 개선

## 변경과 보안 경계

Weather89의 e25e6ca7bc7cca8d5348036b0fa831a41c4e66a4 위에서 provider 공통 키 정규화의 반복 정규식을 줄인다.
정확한 기본 str 타입이고 ASCII 소문자이며 hyphen이 없을 때만 이미 같은 결과를 반환한다.
그 외 입력은 기존 정규식 경로를 그대로 사용한다. Unicode·비문자·str 하위 클래스의
변환과 credential 제거 계약을 유지한다. 캐시·payload 공유·장기 보관 객체는 추가하지 않는다.
각 metric의 중첩 payload 독립 복사, 원천/raw lineage·identity·upsert·요청 수·pacing·
병렬도·DB schema·게시 단위는 바꾸지 않는다. KMA 발표 시각과 격자는 건드리지 않는다.

초기 2429c99의 fastpath는 str 하위 클래스의 재정의 가능한 predicate/hash 등을 신뢰했다.
독립 리뷰 W91-D-P2-01에서 기존에 제거되던 credential 값이 남는 회귀를 재현했다.
기본 타입 조건을 추가한 81816d29615325957f0fcfe9971abc422a5c6343는 이를 기존 fallback으로 보낸다.
predicate/hash/contains/equality 네 사례가 이전 코드에서 4 FAIL, 수정 후 기존 회귀를
포함한 6 PASS였다. 실제 provider가 이런 Python 객체를 반환하거나 운영 credential이
노출됐다는 증거는 없고, 초기 후보를 운영에 배포하지 않았다.

## 최종 수정본 교차 실측

실제 수집·정규화·저장 경로에 350개 합성 위치마다 40행을 전달해 위치당 330개,
총 115,500개 fact와 350개 source를 저장했다. 응답당 15,019B, 요청 350회다.
HTTP와 quota 대기를 fixture에서만 대체하고 소유 UUID 로컬 PostgreSQL을 사용했다.
운영 설정은 바꾸지 않았다. 이전 e25와 최종 81816d2를 별도 프로세스로
old/new, new/old, old/new 순서의 3쌍으로 비교했다.

| 중앙값 | 이전 e25 | 최종 81816d2 |
| --- | ---: | ---: |
| wall | 52.1376초 | 51.5728초 |
| Python client CPU | 22.5437초 | 21.3598초 |
| 프로세스 peak RSS | 167,228KiB | 167,336KiB |

CPU 중앙값은 약 5.3%, wall은 약 1.1% 감소했다.
한 쌍에서는 수정본 wall이 더 길었다. 표본 3쌍·백그라운드 부하 미통제인 로컬 x86 결과이며
Odroid·ARM·운영 CPU 개선이나 3시간 이상 job 지연 해소를 입증하지 않는다.
RSS는 의미 있는 개선으로 주장하지 않는다.

6회 모두 fact/source 내용 digest·개수, projection 개수와 SQL 종류별 개수가 같았다.
초기 비교의 fact/source digest와도 같다. projection 내용 digest는 NOT_CAPTURED다.
신규 저장만 비교했으며 재수집·다른 데이터 분포나 동시 부하를 대표하지 않는다.

측정은 cProfile 없이 time.process_time과 perf_counter를 사용했다.
client CPU는 PostgreSQL 서버 CPU를 제외하고 같은 timer instrumentation은 양쪽에 있다.
VmHWM은 PSS가 아니며 시작/import·계측·측정 후의 untimed streaming digest 검증까지
포함하는 프로세스 peak다. 검증과 full test suite를 pipeline 실행 시간으로 더하지 않는다.
각 실행 전 전용 DB 측정 테이블만 초기화했다. 기존 DB/컨테이너는 변경하지 않았다.

## 초기 후보 측정 이력

초기 2429c99의 3쌍 중앙값은 CPU 23.0117→21.2426초(7.7%),
wall 53.2504→51.5106초(3.3%)였다. 순수 redaction만의 CPU는
3.8060→1.9511초(48.7%)였다. 이는 보안 수정 전 후보의 수치이며 최종 코드나
전체 운영 CPU에 적용하지 않는다. 원본은 e4d1846과 별도 private 원문에 보존했다.

최초 cProfile 포함 측정은 wall 80.625초/client CPU 51.562초/peak RSS 167,288KiB였다.
키 정규화 약 382만 회, redaction 약 411만 회가 관측되었다. hash 누적 profile wall은
0.152초/model_copy 0.876초로 지배적 원인으로 제시하지 않는다.
기존 fact 조회·projection·flush 비용과 실제 외부 HTTP 대기는 남아 있다.
최초 private JSON의 self_cpu_profile_s/cumulative_profile_s 필드명은 잘못된 wall 이름이다.
원문은 그대로 두고 후속 producer의 이름과 해석을 정정했다.
함수·메서드·SQL·flush·commit의 포함 시간은 서로 더하지 않는다.

## 확인과 남은 gate

- 초기 e4d의 로컬 전체 Python 479 PASS, frontend 140 PASS/type/ruff PASS.
- 최종 81816d2의 로컬 보안 회귀 6 PASS, provider 관련 46 PASS, ruff PASS.
- core/API/Dagster import가 새 WT를 가리키는지 확인하고 별도 UUID DB만 사용했다.
- 최종 제품 81816d2의 CI python(전체 pytest+ruff)와 frontend(type-check+test) PASS.
  새 CI의 실제 test 개수를 별도 추출하지 않았으므로 483 등으로 추정하지 않는다.
- 작성자 검증은 독립 적대 리뷰나 운영 E2E가 아니다. 최종 독립 FULL 리뷰 2인과
  운영 gate는 root가 별도 확인한다.
- 현재 branch는 Weather89 위의 stack이다. 배포 통합본은 별도 Weather90 API projection을
  함께 보존해야 하며 이 branch만으로 운영 API 이미지를 교체하지 않는다.

각 시료·소스 SHA·원문 SHA·한계는
[버전별 로컬 측정 증거](reviews/adversarial/evidence/2026-10-16-owm-cpu-local-measurements.json)의
initial_rejected_candidate/final_postfix_candidate에 구분한다.
