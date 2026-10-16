# Weather102 독립 리뷰 manifest

## 동일 검토 요청

대상은 PR101 916715d7ca9414e259adb60ea22c73543a891af6 기준의 health/pin/tests/docs 변경이다.
제품 커밋 074f5694fe4706739ae14f463ea78278fcb36859 후 문서만 보강했다.
Common37 병합 c48595880aaad4367802ad71af2525a8a5e17c6a를 사용한다.
두 리뷰어는 상대 원문을 읽기 전에 동일 source 전체와 아래 경계를 독립 검토한다.

- 공용 API/CS 최종 interpreter의 빌드 전용 stdlib checked-hash 준비·실패 전파·runtime cache 경계
- API loopback /health 헤더2xx/quiet/proxy·redirect·timeout·경량 import
- Docker interval만3배/timeout·retries·start_period·업무 cron·grace·guard/기존 복구 보존
- 기준선 API100·UI101 및 기존 provider/lock version·instance/workspace 전체 보존
- 테스트가 이전 source를 실제 거절하는지와 운영 성능 미검증 한계

실제 운영/SSH·이미지 빌드/로드·외부 provider·기본/공유 DB 호출 없이 검토한다.
필요한 로컬 시험의 범위와 실행/미실행을 구분하고 findings/라인/실행ID·원문SHA를 남긴다.

## 변경 파일 SHA256

| 경로 | SHA256 |
| --- | --- |
| compose.yaml | f1319e59803b7262db7e3bbe777ce12692f8886393615e86ba071e9ad54df8b2 |
| deploy/Dockerfile.python | 3789d7e0bb1af5c7b616e80775e2aed6209e7529e6ff2c1e7c2c7b755d6c9ae3 |
| docs/runbooks/dagster-recovery.md | a3938e52677ae50e5de8532c8526159033fbe84fb7556689b792b42c4d1c12a5 |
| docs/weather-health-bytecode.md | bc2113eb8fc027374f3577b9b8a752c4b45573f46ab23a5e2599b25e87ac2343 |
| docs/weather-recovery-executor-adoption.md | 068c771f3b645151601cbdae1a9870c643f22075502db6fa1dbb8e1c28082e5c |
| packages/kor-travel-weather-dagster/pyproject.toml | 5d8e778adc8a06777ad439fd0e5fd25ab35ef202f5f559fc7b19cc7fbb896627 |
| pyproject.toml | 17050cfe54dacdb4108121075bec5293de7859fe856cf67ac6f4fbf6d3ae094f |
| tests/test_common_health_adoption.py | 8a9eb80e00cc4b5d2ab970a6404fbb053e093b5da07b0b3a3ed7ec54627078c9 |
| tests/test_image_health_bytecode.py | 4dfb8d73ed68ee5ce2e94ee7865a4f6f6a1ff43bf3fe02f93ab7daec23f53ac9 |
| uv.lock | 107dd80e2eea6687c1e1173c37d37b79a0b1b09a0d4248ecb0fee8bcbf2e5298 |

## 검증 계보

소유UUID 전용 PG 전체557 PASS와 집중16 PASS/Ruff, frontend196/type/lint PASS는 제품074f에서 실행했다.
P/weather102-health-python-validation-a-r1-20261016.safe.json와 stage별 private log에 보존했다.
독립 리뷰 결과는 작성자의 검사나 CI로 대체하지 않는다. 새 이미지/운영 효과는 후속이다.
