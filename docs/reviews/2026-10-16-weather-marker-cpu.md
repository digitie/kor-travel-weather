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
실제 PostgreSQL generic/custom prepared plan 및 복합키 회귀를 포함한20개 시험을
통과했다. 독립 A·D FULL 리뷰는 SOURCE_PASS/열린 P0·P1·P2 0이며 원문은
아래 evidence에 수정 없이 보관한다.

읽기 전용 운영 DB/같은60위치/고정시각/old→candidate→old→candidate 프로파일에서
4개 응답의 전체 data SHA256이 같고170개 fact·142144byte였다.
워밍 후 CPU3174.66→2558.17ms, wall3678.92→3060.49ms였다.
이는 cProfile 오버헤드와 기존 이미지 안의 격리 후보 함수 실행을 포함하며,
HTTP 서버 교체나 전체 호스트 CPU·UI 성공 수치가 아니다. DB 쓰기·DDL은0이었다.

- [A 원문](evidence/2026-10-16-weather-marker-cpu-a.md):
  SHA256 80c3b4fda94d7077071930ff80aa993fcce97484493e07f424611dd76374d998
- [D 원문](evidence/2026-10-16-weather-marker-cpu-d.md):
  SHA256 b8d026fab57c020150359f26b23dd4634def1087280eca15141117cee3817a4e

최종 보존 정책과 통합한 전체 회귀, 실제 이미지 교체 후 운영 UI25초 기준 및
HTTP/호스트 CPU 검증은 진행 중이다.
