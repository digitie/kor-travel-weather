# 최신 Weather 통합 이미지 배포

## 반영 범위

2026-11-05 사용자의 최신 코드 이미지 배포 요청에 따라, 요청 시점의 `main`
`240f3427770f4932d0d2edd144cb93888328afcf`를 기존 배포 기능이 있는 브랜치와 합쳤다.
제품 소스는 `9acc40906d421ab58712838605649af10496cc2b`다.

- PR200: 오래된 위치별 승인 만료와 export meta의 실제 유효 범위 반영.
- PR201: 행정구역 근거가 있는 공식 특보 발표 이벤트 API·수집·화면 표시.
- PR202: export 선택 계획 재사용, 배열 매개변수와 소유자 조회 비용 개선.
- PR203: 승격의 배타 guard를 최종 CAS로 축소하고 파일 재검사 유지.
- 기존 PR193 상세 날씨 UI와 PR196·198·199 성능·응답·snapshot projection 변경 유지.

`0036_snapshot_kind_projection`와 `0036_regional_alert_events`는 독립 이력으로 보존한다.
추가한 `0037_release_merge`는 두 부모를 연결하는 무변경 병합 revision이다.
기존 운영 DB에서 특보 이벤트 테이블만 추가하며 downgrade·stamp·행 삭제는 하지 않는다.
이는 두 기능의 병합이며 서로 다른 revision의 이름을 덮어쓰는 방식이 아니다.

## 검증과 원 실패

WSL의 격리 PostgreSQL18에서 최신 head migration·특보 저장/정규화·export head/inventory
85개와 실제 특보 HTTP 계약16개를 통과했다. 시험 DB와 운영 DB는 분리했다.
병합 중 재발한 0035 전용 시험의 revision 기대값은 실제 upgrade target인 0035로 맞췄다.
이 시험 자료 변경으로 제품 이미지를 다시 만들지 않는다.

첫 API 이미지는 실제 Docker 빌드0이다. 후속 격리 검사에 앱의 PYTHONPATH가 빠져 최초
빌드 부모는1로 종료했다. 다음 격리 검사는 API package의 자동 앱 생성이 개발 DB에
접속해 실패했다. 원 부모1·종료·cgroup 부재를 보존하고 이미지 생성 성공과 구분한다.
검사는 DB 요청 없는 core import·마이그레이션 head·리소스 존재 확인으로 정정했다.
실제 앱 기동과 인증·DB는 교체 뒤 실제 HTTP로 확인한다.
동일 API 이미지의 재빌드는 하지 않았다.

비공개 VWorld 입력은 BuildKit secret으로 전달하고 실제 현재 입력과 일치 여부만 확인한다.
키 값은 문서·Git·출력에 기록하지 않는다. 사용자 지시에 따라 백업·복원은 생략한다.

## 실제 배포 완료

제품 `9acc40906d421ab58712838605649af10496cc2b`의 세 역할을 실제 반영했다. 최신 main240f342와 기존 배포 기능을 포함하며 문서·시험 후속 커밋으로 제품 이미지를 재빌드하지 않았다.

| 역할 | 실제 CID | 이미지 | 실제 명령 |
| --- | --- | --- | --- |
| kor-travel-weather-dagster-code-server | `6b0cfb6bea723313bb65035b5aeae2ccc429acfa3c714b6bcef013d2efb41cdc` | `sha256:1731ff3c73762290b67893aec65c2d5ac7f3fabdb3002649a384927a2a6d8bd6` | `dagster code-server start -h 127.0.0.1 -p 12206 -m kortravelweather_dagster.definitions --inject-env-vars-from-instance` |
| kor-travel-weather-api | `094b8338dc6b1d671cda352e1ee398099365c403fd166d2dac02c56b2b0082ae` | `sha256:99bdacb984e54dba2536a4be57f090b1df71133540f2be9fb86067285e9f6435` | `uvicorn kortravelweather_api.app:app --host 0.0.0.0 --port 12201` |
| kor-travel-weather-web | `b1d95fc7e3b2a593781d06e8fa531e7bdcaf711c9845c7dda854a30a53259e9b` | `sha256:508a4383eea8eff4ac1469d09f23d4c1482d9b5f167b7e4d3479c948e16dfa7a` | `npm run start -- --hostname 0.0.0.0 --port 12205` |

같은 PostgreSQL18의 schema는 `0037_release_merge`다. 원 26개 Weather 일정/센서의 상태를 복원했고 새 `regional_alert_events_every_five_minutes`는 후보의 기본 RUNNING으로 명시 정산했다. 다른 서비스의 실행 신원과 보호 환경 파일은 같다.

Manager adopt/verify/show는 healthy이며 current는 `/srv/ktdm-releases/weather-adopt-20261105T133024Z-5364cd182e564184b6eac623a38f21f6`다. 배포 최초 Invocation `2219cd940ec54c85b7b9db1e9c5ffb4a`의 journal done·실제 정상 종료, MainPID0·cgroup 부재·소유 자식 종료를 확인했다. 빌드 최초 Invocation `7b807a25c9dd4a01b65853f12231934f` 역시 실제 done이다.

## 실제 HTTP·화면 검증

최종 health·version·지역 특보 API·export status·500행 위치 응답은 모두200이다. version의 실제 git_commit도 제품9acc409와 같다. 최종 조건부 위치 요청은 HTTP304이며 재시도0이다. 최초 관측의 `EXPORT_TEMPORARILY_UNAVAILABLE`503/Retry-After5는 `live-api.json`에 별도 보존한다. 이 표본으로 전국/첫 요청 성능 배율을 주장하지 않는다.

Firefox157 PC1440·모바일375에서 현재 날씨·내일 예보·24시간 그래프·지도 배경을 확인했다. 두 화면 overflow0·불필요한 빈 특보 설명0·logout200·소유 브라우저 자식 종료·잔여0이다. 화면 원문은 `live-ui/results.json`, `overview-ready-1440.png`, `overview-ready-375.png`, `hourly-1440.png`, `hourly-375.png`다. 원천에 없는 미세먼지·체감·일출 등은 미제공으로 남으며 데이터 충족 성공으로 바꾸지 않는다. 새 특보 API200은 제공기관의 실제 특보 수집 성공과도 구분한다.

검증은 격리 PG18의 서로 다른85+16+1개, 새 실행 원인 표시 Vitest3개와 ruff, 실제 ARM64 웹 컴파일·타입 검사·정적 페이지15개 생성이다. Vitest는 버전이 같은 시험 도구만 재사용했고 런타임 의존성은 실제 이미지 빌드의 고정 lock 기준이다. 백업·복원은 사용자 지시로 생략했다.

정본 원문은 Git 밖 `C:/Users/digit/.codex/tmp/weather-latest-release-20261105/final-evidence.json`, SHA256 `57e29b3dc4b8f14a5e8940ddfd8092cf37d4e6342c4172a48e2c102d88f31450`다. 원 실패·자연 종료 대기와 실제 배포 완료를 구분해 보존한다.

CI42020322703의 head `d2aa054c681e12ed8b0df77a54cd603f322b749b`에서 Python·frontend가 모두 SUCCESS다. 원 실패·진행 중 CI를 성공으로 바꾼 것이 아니며 GitHub의 완료 결과를 한 번 정산했다.
