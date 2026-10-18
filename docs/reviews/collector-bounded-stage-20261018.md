# KMA 비특보 예보 staging 메모리 개선

다음 위치를 추가하면 staging 상한을 넘는 경우 기존 묶음을 먼저 게시한다.
KMA 비특보 예보(격자·중기 지역)의 staging·게시 상한 후보는 각각2500이다. 특보 staging은5000,
다른 provider가 사용하는 공용 PUBLISH_CHUNK_VALUES도5000으로 유지한다.
한 위치 자체가 상한보다 큰 경우에는 해당 위치를 나누지 않는다.
source lineage·누적 예산·lease 검사와 재시도 경계를 유지한다.

## Odroid 격리 실측

운영 code-server 이미지에 후보 모듈만 읽기 전용으로 적용하고, 실제
run_weather_sync→변환→fanout→PostgreSQL 저장 경로를 실행했다.
합성 응답792행·같은 격자10위치로7920fact를 저장한다. 외부 provider HTTP,
운영 DB, 전체 Dagster job과 호스트 전체 CPU는 측정 범위가 아니다.
daemon은 중지 상태이며 기존 서비스의 구성·실행 상태를 보존했다.

추적 도구를 끈 기준5000/후보2500 각3회 교대 실행 중앙값:

| 항목 | 기준 | 후보 | 변화 |
| --- | ---: | ---: | ---: |
| worker CPU | 17.3326초 | 17.0981초 | -1.35% |
| 처리 시간 | 56.2292초 | 56.3378초 | +0.19% |
| worker RSS high-water | 130.695MB | 111.075MB | -15.01% |
| worker cgroup peak | 111.952MB | 92.426MB | -17.44% |
| 초기 수집 SQL 수 | 107 | 125 | +16.82% |

MB는십진 단위다. 메모리 peak는 측정 종료까지의 준비·import 비용을 포함하며,
CPU·처리 시간은 초기 수집 구간만 계산한다. 이후 digest·재수집은 성능에서 제외한다.
모든 표본의 원천·fact·current 의미 digest, 재수집0건, 정리가 통과했다.
후보 게시 크기는2376/2376/2376/792이며 기준은4752/792/2376이다.

이 실측은6421a4b 소스의 프로세스 상수 두 개를2500으로 주입한 결과다.
비특보 전용 상수로 분리한 실제 제품 a8949778의 복구·배포 검증은 아래에 별도로 기록한다.
tracemalloc을 켠 예비 측정에서는 CPU가 증가했으므로 위 표본과 섞지 않았다.

### PostgreSQL CPU를 포함한 추가 3쌍

같은 조건의 새 3쌍에서 소유 PostgreSQL cgroup의 CPU counter를
초기 수집 시작·종료 시점에 함께 읽었다. 초기화·후속 digest·재수집을 제외하고
해당 PostgreSQL의 background 활동을 포함한다. 두 counter는 같은 sample에서
순차적으로 읽으므로 미세한 시간차가 있다.

| 항목 | 기준 중앙값 | 후보 중앙값 | 변화 |
| --- | ---: | ---: | ---: |
| worker + PostgreSQL CPU | 40.8676초 | 40.3386초 | -1.29% |
| worker CPU | 17.4110초 | 17.3831초 | -0.16% |
| PostgreSQL CPU | 23.1113초 | 23.2015초 | +0.39% |
| 처리 시간 | 55.9580초 | 57.0896초 | +2.02% |
| worker RSS high-water | 130.699MB | 111.104MB | -14.99% |
| worker cgroup peak | 111.968MB | 92.434MB | -17.45% |

합산 CPU는 각 표본의 worker + PostgreSQL 합계에서 중앙값을 구한다.
개별 중앙값 두 개를 더한 값과 같을 필요는 없다. 3쌍의 작은 표본에서 CPU는
동등 수준으로 판단하며, 유의미한 CPU 절감을 주장하지 않는다.
메모리는 두 측정군 모두 약15% 감소했다. SQL 증가는 이 표본에서 CPU 증가로
이어지지 않았지만 다른 provider·중기 응답·운영 전체 job에서도 같다고 일반화하지 않는다.
호스트 전체 CPU 또는 전체 수집 job의15% 메모리 절감도 주장하지 않는다.

[원시 표본·중앙값·semantic digest](collector-bounded-stage-odroid-metrics-20261018.json)를
함께 보관한다. 모든 표본의 의미 digest·재수집·소유 자원 정리가 통과했다.

