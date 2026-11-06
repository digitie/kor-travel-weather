# Weather PR205 최신 코드 이미지 배포

## 반영 범위

최신 main `76f280699b8f5fed39e5ca12412363a0bb6d80f5`(PR205)을 기존 배포 브랜치와 합친
제품 `ba66543d53ac86ad69d17dc124a857f308a912c2`다. API reader guard의 기본 대기를 2초에서
5초로 늘리고 0.5~10초 설정 범위를 적용한다. 배타 guard의 보유 시간을 holder별로 계측하며,
파일 descriptor를 모두 닫은 뒤 계측하고 계측 오류를 본 처리로 전파하지 않는다.

기존 상세 날씨 UI·export 개선·DB0037을 보존한다. 웹 tree·uv.lock·pyproject·Alembic tree는
기존 제품9acc409와 정확히 같다. 웹 이미지를 다시 만들거나 DB migration을 실행하지 않는다.
API·수집기는 현행 이미지와 같은 의존성에 최신 소스를 단일 COPY 레이어로 반영했다.

## 실제 배포 완료

API·수집기 제품 `ba66543d53ac86ad69d17dc124a857f308a912c2` 반영을 완료했다. 원 27개 일정/센서 상태와 카탈로그를 복원했다. 실제 API·CS의 reader guard 설정은 각각5초이고 VWorld 입력을 포함한 사용자 설정과 다른 서비스는 보존했다.

| 역할 | 실제 CID | 이미지 | 실제 명령 |
| --- | --- | --- | --- |
| kor-travel-weather-dagster-code-server | `02c9eabad4d58d9a21a9c7bf57e9abeb78974c1399000d72059b39900a711110` | `sha256:7cabfec22faaf612a4ac65d8da2ec391860d0427f4a258a1d2f5fe65a16e4bbc` | `dagster code-server start -h 127.0.0.1 -p 12206 -m kortravelweather_dagster.definitions --inject-env-vars-from-instance` |
| kor-travel-weather-api | `07c4e7a4690d263161947327597f55902c8810816803262ad3cdcacdb97c0e94` | `sha256:4e59e13e9e4d4c575d92a099300d0289bee37a443b2ed9407d5179ad7a4d2ad8` | `uvicorn kortravelweather_api.app:app --host 0.0.0.0 --port 12201` |
| kor-travel-weather-web | `b1d95fc7e3b2a593781d06e8fa531e7bdcaf711c9845c7dda854a30a53259e9b` | `sha256:508a4383eea8eff4ac1469d09f23d4c1482d9b5f167b7e4d3479c948e16dfa7a` | `npm run start -- --hostname 0.0.0.0 --port 12205` |

웹 CID·이미지는 최초 입력과 같고 소스 tree도 제품ba66543과 같다. 웹 빌드/교체·DB migration은 실행하지 않았으며 schema0037과 PostgreSQL18을 유지했다. Manager current는 `/srv/ktdm-releases/weather-adopt-20261106T051410Z-28cf0462c4124a658f6858f353c1246d`이고 adopt/verify/show healthy다.

배포 최초 Invocation `6815146ca14b45b39f189173f7794fd1`의 journal done·실제 정상 종료·MainPID0·cgroup 부재·소유 자식 종료를 확인했다. 빌드 최초 Invocation `ac8fba1b19184f00913fb681422e28e1`도 journal done이다. 기존 수집의 자연 종료를 기다렸으며 취소·강제 종료·중복 제출은 하지 않았다.

## 검증 범위

격리 PostgreSQL18의 frozen export·reader lifetime67개와 ruff가 통과했다. 최종 실제 health/version/지역특보/status/500행 위치는 모두200, 조건부 위치 요청은 HTTP304다. 실제 version의 git_commit도 ba66543과 같다. 최초 API200/304는 `live-api.json`, 최종 원문은 `live-api-final.json`에 별도로 남긴다. 재시도나 전국/cold 성능 배율로 과장하지 않는다.

Firefox PC1440·모바일375의 현재/내일 날씨·24시간 그래프·지도와 overflow0·불필요한 빈 특보 설명0·logout200·소유 브라우저 자식 종료·잔여0을 확인했다. 소스에서 제공하지 않는 미세먼지·체감·일출 등의 값은 미제공으로 남기며 데이터 충족으로 바꾸지 않는다. 실제 화면과 DTO/DOM은 `live-ui/results.json` 및 같은 폴더의 PNG에 보존했다.

백업·복원은 SKIPPED_BY_USER다. 정본은 Git 밖 `C:/Users/digit/.codex/tmp/weather-latest-release-20261106/final-evidence.json`, SHA256 `86466119407fe52a939374c9ec31ec339bf45a68a2dcbeec1382838d50e30b18`다.

GitHub CI `42150632133`은 head `b537867f9dd3e82f2b0e084a68082c1d85792565`의 python·frontend 모두 SUCCESS다. 제품 소스는 ba66543이고 이후 변경은 배포 기록 문서다. 마지막 문서 커밋의 CI 결과와 혼동하지 않는다.
