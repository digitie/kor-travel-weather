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

## 검증 계획

- Unicode와 혼합 키, credential 별칭 및 중첩 mutable payload의 결과 동등성.
- 동일 입력의 교차 실행 3쌍: Python CPU·wall·peak RSS·SQL 개수 및 저장 digest 비교.
- 루트 ruff·전체 pytest·frontend type-check 및 test.
- 독립 적대 리뷰 2인과 실제 배포·운영 검증은 별도 gate다.
