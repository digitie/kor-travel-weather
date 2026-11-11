# 2026-11-10 — 최신 main Weather 세 역할 이미지 반영

사용자 요청 “weather 최신코드 반영해서 이미지 올려”에 따라 main `ea7fda11da34775858d62bc034a5eb40778e3890`를 API·수집기·웹에 반영했다. PR208 및 PR212~217이 포함되며 배포 부모는 실제0/done이다. API·수집기는 healthy, 웹은 running이며 HTTP와 실화면을 별도로 확인했다. 웹에는 Docker healthcheck가 없으므로 그 역할에 healthy 표기를 붙이지 않는다.

## 실제 반영

| 역할 | 이미지 SHA256 | 실제 CID | 명령 포트 |
| --- | --- | --- | --- |
| kor-travel-weather-api | `0a27c0e2fd2970d29cb267072a4679bf4ff9f1d129a25d6937fb058f47125c68` | `4677787b29128c0153f5867735bd03e44b5e20fa0f70894de2a2c895eed4fd5b` | 12201 |
| kor-travel-weather-dagster-code-server | `456a147835450e9076ef990e9556c12fa6c4d1b925dc65ff640c6a4e5caae424` | `9cf92ae7d3460543a14133f103fcf17ac6df7a1821e30ecec57023c538e9ff5d` | 12206 |
| kor-travel-weather-web | `a70483301f0cc670cbaecdef10c4c3a95930bf5105d6acdcb45f428d30251542` | `449d03eb1e5aee3c742b4ae8f96a0910dc9363dd6a14651cd4294bbdbddca0ee` | 12205 |

schema는 기존 `0039_page_liveness` 그대로이며 migration은 실행하지 않았다. 원 Weather27 일정의 각각의 상태를 복원했고 Manager adopt/verify/show는 같은 새 release를 가리키며 healthy다. 설정·키·마운트·자원·수집기 live healthcheck·다른 서비스는 전후 검산으로 보존했다. 문서 커밋은 제품 source SHA와 별도이며 이 문서 때문에 제품을 재빌드하지 않는다.

## 포함 변경과 검증 범위

- PR208: export 읽기 비용·잠금 경합 축소와 최대60초 상태 재사용.
- PR212: run 원천 목록의 키당 왕복·원문 전송 제거.
- PR213: 수집 배치 조회의 레코드당 왕복 축소.
- PR214: 홈 카드를 완료된 순서대로 개별 표시.
- PR215: 재사용 파일 stat·공용 승인 선택의 cursor 페이지 재사용.
- PR216: 다른 구역 특보를 모델 구성 전에 제외.
- PR217: 상세 예보를 화면의 현재·내일·시간별 구간으로 제한.

