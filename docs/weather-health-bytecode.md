# Weather 점검의 표준 라이브러리 bytecode 준비

## 범위와 기준선

이 변경은 PR101 916715d7ca9414e259adb60ea22c73543a891af6 위에 쌓는다.
운영 API100의 마커 SQL, UI101의 AbortSignal 취소, 기존 예약·재시도·native recovery
설정과 DTO는 보존한다. Common37 병합 c48595880aaad4367802ad71af2525a8a5e17c6a를
root/API 전이 의존성과 독립 Dagster 패키지에 동일하게 고정한다.

## 구현 계획

API와 Dagster가 공유하는 최종 Python Dockerfile에서 의존성 설치 후 동일 interpreter로
python -I -m kortravelcommon.build_bytecode --stdlib를 한 번 실행한다.
이는 빌드 단계의 명시 작업이며 런타임 점검에서 cache를 생성하지 않는다.
checked-hash/optimize0 cache이고, 다른 Python 버전·magic이나 -O 실행에 대한 보장이 아니다.
신뢰한 단일 writer 빌드에서 사용하며 컴파일 실패는 빌드 실패로 유지한다.

API의 Compose 점검만 공용 stdlib HTTP CLI로 바꾼다. loopback의 /health 완전한
2xx 응답 헤더를 확인하며 redirect·재시도·본문/DB 준비 검증은 하지 않는다.
공용 Dockerfile에 API HEALTHCHECK를 넣어 CS 기본 점검을 혼합하지 않는다.
코드서버의 기존 공용 gRPC liveness, daemon monitoring과 복구 소유권은 유지한다.

사용자 요청에 따라 저장소의 Docker healthcheck interval만 각각 3배로 조정한다.
timeout/retries/start_period, 업무 cron, sensor/guard clock, Prometheus scrape 주기는 유지한다.
실제 운영 API는 기존 Manager override 30s이므로 운영 반영 목표는 90s이다.
저장소 API 기본값 10s→30s만으로 운영 override까지 반영됐다고 주장하지 않는다.

## 검증과 운영 gate

고정 Common 설치·quiet CLI·실제 loopback 응답/오류와 경량 import, Dockerfile 명령 실패 전파,
Compose 원모델의 의도한 점검 필드 외 동일성을 검증한다.
필수 Ruff·전체 pytest·frontend type/test 결과는 최종 인계서에 구분한다.
Docker 이미지 빌드·ARM cache 확인·운영 cold 점검·정의 준비·UI·CPU 비교는 후속 gate이다.
소스 검증을 운영 적용 또는 인과적 CPU 개선으로 세지 않는다.
