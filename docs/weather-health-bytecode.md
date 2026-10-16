# Weather 점검의 표준 라이브러리 bytecode 준비

## 범위와 기준선

이 변경은 PR101 916715d7ca9414e259adb60ea22c73543a891af6 위에 쌓는다.
운영 API100의 마커 SQL, UI101의 AbortSignal 취소, 기존 예약·재시도·native recovery
설정과 DTO는 보존한다. Common37 병합 c48595880aaad4367802ad71af2525a8a5e17c6a를
root/API 전이 의존성과 독립 Dagster 패키지에 동일하게 고정한다.

## 이미지와 점검 계약

API와 Dagster가 공유하는 최종 Python Dockerfile에서 의존성 설치 후 동일 interpreter로
python -I -m kortravelcommon.build_bytecode --stdlib를 한 번 실행한다.
이는 빌드 단계의 명시 작업이며 런타임 점검에서 이 빌드 helper를 호출하지 않는다.
-I는 PYTHONDONTWRITEBYTECODE 환경변수를 무시하므로 cache가 없거나 무효이면 Python의
암묵적 컴파일·쓰기 시도가 사라진다고 보장하지 않는다. 최종 cache와 runtime 권한을 별도로 검증한다.
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


| 저장소 production 서비스 | 기존 interval | 변경 interval |
| --- | --- | --- |
| db | 5s | 15s |
| api | 10s | 30s |
| dagster-code-server | 30s | 90s |
| dagster-webserver | 15s | 45s |
| dagster-daemon | 30s | 90s |
| prometheus | 10s | 30s |

시험용 CI PostgreSQL health와 Prometheus scrape/evaluation은 이 표의 변경 대상이 아니다.
web/UI에는 이 저장소의 독립 Docker healthcheck가 없어 새 점검을 추가하지 않았다.
Manager가 실제 UI/공유 daemon/webserver에 선언하는 override는 해당 소유 저장소에서 정산한다.

## interval과 복구 시간 경계

Common native recovery는 Docker health 표본의 나이를 소비하지 않는다. private PID namespace의
canonical PID1 leader/boot/cgroup·실제 NTP clock·최소60초 grace·첫 anchor와 공식 run 상태가
독립 기준이며 그대로 유지한다. Dagster1.13.24의 run monitoring도 launcher의 직접 worker
health 응답을 사용한다. daemon heartbeat 생성 주기와 liveness CLI의 허용창은 Docker 점검의
실행 간격과 별개다. 따라서 interval을 늘린다고 sensor grace나 heartbeat 허용창을 늘리지 않는다.

unhealthy 감지와 service_healthy 시작 의존성 확인은 늦어질 수 있다. HTTP 내부3초는 Python
시작 시간을 포함하는 Docker5초 전체 상한 보장이 아니다. 운영 부하에서 cold 점검이 여전히
timeout이면 실패로 기록하고, 간격 증가나 warm 성공을 그 실패의 해소로 해석하지 않는다.
실제 Manager의 기존 reaper/guard 정책은 이 변경으로 제거·배포되지 않는다.

## 소스 검증 결과

제품 커밋 074f5694fe4706739ae14f463ea78278fcb36859에서 전용 loan PostgreSQL의 새
UUID DB만 사용했다. migration PASS, 집중16 PASS, 전체557 PASS/49warnings/622.29초,
Ruff PASS이다. 시험 DB는 보존했고 기본 weather_test/공유15432·15433 및 다른 DB에
접속·정리하지 않았다. frontend196 PASS/type-check/lint PASS, 제품 커밋 GitHub CI2 SUCCESS이다.
기존101의 실제 Dockerfile/Compose에 새 시험 함수2개를 적용하면 RED이며 현 구현은 GREEN이다.

lock105개 package의 version과 provider pin은 동일하다. 전체 Compose 모델은 API test와
위6개 interval 외 동일하다. 수집/core repository/API/Dagster 정의/UI/DTO/instance/workspace
파일은 기준선101과 byte동일하다. 로컬 Python3.12.13/Node22.22.2 검증이며 실제 ARM 이미지
빌드·배포·CPU 변화·최종 runtime cold 점검·UI E2E는 미실시다. 최종 문서 commit의 CI는
별도로 확인해야 하며 과거 제품 커밋의 결과로 대신하지 않는다.
