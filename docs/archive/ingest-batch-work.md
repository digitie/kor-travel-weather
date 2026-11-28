# 수집 배치의 중복 연산 축소

정상 source key를 그대로 쓰는 fact는 모델을 다시 복사하지 않고, source identity에서 계산한 raw hash를 내부 저장 검증에서 재사용한다. lineage 조회의 autoflush를 줄이되 배치 내 같은 복합 키를 seen 집합으로 보호한다.

원 run ownership·source identity/alias·raw lineage·fact immutable 비교·FK·projection CAS·retention·transaction rollback을 유지한다. provider 요청, DTO, timeout, cadence와 Common pin은 바꾸지 않는다.

회귀는 PostgreSQL의 새 source/재수집/alias/중복 lineage·충돌 rollback 및 flush/복사/해시 호출 수를 검증한다. 순수 모델 검증과 실제 DB 검증을 구분하며, 운영 CPU 개선 수치는 별도 실측 전 주장하지 않는다.

## 검증 범위

실제 WeatherValue와 새·구 alias AST를 이용한 네 가지 순수 비교에서 값·입력 불변이 같았다. 동일 key5,000개는 복사5,000→0이며 해당 블록만의 tracemalloc peak8,202,552→42,032B였다. 전체 수집 RSS나 운영 CPU 개선율이 아니다. 자체 최초 모형의 필수 필드 누락 실패는 별도로 보존했다.

새 PostgreSQL 회귀4개는 raw hash1회·변경 없는 fact copy0·lineage12개 단일 flush, canonical alias/중복 멱등, provider 충돌 전체rollback, 같은 source key의 raw 충돌 거절을 확인한다. 작성자는 운영·공유·기본 DB에 접속하지 않았으며 실제 DB 실행은 별도 담당의 전용 DB 결과로 기록한다. 기존 source/fact FK flush와 projection CAS SQL은 유지한다.
