# 2026-11-07 Weather PR206 최신 코드 이미지 배포

최신 main `e91a490bfc3ee7832c69a125e5abf17ab879fb3a`(PR206)을 기존 상세 UI·성능 개선 제품에 통합한 `87a0b50577d145cfa8dc12b62bdafc0e3a5be6c9`를 운영 API·수집기에 반영했다. 최신 main만으로 기존 미머지 제품 기능을 덮어쓰지 않았다.

추가 제품 코드는 `export_daily_backfill.py` 한 파일이다. KMA 성공 수집 원천으로 지난 날짜 요약을 복구하는 기능을 포함하지만 이번 요청은 이미지 배포이므로 실제 backfill은 실행하지 않았다. DB migration·원본 삭제·외부 재수집도 실행하지 않았다.

| 역할 | 실제 CID | 이미지 | 명령 |
| --- | --- | --- | --- |
| kor-travel-weather-dagster-code-server | `3b22ec6cd5a23c46ea275014583f726e8fd13a980e301c45e1b48e113014aaea` | `sha256:c5b3ccdf8f711273c11391aac5844263153ccbb1fabe59ce9e3bd951cf89130f` | `dagster code-server start -h 127.0.0.1 -p 12206 -m kortravelweather_dagster.definitions --inject-env-vars-from-instance` |
| kor-travel-weather-api | `5eda2322d71c07d39b46221936d355c06907c63b76ac6be0fda3ae43573d70d5` | `sha256:69868fc876fd1f51d8483c73be5532c4e01729940748c1c94bd66ed23f33e5d6` | `uvicorn kortravelweather_api.app:app --host 0.0.0.0 --port 12201` |
| kor-travel-weather-web | `b1d95fc7e3b2a593781d06e8fa531e7bdcaf711c9845c7dda854a30a53259e9b` | `sha256:508a4383eea8eff4ac1469d09f23d4c1482d9b5f167b7e4d3479c948e16dfa7a` | `npm run start -- --hostname 0.0.0.0 --port 12205` |

웹은 최신 제품과 source tree가 같아 기존 이미지·CID를 유지했다. PostgreSQL18/schema0037과 실제 포트·사용자 설정·VWorld 키·다른 서비스를 보존했다. 원 27개 일정/센서의 상태와 카탈로그를 복원했다. 기존 실행·대기 수집과 실제 worker는 자연 종료를 기다렸으며 임의 취소·강제 종료를 하지 않았다.

Manager adopt/verify/show healthy, current `/srv/ktdm-releases/weather-adopt-20261107T055410Z-c42f7d6f37be45219d9cbbcca0ad8952`. 배포 최초 Invocation `287390f5b087471cb743ab232d2a88fd`의 journal done·실제 정상 종료·MainPID0·cgroup 부재·소유 자식 종료를 확인했다.

## 검증 범위

WSL Python3.13·격리 Docker PostgreSQL18에서 일간 요약 관련 시험24개가 통과했고 변경 코드와 시험의 ruff도 통과했다. ARM64 API·수집기 이미지에서 복구 모듈 import·reader 기본5초·Alembic head0037을 검산했다. 운영 DB에는 시험 자료를 넣지 않았다.

실제 새 API의 버전·health·지역특보·export status·500행 위치 조회200, 조건부304를 확인했다. 재시도0이며 전국/cold 성능 수치로 확대하지 않는다. Firefox PC1440·모바일375의 상세 날씨·내일·시간별 그래프·지도·overflow0·logout 및 소유 브라우저 종료를 확인했다. 원천 미제공 값은 미제공으로 남긴다.

원문은 Git 밖 `C:/Users/digit/.codex/tmp/weather-latest-release-20261107/final-evidence.json`, SHA256 `2527ab805417280714766087ac476e0251bdca28ef4d5d566e0537f1b473f5d2`다. 백업·복원은 SKIPPED_BY_USER다.

GitHub CI `42341235591`은 제품 head `87a0b50577d145cfa8dc12b62bdafc0e3a5be6c9`의 python·frontend 모두 SUCCESS다. 문서 마감 커밋의 미래 CI까지 성공으로 바꾸지 않는다.
