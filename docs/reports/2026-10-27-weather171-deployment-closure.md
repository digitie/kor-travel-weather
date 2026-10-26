# Weather171 배포·기존 metrics·공개 UI 수용

문서 정산일은2026-10-27이며 아래 실제 배포/관측/복원은2026-10-26 UTC 기준이다.
이 후속은 Markdown만 바꾸며 새 배포·SSH/HTTP·서비스·PG·수집을 실행하지 않는다.
원본 작업 트리의 미커밋 metrics 파일4개와 이전 실패 원장은 보존한다.

## source·CI와 공식 배포

PR [#171](https://github.com/digitie/kor-travel-weather/pull/171)의 최종 제품
`74482c80ef9c14bab4231a5776f842569918791b`는 정확 lock CI Python1292PASS/57warnings,
frontend253PASS/type-check/lint/build 및 두 독립 source/합성 검토를 통과했다.
CI606.52초는 해당 Python job 값이며 운영 수집 시간이나 아래 scrape 시간과 다르다.
병합 main `95020bd525ba930bcdd0e9a61036ad53f60cec11`은 후보와 전체 tree가 같다.
구 로컬 venv/PG autouse 실패와 frontend log 회수 오류는 환경/감사 이력으로 남기며
새 제품 실패나 CI 재실행 성공으로 바꾸지 않는다.

공식 배포는 migration0033을 적용하고 API·Dagster code-server·웹 세 이미지를95020으로
교체했다. 최종 readback에서 세 runtime source와 restart_count0을 확인했다.
API와 code-server는 running/healthy, 웹은 healthcheck 없이 running이다. 웹의 health가
비어 있는 것을 healthy 판정으로 세지 않는다.

| 정본 rollout 단계 | 저장 결과 | 원 결과 파일 SHA256 |
| --- | --- | --- |
| migration0033 | migrated | `9d5e440dd35282659e5afdbb284fb56d9f5a25b3a40161cff9e4a0c6de8f3c29` |
| API 교체 | api-healthy | `6de1338c4caddf5b0d619ba52da5b84e958318f8a7c791acd7513bd29c3c9942` |
| CS/웹 교체 | images-healthy | `ae25446e37113bfc0b15617feaa66e1ba0ae83806bcc1d0fa57516974993f57f` |

최종 합본 `weather171-postdeploy95020-runtime-http-restore-root-r1.json` SHA256은
`1c9f85a1ea42b578cbbd2251c489148ceb177bcd152c72a34df349144da4063e`다.
합본은 세 rollout 파일의 상태·해시를 결박한다. 별도 안전 필드 복사본
`weather171-deploy-three-official-results-root-r1.json` SHA256은
`0ef80a14290c1234d005bc23aafdaed752643d8f928218a655a33b4f24e32cc1`이다.
이 원문에서 schema `0033_export_source_inventory`, 세 단계의95020 revision과 타 컨테이너17개
불변, state backup7811 entries 및 최종 복원을 대조했다. migration은06:34:09Z, API는06:37:02Z,
나머지 교체는06:39:14Z에 완료됐고 마지막 단계까지 instigator는 pause 상태였다.
이 문서 작업에서 운영 DB를 다시 조회하거나 상태를 변경하지 않았다.

## 기존 listener HTTP와 감사도구 실패의 구분

새 source의 기존 CS metrics listener를 단일 GET으로 확인했다. 결과는 HTTP200,
text/plain Prometheus0.0.4·2241B·server_up1·HTTP 왕복0.066120초다. raw body/env/token은
문서에 복사하지 않는다. 새 monitor는 시작하지 않았다.

앞선 감사도구 결과 두 건은 그대로 FAIL로 보존한다.

- `weather171-postdeploy95020-existing-listener-scrape-root-r1.json`은 source 단계에서
  실패했다. 별도 진단은 실제 HTTP 시도0, metrics/definitions 두 source 해시 일치와 선택
  process env 정합을 확인했다. host proc-root의 디렉터리 검사와 실제 container namespace의
  디렉터리 존재를 혼동한 검사 경계를 분리했으며, source 오류나 listener HTTP 실패로 단정하지 않는다.
- `weather171-postdeploy95020-existing-listener-scrape-root-r2.json`은 scrape 단계 FAIL이다.
  이 도구의 성공 원장이나 정상 metrics 응답으로 세지 않는다. 해당 실패의 상세 cause는
  저장된 고정 stage만으로 확정하지 않으며, 이후 최종 합본의 실제 단일 HTTP200과 구분한다.

모듈 source 확인과 HTTP200은 새 memo 분기의 hit 수를 증명하지 않는다. 현재 파일/key 분포와
collector 작업량·컨테이너 교체 때 tmpfs 상태가 달라질 수 있으므로, 기존979파일/32key의
합성 endpoint CPU 비교를 운영 ARM·전체 수집 CPU/RSS 개선률로 환산하지 않는다.

## 실제 공개 UI21

`weather171-postdeploy-runtime95020-public-ui-root-r1.json`은21개 모두 PASS,
command_exit0·elapsed14970ms다. 원 UI 결과 SHA256은
`312efe8e2caa31607174f2142329b0e400481395c5f6c1a255193e0629b7b7e5`이다. 실제 로그인HTTP200·Secure/HttpOnly 쿠키·메뉴4개,
인증 재조회·지도 canvas/실제tile20·목록/출처catalog·기존 read API200을 확인했다.
로그아웃 뒤 쿠키 제거·보호 화면의 로그인 요구와 domain write0, JavaScript/console/API/
HTTP 오류0·정확 owned browser container 정리를 확인했다. 허용 write는 로그인/로그아웃뿐이다.
`navigation_abort`16은 별도 계수로 기록돼 있으며 이를 네트워크 실패0 표시에 합쳐 지우지 않는다.
이 시험은 기존 admin 화면의 읽기 수용이며 신규 Map service export 공개·소비자 수용이 아니다.

## 원래 일정 복원과 열린 수용

2026-10-26 07:06:00Z에 기존21RUNNING/2STOPPED를 복원했다. 중지 상태인
`weather_export_publisher`·`weather_native_incarnation_recovery`는 그대로다.
공유 daemon/타 프로젝트 instigator 변경0·Map 연동 신규 활성화0이며 복원 시 active_runs는
빈 배열이었다. 이 한 시점의 drain을 장시간 안정성·모든 다음 수집의 성공으로 확대하지 않는다.

다음은 계속 미수용이다.

- 운영 ARM CPU/RSS·동시 수집의 전체 비용/처리량·장시간 안정성 및 실제 memo-hit 계수.
- Map export 활성화·승인 source bootstrap·Map 실제 소비·공유 artifact/GC와 관련 상위 gate.
- 운영 workload가 같은 전후 비교. source와 listener가 정상이라는 사실만으로 절감률을 계산하지 않는다.

`W-METRICS-01`의 제한 메모 구현/정확성·CI·배포/기존 HTTP/UI 단위는 수용했지만 상위 운영
비용 과제는 미완료로 유지한다. 이번 문서 정산은 collector·schema·설정·API·서비스를 바꾸지 않는다.
