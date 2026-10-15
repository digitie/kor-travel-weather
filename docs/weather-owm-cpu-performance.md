# OWM forecast 로컬 CPU 분석

## 범위와 기준선

기준은 Weather89의 e25e6ca7bc7cca8d5348036b0fa831a41c4e66a4다.
350개 합성 위치마다 40개 예보 행, 위치당 330개 fact를 실제 정규화·저장 경로에 전달한다.
외부 HTTP와 quota 대기를 fixture로 대체하고 소유 UUID PostgreSQL만 사용한다.
115,500개 fact와 350개 원천의 개수·내용 digest를 비교한다.
실제 운영 수집·Odroid CPU·공급자 응답 시간과 같은 측정으로 취급하지 않는다.

최초 cProfile 측정에서 wall 80.625초, Python client CPU 51.562초,
프로세스 peak RSS 167,288KiB였다. cProfile 기본 시계는 wall이며 함수별 누적 시간을
CPU로 해석하거나 서로 더하지 않는다. PostgreSQL 서버 CPU는 측정하지 않았다.

키 정규화가 약 382만 번 실행되었다. 소문자 ASCII 키의 결과가 이미 정규식 변환과
같은 경우에만 정규식을 생략하는 후보를 비교한다. 캐시나 redacted payload 공유는
추가하지 않고 중첩 원천 필드의 독립 복사와 credential 제거 계약을 유지한다.
공급자 요청 수, pacing, 병렬도, DB schema, 게시 단위는 바꾸지 않는다.

## 전체 pipeline 교차 비교

cProfile을 끄고 이전·수정 코드를 별도 프로세스로 old/new, new/old, old/new 순서로
3쌍 실행했다. 같은 고정 UTC 입력과 전용 DB를 사용하며 실행 전에 이 DB의 측정 테이블만
초기화했다. 버퍼와 로컬 데스크톱 백그라운드 부하는 완전히 통제하지 못했다.
필수 test suite는 측정 후 실행하여 이 작업 자체의 동시 부하는 피했다.

| 중앙값 | 이전 | 수정 |
| --- | ---: | ---: |
| wall | 53.2504초 | 51.5106초 |
| Python client CPU | 23.0117초 | 21.2426초 |
| 프로세스 peak RSS | 167,448KiB | 167,292KiB |

Python client CPU는 약 7.7%, wall은 약 3.3% 감소했다. PostgreSQL 서버 CPU와 외부
HTTP는 포함하지 않는다. RSS 156KiB 차이로 의미 있는 메모리 감소를 주장하지 않는다.
새 캐시나 장기 보관 객체는 없다.

6회 모두 fact·원천 개수와 정렬한 내용 digest, projection 개수, SQL 종류별 개수가 같았다.
projection 내용 digest는 별도로 수집하지 않았다. 350회 요청과 115,500개 fact를 그대로
유지한다. CPU를 줄이는 작은 변경이며 실제 3시간 이상 job 지연을 해결했다고 주장하지 않는다.
기존 fact 조회·projection SQL과 HTTP pacing 등 다른 비용은 남아 있다.

순수 redaction만 같은 호출량으로 비교한 3쌍에서는 CPU 중앙값 3.8060→1.9511초였다.
이 약 48.7%는 해당 단계만의 비율이며 전체 pipeline이나 운영 CPU에 적용하면 안 된다.
함수별 profile·SQL·flush의 포함 시간도 서로 합산하지 않는다.

수치·각 표본·소스 SHA·측정 한계는
[로컬 측정 증거](reviews/adversarial/evidence/2026-10-16-owm-cpu-local-measurements.json)에
보존한다. private 원본 최초 profile의 self_cpu_profile_s/cumulative_profile_s 필드명은
잘못된 wall 이름이므로 원문을 바꾸지 않고 후속 producer의 이름과 해석을 정정했다.

## 검증과 남은 gate

- Unicode와 혼합 키, credential 별칭 및 중첩 mutable payload의 결과 동등성.
- 동일 입력의 교차 실행 3쌍: Python CPU·wall·peak RSS·SQL 개수 및 저장 digest 비교.
- 루트 ruff·전체 pytest·frontend type-check 및 test.
- 독립 적대 리뷰 2인과 실제 배포·운영 검증은 별도 gate다.
