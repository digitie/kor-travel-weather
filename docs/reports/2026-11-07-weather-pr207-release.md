# 2026-11-07 Weather PR207 최신 코드 이미지 배포

최신 main `699f899316005f5139f65c1bc7dbfe52eff5955a`(PR207)을 기존 상세 UI·성능 개선 제품에 통합한 `3a4ae3fa6b6057add487deaeea030cc26a3501ac`를 운영 API·수집기에 반영했다. 최신 main만으로 기존 미머지 제품 기능을 덮어쓰지 않았다.

PR207은 값의 만료와 신선도를 분리한다. 최근24시간 안의 승인만 meta·stale에 반영하며 창 안 승인이 하나도 없는 원천은 stale로 표시한다. 창 밖이지만 만료 전인 값은 유지한다. 현재·예보·중기·일간 reader와 위치별 판정을 함께 반영했다. 기존 PR206 복구 기능은 보존하되 실제 backfill·DB migration·원본 삭제·외부 재수집은 실행하지 않았다.

| 역할 | 실제 CID | 이미지 | 명령 |
| --- | --- | --- | --- |
| kor-travel-weather-dagster-code-server | `d34898c58c33724009134fc58058fc3cc46e4cbad71dea774fd6c5268323c514` | `sha256:88e41d8f06041a9905d68ef94a50c9dd652940d48c2e95d1ed6187fd2daa3f75` | `dagster code-server start -h 127.0.0.1 -p 12206 -m kortravelweather_dagster.definitions --inject-env-vars-from-instance` |
| kor-travel-weather-api | `8fc8c13c4b01b6fd65a100892eaccf6bf38c6870758025644da640da998d5392` | `sha256:e43ad042732f837b46e8d9848b1ad4ad9b02860c4f78b9ca0d491ec56618ed87` | `uvicorn kortravelweather_api.app:app --host 0.0.0.0 --port 12201` |
| kor-travel-weather-web | `b1d95fc7e3b2a593781d06e8fa531e7bdcaf711c9845c7dda854a30a53259e9b` | `sha256:508a4383eea8eff4ac1469d09f23d4c1482d9b5f167b7e4d3479c948e16dfa7a` | `npm run start -- --hostname 0.0.0.0 --port 12205` |

웹은 최신 제품과 source tree가 같아 기존 이미지·CID를 유지했다. PostgreSQL18/schema0037과 실제 포트·사용자 설정·VWorld 키·다른 서비스를 보존했다. 원 27개 일정/센서의 상태와 카탈로그를 복원했다. 기존 실행·대기 수집과 실제 worker는 자연 종료를 기다렸으며 임의 취소·강제 종료를 하지 않았다.

Manager adopt/verify/show healthy, current `/srv/ktdm-releases/weather-adopt-20261107T072214Z-0c80eadf93164561b69d9960f7dcf631`. 배포 최초 Invocation `0755481c8af84f1f93d2d9aab80a4fcb`의 journal done·실제 정상 종료·MainPID0·cgroup 부재·소유 자식 종료를 확인했다.

## 검증 범위

WSL Python3.13·격리 Docker PostgreSQL18에서 inventory·일간·전체 원천·frozen API 관련 시험92개가 통과했고 제품 Python 소스의 ruff도 통과했다. ARM64 API·수집기 이미지에서 복구 모듈 보존·24/25시간 신선도 경계·reader 기본5초·Alembic head0037을 검산했다. 운영 DB에는 시험 자료를 넣지 않았다.

실제 새 API의 버전·health·지역특보·export status·500행 위치 조회200, 조건부304를 확인했다. 재시도0이며 전국/cold 성능 수치로 확대하지 않는다. Firefox PC1440·모바일375의 상세 날씨·내일·시간별 그래프·지도·overflow0·logout 및 소유 브라우저 종료를 확인했다. 원천 미제공 값은 미제공으로 남긴다.

원문은 Git 밖 `C:/Users/digit/.codex/tmp/weather-pr207-release-20261107/final-evidence.json`, SHA256 `f94518a25186c49b4fc5bcfabac111a627f6981a760e114b0a000f9942428fc6`다. 백업·복원은 SKIPPED_BY_USER다.

## 신선도 실제 응답과 원 실패

실제 API에서 24시간 경계 포함·25시간 제외를 검산했다. 현재값·초단기·단기·중기·외부예보·일간 요청은 원문 `live-freshness.json`에 보존했다. 첫 현재값 요청은 statement_timeout으로 HTTP503이었고, 별도 한 번의 후속 GET은 HTTP200/1행/stale였다. 다른 다섯 응답은 HTTP200이며 중기는 ok, 초단기·단기·외부예보·일간은 stale였다. 이를 전체 원천 신선함이나 속도 개선 성공으로 바꾸지 않는다. 수집·backfill·재배포로 성공을 만들지 않았다.

제품 head `3a4ae3fa6b6057add487deaeea030cc26a3501ac`의 자동 CI `42350307400`은 07:29UTC 관측에서 frontend SUCCESS, python 진행 중이다. 배포·집중 시험 성공과 전체 CI 완료를 구분하며 문서 마감 커밋의 미래 CI도 성공으로 바꾸지 않는다.
