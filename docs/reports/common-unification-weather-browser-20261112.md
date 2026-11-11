# Weather 최종 후보9 브라우저 검증

## 최신 결과 — 2026-11-12

Weather 소스 기준점은 e21e281이며, 이 문서를 포함한 변경은 fixture 서버 준비 확인과 그 회귀 검사만 보강한다. 제품 화면·API·공통 라이브러리 코드는 바꾸지 않았다. Draft PR은 digitie/kor-travel-weather#226이다.

| 검사 | 결과와 범위 |
| --- | --- |
| n150 일반 브라우저 | 기존 20개 모두 통과, 실패·skip·flaky 0 |
| n150 타일 장애/회복 | 기존 5개 모두 통과, 실패·skip·flaky 0 |
| 전체 frontend 단위 | 27개 파일, 308개 통과(기존 304 + 준비 확인 회귀 4) |
| 전체 frontend lint / 타입 | 경고 0 / 통과 |
| React Doctor | scope full, blocking warning, 87개 파일 진단 0 |
| 외부 정본 guard | integration-9-review를 명시해 통과 |
| root 및 n150 실제 설치 | UI 94·tokens 25·core 25·web 85·CSS 19개 파일이 각각 tgz 원문과 일치, vendor SRI = lock |
| 소스 대조 | root/n150의 lock·원장·runner·helper·test SHA256 모두 일치 |

일반 production build는 부모가 확인한 후보8 성공 결과와 n150의 기존 성공 빌드를 사용했다. 부모의 common-c8-c9-runtime-equivalence.json은 후보9 변경이 UI/tokens의 검사기 파일뿐이며 runtime/CSS/metadata가 같음을 증명한다. 이 차이만으로 일반 build를 반복하지 않았다. 타일 5개 검사는 전용 dummy 공개 키를 넣어 후보9 소스에서 실제 build를 다시 수행한 뒤 통과했다. 그 .next는 테스트 전용이며 운영 배포 입력이 아니다.

## 준비 시간 초과 조사와 변경 근거

원래 browser.log/result.json의 실패는 실제 테스트 진입 전 서버 준비 확인 단계였다. stdout/stderr를 노출한 최초 재시도에서도 시간 초과를 보았으나, 당시 Next 출력은 없었다. Node loopback 진단에서 localhost와 127.0.0.1 모두 응답했으므로 IPv6를 최초 원인으로 단정하지 않는다.

확장 대기 진단의 준비 성공은 수용 gate로 계산하지 않았다. 별도 진단 runner는 원래 100회/200ms 예산을 유지한 채 약 5.2초에 준비되고 20개를 통과했다. 최종 runner도 20초 예산을 유지하며, 20개 및 타일 5개의 기존 assertion·선택·workers=1을 그대로 실행했다. 과거 무로그 시간 초과의 최초 원인은 여전히 미확정이다.

다음 결함은 코드와 실제 회귀 검사로 확인했다.

- exitCode가 null인 시그널 종료를 놓치므로 signalCode도 검사한다.
- fetch가 끝나지 않으면 기존 반복 대기가 무한히 길어질 수 있어 전체 20초 예산 내 요청별 AbortSignal을 사용한다.
- 준비 확인 HTML은 사용하지 않으므로 응답 body를 닫는다.
- 자식 stdin을 DEVNULL에 연결하고 stdout/stderr는 보존한다. 서버 bind와 검사 origin은 같은 loopback 주소를 사용한다.

회귀 4개는 503→200/응답 정리, 시그널 종료, 지속 503 실패, 실제 무응답 loopback 연결의 중단을 검증한다. 시그널 검사·응답 정리·요청 중단을 각각 제거하면 해당 검사가 실패하는 red 로그를 보존했고, 복원 후 4개와 전체 308개를 통과했다. suppression·assertion 삭제·광역 skip은 추가하지 않았다.

## 재현 및 증거

frontend에서 다음 검사를 수행했다.

~~~sh
node node_modules/eslint/bin/eslint.js . --max-warnings 0
node node_modules/next/dist/bin/next typegen
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vitest/vitest.mjs run
node node_modules/react-doctor/dist/cli.js . --scope full --yes --no-telemetry --no-cache --blocking warning
node scripts/run-browser-fixtures.mjs
node scripts/run-browser-fixtures.mjs --tile-fixture
~~~

외부 정본 검사는 설치된 @kor-travel/ui/check-dependencies.mjs에 --lock package-lock.json, --artifacts common-artifacts.json, 부모 common의 frontend-dependencies.json을 --policy로, root/artifacts/integration-9-review/common-artifacts.json을 --canonical로 지정했다. KT_REQUIRE_CANONICAL=1, --profile web, --source app --source components --source lib를 명시했다. 공통 소스는 수정하지 않았다.

원시 증거는 root/validation/weather-final-c9-20261112/에 보존한다. 로그인 storageState·trace·환경 dump는 수집하거나 커밋하지 않았다.

- final-c9-result.json 및 final-c9-browser.log/report.json 및 final-c9-tiles.log/report.json: 최종 20+5개 결과
- unit-final.log, lint-final.log, type-final.log, doctor-final.log
- external-canonical-guard.log, root-c9-installed-bytes.json
- n150-c9-installed-and-processes.json, source-stage-sha256.json
- readiness-red-*.log, readiness-unit-restored.log
- 원래 browser.log/result.json, c9-browser-initial.log, strict-readiness-browser.log

n150 소유 stage는 root/validation/weather-c8-7b5536f0db18/이며 이름은 최초 후보8 기준으로 유지했다. 현재 lock·원장·실제 설치·runner는 후보9와 이 변경이며 SHA256 대조로 구분한다. Node 22.22/npm 12 및 기존 Chromium 1254 cache를 재사용했다. 기존 npm ci 433개와 build 성공 증거는 보존하고, 이번에는 UI/tokens만 명시 file install했다. 완료 후 해당 stage를 cwd로 가진 실행 프로세스는 0개다. 부모의 기존 orchestrator나 다른 작업자 프로세스는 종료하지 않았다.

## 남은 통합 gate와 배포 경계

로컬 검사는 완료했다. 후보9 원장은 여전히 CANDIDATE_NOT_RELEASED이며, 독립 2인 consumer 리뷰·정확 HEAD CI·선행 common PR/정책 코드 공개·Weather PR 머지·부모 go는 별도 gate다. 로컬 PASS를 CI나 정식 릴리스 완료로 대체하지 않는다. 중앙 reusable workflow pin은 부모의 2c1182e765bb2bc53e01e0962cf2a73683891e1d를 보존했다.

배포 계획은 [웹 단독 런북](../runbooks/common-ui-web-release-20261111.md)을 따른다. 병합 SHA에서 저장소 root context와 deploy/Dockerfile.web으로 새 immutable 이미지 태그/digest를 만들고, 실제 Manager context·현재 이미지/rollback 입력·보호 환경 보존을 재확인한다. 승인된 web 역할만 교체한 뒤 실제 로그인·대표 화면·모바일/focus·지도와 health를 확인한다. DB·RustFS·Dagster·API lifecycle은 실행하지 않는다. 이번 검증에서는 운영 접근·이미지 배포·merge를 수행하지 않았다.