정확한 main SHA의 [CI42763637869](https://github.com/digitie/kor-travel-weather/actions/runs/42763637869)는 Python/frontend SUCCESS다. 같은 소스를 NVMe에서 최소 이미지로 빌드했고 Python141개 source 파일·의존성 및 실제 포트, 웹96개 runtime 파일을 검산했다. 별도 SDK 설치·웹 의존성 변경·추가 전체시험은 없었다.

운영 HTTP는 health/version·공개 위치1개·관리 위치1개·최근 run10개·지역 특보의6요청이 모두200이었다. 각 단일 wall은0.1870/0.0134/0.5175/0.3310/0.1276/0.9276초다. 제공자 호출·전국 export 성능시험은 실행하지 않았다. 이 결과를 p95·cold·최초 본문1초나 수집 성능 달성으로 확대하지 않는다.

PC1440의 홈4카드·서울 선택 상세·시간별24개 자료 열과 그래프1개·요청 상한5000 및 현재부터 내일 말까지의 시간 경계를 실제 Firefox157에서 확인했다. 현재10.6°와 일부 날씨/세부 항목 미제공을 보존한다. 초기 PC 상세 캡처의 내일은 예보 조회 중이었으므로 그 텍스트만으로 내일 정상 수용을 주장하지 않는다. PC 가로 넘침·브라우저 오류0, logout200이다.

모바일375도 지도 준비 뒤 목록·서울 위치 상세·내일 상태·시간별 그래프를 확인했다. 브라우저 오류·가로 넘침0, logout200, 소유 브라우저 자식 종료를 확인했다.

첫 PC/모바일 묶음의 모바일 목록 전환 TimeoutError와 후속 모바일 로그인 Error·지도 대기 TimeoutError는 원문으로 보존했다. 후속 지도 대기 실패 캡처는 로그인 화면이었다. 마지막 관측은 인증된 홈 내용이 표시된 뒤 이동하며 지도 준비 후 목록을 누르도록 대기 순서를 보완했다. 제품 변경·재배포 없이 별도 원장에 기록했고 이전 FAIL을 PASS로 덮어쓰지 않았다. 홈의 “최근 수집 실패·8,118건 저장”은 실제 기존 수집 상태이며 배포 성공 때문에 성공으로 변경하지 않는다.

## 수집 중지와 데이터 보존

이전 인간의 “강제 중지하고 올려” 지시에 따라 Weather의 실행·큐17건에 Dagster 정식 강제 취소만 요청했다. 대상17건의 CANCELED·Weather active0·실제 old worker0을 확인한 뒤 수집기를 교체했다. OS 직접 kill·다른 서비스 run 취소는 없었다. 기존27 일정은 원래 중지 상태까지 동일하게 복구했다.

정확한 취소 owner에 대해서만 canonical interrupted-run 회수를 실행해 변경1건을 정산했다. 해당 app run들의 기존 `values_loaded` 8,118/404,544/116,508은 동일하다. 이것은 대상 run 카운터이며 전체 DB의 고유 원본 행 수로 확대하지 않는다. 원본 삭제·자동 rollback·backup/restore·prune는 없었고 백업/복원은 사용자 지시대로 SKIPPED_BY_USER다.

## 실패 이력과 부모 종료

| 작업 | 최초 Invocation | 실제 종료 | 결과 |
| --- | --- | --- | --- |
| build | `01bcb7eb5cf7405cb735b03c7760965b` | 1 | failed 보존 |
| build-r2 | `200a2a38d23b4f8cb7b0647671258ca6` | 1 | failed 보존 |
| validation | `50c265c5e88c4141b9bddb3607b6adf9` | 0 | done |
| deployment | `05ff487c3ff34910914fc70f899e79fe` | 0 | done |

첫 build는 image ID를 Docker Hub base 이름으로 해석한 입력 오류로 Docker child1이었다. 별도 R2는 세 Docker build child0으로 이미지를 완성했으나 마지막 보조 검사가 기존 `.dockerignore`의 생성 타입 파일 `next-env.d.ts`까지 요구해 바깥 부모1이었다. 실제 runtime 파일96개는 모두 동일했고 mismatch0이었다. 별도 validation-only 부모0에서 이 생성 파일만 제외한 native 검산을 완료했으며 추가 재빌드는 하지 않았다. 두 원 실패는 그대로 남겼다.

최초 네 Invocation의 terminal, MainPID0·cgroup 부재·시작한 자식과 종료 영수증의 대응을 확인했다. 마지막 배포의 소유 자식85개는 모두 종료했다. 새 실패를 감추거나 바깥 CLI1을 전체 성공0으로 바꾸지 않았다.

## 정본과 남은 한계

정본은 로컬 `C:/Users/digit/.codex/tmp/weather-latest-image-20261110/final-acceptance.json`, SHA256 `91ea27c07a20763ca99a5c75cba00e905dc0f461138231c41153d1657cfa17e6`다. 세 역할의 source는 모두 main ea7fda1이며 Manager release는 `/srv/ktdm-releases/weather-adopt-20261110T063523Z-1e51fd2031764d6a858c21e68536b6e5`다. 원장에는 HTTP·화면·설정·원 일정·취소·부모별 실제 종료와 증거 SHA를 연결했다.

기존 export 최초 구성 지연, 원천 stale·미제공, 자료가 없는 예보·특보 구역 완결성은 별도 미완이다. 이번 최신 이미지 반영 성공으로 완료 처리하지 않는다. 모바일 마지막 관측도 위에 적은 범위만 수용한다.
