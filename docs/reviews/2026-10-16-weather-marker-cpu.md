# 지도 marker의 요청별 CPU 비용

운영 baseline에서 60개 위치 요청의 DB 실행은 약 0.5초였지만 Python 프로파일
전체는 3.28~3.94초였다. 프로파일 자체의 오버헤드가 있으므로 HTTP 지연과
동일한 값으로 취급하지 않는다. PostgreSQL EXPLAIN의 JIT는 사용되지 않았다.

위치/metric VALUES.data를 typed array bind의 unnest로 바꿔 요청 내용과 개수가
달라도 SQLAlchemy의 제한된 엔진 statement cache를 재사용한다. 부분 인덱스의
고정 literal predicate와 복합 fact key (value_id, known_at)는 유지한다.
fallback은 두 배열의 같은 인덱스를 하나의 위치/metric pair로 취급한다.
API의 동일 marker 묶음 특보 판정은 위치당 한 번만 수행한다. 별도 특보 묶음을
제공하는 repository 호환 경로는 유지한다. DTO/보존 정책/수집 주기는 변경하지 않는다.

기존 소스에서 새 회귀 3실패/1통과를 확인했고 수정 후 순수 회귀 16통과이다.
실제 PostgreSQL generic/custom plan, 실측 전후 응답 동등성, 전체 회귀,
2인 독립 리뷰와 운영 UI 25초 기준은 아직 검증 중이다.
