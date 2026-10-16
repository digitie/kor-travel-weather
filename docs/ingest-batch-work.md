# 수집 배치의 중복 연산 축소

정상 source key를 그대로 쓰는 fact는 모델을 다시 복사하지 않고, source identity에서 계산한 raw hash를 내부 저장 검증에서 재사용한다. lineage 조회의 autoflush를 줄이되 배치 내 같은 복합 키를 seen 집합으로 보호한다.

원 run ownership·source identity/alias·raw lineage·fact immutable 비교·FK·projection CAS·retention·transaction rollback을 유지한다. provider 요청, DTO, timeout, cadence와 Common pin은 바꾸지 않는다.

회귀는 PostgreSQL의 새 source/재수집/alias/중복 lineage·충돌 rollback 및 flush/복사/해시 호출 수를 검증한다. 순수 모델 검증과 실제 DB 검증을 구분하며, 운영 CPU 개선 수치는 별도 실측 전 주장하지 않는다.