## 실패·복구 경계

게시 경계가 바뀌므로 실패 전 실제 commit 수는 달라질 수 있다.
예산7·위치당2fact·staging3 시험은 기존4개 대신2개가 commit된 뒤 실패하며,
run count는 실제 commit된2개를 유지한다. 나머지는 다음 정상 재수집으로 복구한다.
전체 작업의 원자성을 주장하지 않는다.

실제 a8949778 소스와 기본값(비특보2500·특보5000)에서 아래 두 상황을 검증했다.
기준 이미지의 후보 모듈만 읽기 전용으로 적용하고 나머지 소스 해시도 확인했으며,
측정용 상수 주입은 사용하지 않았다.

| 실패 주입 | 실패 전 실제 저장 | 복구에서 추가 저장 | 최종 저장 |
| --- | ---: | ---: | ---: |
| 두 번째 실제 DB 쓰기 후 commit 전 오류 + 원천 alias | 2376 | 5544 | 7920 |
| 두 번째 commit 완료 후 응답 오류 | 4752 | 3168 | 7920 |

두 경우 모두 기존 행·실패 run 보존, canonical 원천1개, 원천 연결과 current projection,
정상 기준3개 테이블의 semantic digest 일치 및 다음 재수집0건을 확인했다.
시험 컨테이너·볼륨은 모두 정리했고 기존 서비스86개의 실행 상태는 유지됐다.
[복구 결과와 정상 기준 비교](collector-bounded-stage-recovery-20261018.json)를 함께 보관한다.
실제 프로세스 kill·PG 네트워크 단절·reaper 경쟁을 시험한 결과는 아니다.

## 검증 상태

6421a4b 후보는 새 격리 PostgreSQL 관련43개·전체665개, frontend214개,
type-check·Ruff·CI와 독립 소스 리뷰2건이 통과했다.
초기 기본 로컬 시험 DB 실행은 partition schema 불일치로1실패/34통과였고,
새 소유 PostgreSQL로 재검증했다. 실패 결과를 전체 통과로 간주하지 않는다.
grid 전용2500 후보는 실제 기본값에서1200fact·2501fact 응답의 위치 원자성과
lineage 검증을 추가했으며, 격리 PostgreSQL 전체667개가 통과했다.
새 테스트의 줄 길이2곳을 정리한 뒤 Ruff 재검증도 통과했다.
최종 제품 a8949778의 독립 소스 리뷰2건 및 Python/frontend CI가 통과했다.
실제 기본값 복구2건도 통과했다.

## 운영 배포·live UI 최종 수용

실제 a8949778의 ARM64 API/CS native 이미지를 빌드하고 각 설치 소스67파일을 검증했다.
API 이미지는 sha256:b056595fd07afa5fd007a0ef4003dc6e4d34f5d71f4ae52d7f94b0e7aeb5ebf2,
CS 이미지는 sha256:119b3869e1b10f93d708087127c938b043b2634c3652960e82eefe4941e6f9d5다.
빌드 당시 다른 서비스와 daemon OFF를 보존했다.

운영 전환 f5cc3880-b833-46d7-b7a5-77ef0632e952는 API/CS READY로 통과했다.
기존 컨테이너를 보존하고 큐·env 파일을 유지했으며 runtime 환경은 버전 키만 변경했다.
이후 WSL Chromium1243의 공개 UI36개가 모두 PASS/exit0이며 실패·미실행·미검증 케이스는0이다.
로그인·메뉴·지도 마커·Dagster 관측/링크·오류 복구·5개 화면 폭·로그아웃 등을 확인했고,
검증 중 Weather API/CS/Web 실행 상태와 daemon OFF를 유지했다.

[빌드·배포·36개 UI 결과의 공개 가능한 기록](collector-bounded-stage-deployment-20261018.json)을 보관한다.
원 UI 결과 SHA256은 d530089254ea8c7b5d0c9408af2359b15c34a66353be4b6e8e9d0f315da220dc다.
이 UI 수용은 실제 provider 전체 수집·24시간 안정성·자동 daemon
복구·Kakao 실제 fallback·전체 주소 sweep·Track1/2의 남은 운영 수용을 완료한 뜻이 아니다.

최종 배포·UI·문서의 독립 검토는 collector_transport_cpu와 concierge_closure_review_b가 각각 수행했으며, 미해소 P0/P1/P2는 모두0건이다. 제품 소스 리뷰2건과 구분된 최종 수용 검토다.
