# 특보 후보 제한의 API 단독 배포와 첫 cold 지도 수용

## 현재 수용과 정확한 소스 범위

2026-10-28 04:42 UTC의 첫 Firefox 검증으로 **W-MARKER-PREFIX-01**의 한정 수용을 마쳤다.
[PR #190](https://github.com/digitie/kor-travel-weather/pull/190)은
`0683431255ae6e03911cf4cfd087a6549326156b`로 머지됐다. 부모는 main `bd56152`와 통합 후보
`7504f5a4d884e94a71acb9ce0f6f238fcf15cde3`다.

실제 배포한 API 소스는 도달 가능한 원 검증 후보
`21d9ea9090360ffed1ef68e42a1b7d866d3b53de`다. 원 후보의 marker 변경과 전체 검증을 사용해
API 한 개만 교체했다. 병합 main 전체·migration0035·export/UI 추가 제품·CS 새 이미지를
운영에 반영했다고 해석하지 않는다. 웹 a707·CS e393·공용 daemon·DB0034를 유지했다.
이 문서는 [원 변경·반례·이전 실패 보고서](2026-10-28-marker-alert-prefix.md)의 후속이며,
그 문서와 journal/resume의 이전 미배포 표현은 당시 이력으로 보존한다.

| 역할 | 실제 소스/이미지·신원 |
| --- | --- |
| API | 21d9 / `sha256:d307600054b8f9d5c38f9787075e829a53de2ce4dcd6d2ec03925003ce5eb80a` |
| API CID·시작 | `36cd9e8438a563cf0514da7c00afb3306ceceb67611ef5f7c6427ed4cc965de5`, 2026-10-28T04:34:30.370922288Z |
| 웹 | a707 / `6d2ba2c5…`, 기존 CID `1156e2ec…` 유지 |
| CS | e393 / `e6e3a048…`, 기존 CID `19b6bddc…` 유지 |
| 공용 daemon | 72ee 계열 / `dd27d458…`, 기존 CID `25fc0000…` 유지 |
| DB·설정 | PG18.6, `0034_marker_kind_indexes`·기존 인덱스4개, API·CS export True, publisher STOP 유지 |

공유 일정·외부 프로젝트·CS·웹·daemon·소비자 설정은 이번 API 교체 대상이 아니다.
Map/PinVi 소비자 OFF는 과거 지시/이력과 구분하며 이번 직접 관측은 **NOT_OBSERVED**다.
설정을 바꾸거나 export 목표를 재개하지 않았다.

## 실행·SDK·배포 정산

- build Invocation `f521a8a19a194764b68d049051a9ad27`: 실제 ExecMainCode1/status0,
  MainPID0·cgroup 부재, API image1개/arm64, child terminal·builder 복구·runtime/foreign 보존을 확인했다.
- SDK6/제품 app·repository 지문과 Dagster 미포함 검산 뒤 qualifier·SDK accept 각각 실제 원격0,
  local wrapper0·child terminal·잔여0·신호0이다. 실패한 이전 묶음을 성공으로 변환하거나 재사용한 것이 아니다.
- rollout Invocation `5ed74c1fd9814cc8b5adde69dd57ce38`: 실제 ExecMainCode1/status0,
  MainPID0·cgroup 부재, `api-only-deployed-health-verified`, web_preserved=true다.
  systemd 시작 명령0을 완료로 대신하지 않았다.
- 별도 postmetadata actual0·동일 읽기 두 스냅샷·API /version21d9·최종4역할·보호파일/Compose
  이미지 해석을 확인했다. `.env` 원문은 유지하고 정본 Compose의 API image scalar만 바뀐 결과다.
  실제 UI 전후 preflight/postflight SHA도 동일하다.

## 첫 cold Firefox 수용

별도 marker 예열 호출 없이 새 배포 뒤 실행한 첫 브라우저 수용 한 번이다. DB/OS cache를
비웠다는 뜻은 아니며 기존 warm 진단을 이번 결과로 대체하지 않는다. strict 하네스의
25초·본문/DTO·실패 기록·동시2 조건을 유지했다. 실제 Node/외부 wrapper 모두 종료0이고
29개 검사가 전부 통과했다.

| 최초 요청 순서 | 요청/응답 | 본문 완료 |
| --- | --- | --- |
| 1 | 60개, HTTP200·DTO/위치 대응 | 18.075초 |
| 2 | 60개, HTTP200·DTO/위치 대응 | 16.469초 |
| 3 | 60개, HTTP200·DTO/위치 대응 | 17.593초 |

동일6종류 필터·총180개 고유 위치, 처음 두 요청의 겹침과 세 번째 큐 진행, 동시 요청 최대2,
같은 범위 중복0과 누락/실패 없는 본문을 확인했다. **각 요청의 25초 이내 완료**를 수용했다.
처음 시작부터 세 묶음 완료까지는 **34.074초**다. 전국 전체의 모든 마커가25초 안에
완성되거나 어느 카메라·부하에서도 같은 응답을 보장한다는 결과가 아니다.

| 화면·상호작용 | 실제 결과 |
| --- | --- |
| PC1440 | 지도 top152px·높이824px·폭1120px |
| 모바일375 | 지도 top193px·높이615px·폭367px, 가로 overflow0 |
| 지도·상세 | VWorld 실제 타일50개 전부200, 같은 지도 위 상세 오버레이 |
| 표시·DTO | 기온13.7℃·풍속2.2m/s를 실제 DTO와 대조, 불필요 재조회 안내 없음 |
| 키보드 | 목록 선택 직후 상세 자동 초점, Escape 닫기·초점 복원 |
| 인증·정리 | 로그아웃/보호화면·쿠키 잔여0, JS오류0, 소유10PID 모두 ABSENT |

Root와 문서 작성자가 저장된1440/375 PNG를 직접 확인했다. 실제 runtime·하네스는 전후 불변이다.
이전 R15 초점 실패는 검사기가 초점을 먼저 옮긴 문제였고 별도 검증으로 해소돼 제품 수정하지 않았다.

## 소스 QA와 CI의 구분

- 원21d9 관련 WSL150/Docker150, 원21d9 전체 **WSL1545 PASS/actual0**와
  **Docker1544 PASS/1SKIP/actual0·독립 wait0**를 회수했다. Docker의1SKIP는 QA 이미지에
  Docker CLI가 없는 배포 계약 검사다. 소스648파일·의존성/PG18 조건은 실행 전후 불변이다.
- 통합7504는 원21d9를 부모로 정상 merge했고 marker 함수 원문을 유지했다. 관련150×2 및
  CI771 `40623613188`의 Python1577/프런트276·타입/린트/build를 별도 회수했다.
  CI의 실제 checkout168d102 tree는7504와 동일하다. 원150이 main의0035/export/UI 전체를
  검증했다고 하지 않으며 통합7504의 별도 로컬 전체 실행을 주장하지 않는다.
- 원 PRhead21d9 CI770 `40622054028`은 synthetic merge f83023f였고 standalone21d9 tree가 아니다.
  실제 운영 후보의 전체 증거는 앞의 원21d9 로컬 두 전체이며 CI 소스 문맥과 혼동하지 않는다.
- 원 소스 두 명·통합 증분 두 명의 독립 검토, 배포 도구 및 실제 UI 상수 두 명 검토 뒤
  실행·수용했다. 이 후속 문서 자체의 리뷰·PR 상태는 별도 문서 PR이 정본이다.

## 원본 증거

비밀·환경·DSN·원문 SQL/바인드·인증값은 commit하지 않는다. 아래 저장 경로는 로컬 재검산용이다.

| 원장 | 경로·SHA256 |
| --- | --- |
| 최초 UI 원본 | `/var/tmp/weather-live-prefix-21d9-20261028-602ce248f5` |
| 복사 UI result | `C:/Users/digit/.codex/tmp/weather-live-accepted-21d9-20261028-r1/result.json` · `fd09a3005ac00cb8da8574b502720f8164849bde57670160cc223680e095a7f9` |
| UI root receipt | 같은 폴더 `root-receipt.json` · `7d1a3aed53e1d6909560d6be841e2ac2e7b94b4034b145a914ddd5e7fae8f620` |
| UI pre/post | 같은 폴더 `preflight.json`/`postflight.json` · 둘 다 `7ced4d1171782ffd11c7751c0b1361f01f22d9fe3689aac2c2d538f11d7081ae` |
| 배포 관측 | `C:/Users/digit/.codex/tmp/weather-api-only-postbuild-helpers-21d9-20261028-r1/observation-20261028T043718618189Z.json` · `c39ad5457e7db4674c130cb27e031d8aba3c0637b2dcfd4c7ea7e189775ce5da` |
| postmetadata | `C:/Users/digit/.codex/tmp/weather-api-only-postrollout-helpers-21d9-20261028-r1/weather-api-only-post-metadata-20261028-r1/20261028T043744771128Z-metadata.json` · `21d2d6570a4224d485f05e44bf61c6b4c99789935eebb570f563f672c4081a8b` |
| 원21d9 전체 QA | `/var/tmp/weather-alert-prefix-13c068dba2f2/original-release-qa-final-r1/manifest.json` · `a5f85d60920d306c2168d245816ebd8cd6522609b8974c6c7f9ebbc309a9e287` |

기존 a707 첫 cold `9c71a7bf96`의26.817/25.539/25.316초 **FAIL**, 별도 warm R17 결과,
단순 LATERAL·정렬/fixture의 RED·QA 환경 실패는 [원 보고서](2026-10-28-marker-alert-prefix.md)와
각 원장에 보존한다. 실패를 재실행 성공으로 지우지 않았다.

## 이번 수용에 포함하지 않는 항목

전체 부하/장시간/모든 카메라의 지연 상한·원인별 개선율은 측정하지 않았다. DB planning/IO
기여와 데이터 분포 차이를 남기며 한 표본의 차이를 코드 단독 인과관계로 환산하지 않는다.
retention 물리 정리 완료, 특보의 위치별 실제 지역 적용 정확성, 외부 소비자 상태,
추가 provider/수집 전체 완주와 main0035/export/UI 전환은 별도 과제다.
특보 지역 계약을 확인하지 않고 표시를 임의로 여과하거나 정확하다고 단정하지 않는다.
이번 후속 문서 작업은 저장 파일 증거만 사용했고 새 운영 API/SQL/marker/provider/수집 호출은0이다.
