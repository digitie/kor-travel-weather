# Weather111 독립 적대적 FULL post-fix 리뷰 — James r2

- Review ID: WEATHER111-JAMES-R2. 실행 주체 /root/review_finish_a.
- **판정: REQUEST_CHANGES. OPEN P0=0 / P1=0 / P2=1.**
- 이전 W111-A-P2-01은 문서화한 단일 Node 프로세스 동시 진입 범위에서 FIXED, W111-A-P2-02의 직접 실패 replay 오표시는 FIXED. 새 W111-A-P2-03은 OPEN이다.
- Weather base: 28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac.
- Weather exact head: 78817516561968868bb7a77c6cb17c6b90cefee5.
- 이전 본인 검토 head: c52e08b5a5d7401fc600ddd4a378f327f6395f60.
- Common pin: c067a7f6a11b106931a73cd7465503d90e54d976.
- Manifest: /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r2.json.
- Manifest 실제 SHA256: bfc6246e4361a371761ac28810e7aced666f6676e664fd8ce5afcad51bd1d3a0.
- Transport 1e5dbeb44e12446858acd09ff867f6e1cb2511db는 문맥 전용이며 소비자 정식 PASS로 기록하지 않는다.
- 시작 UTC: 2026-10-18T02:32:25.973983+00:00.
- 상대 reviewer의 이번 결과·보고서는 열람하지 않았다.

## 범위와 격리

적용 Weather AGENTS와 원천/lineage 보존, fail-closed admin, DTO/OpenAPI/TypeScript 동기화 규칙을 기준으로 검토했다. base→head는 37파일 1143+/19-다. 이전 r1에서 읽은 전체 base→c52 변경 및 연결 경로와 이번 c52→788 전체 delta를 함께 검토했다. frontend BFF/화면/CSS/시험/E2E, middleware/session/Origin, API/OpenAPI/TS, migration0021, core/settings/Compose, Dagster 및 Common callback/pin, 문서와 lockfile을 포함한다.

제품은 git show의 immutable object로 읽었다. 시작·종료 worktree status는 clean이다. 제품·index·branch·다른 작업자의 WIP를 변경하지 않았다. 소유 임시 Git-object 복사/메모리 transpile·compile/인메모리 SQLite만 사용했고 실행 뒤 임시 fixture는 정리했다. 이 보고서만 새로 영구 저장한다.

운영/SSH/Docker, 실제 provider/키, 실제 DB/DDL, 실제 Dagster 실행, Daemon 시작 및 실제 브라우저를 실행하지 않았다. 아래 컴포넌트 반례는 실제 TSX 함수의 오프라인 hook/JSX stub 실행이며 React DOM 또는 실브라우저 검증이 아니다. root의 CI39090100738 Python658/frontend211/lint/type/build/OpenAPI SUCCESS 및 loopback 실브라우저 UI22/두 화면크기 증거는 의뢰자가 전달한 외부 증거이며 본인 실행 결과로 합산하지 않는다.

## W111-A-P2-03 — 늦게 완료한 상태 조회가 수동 예약 실패를 지운다

상태: **OPEN / P2**.

위치:
- packages/kor-travel-weather-admin/frontend/components/address-refresh.tsx:27: GET 성공 때 공용 error를 무조건 빈 문자열로 덮는다.
- 같은 파일 :46–49 및 :58–59: replay FAILURE/CANCELED 또는 예약 실패를 같은 error 상태에 기록한다.
- :72–73: 예약 중에도 상태 새로고침 버튼은 별도 요청을 시작할 수 있고, 예약은 앞선 상태 GET을 취소하지 않는다.
- :76: 유일한 실패 role=alert가 error의 빈 값 변경으로 사라진다.

실패 시나리오:
1. 최초 GET이 configured=true를 반환하여 수동 실행 버튼이 활성화된다.
2. 사용자가 상태 새로고침을 눌러 두 번째 GET을 시작하고 응답은 대기한다.
3. 사용자가 수동 요청을 재확인하여 POST가 기존 실행 FAILURE와 replayed=true를 202로 반환한다. 화면은 수정된 실패 안내를 올바르게 표시한다.
4. 먼저 시작했던 두 번째 GET이 200으로 완료된다. load():27의 setError("")가 방금 받은 예약 실패를 지워 버린다. 실패가 해결되거나 새 실행이 만들어진 것은 아니다.

재현은 고정788 TSX 전체를 TypeScript transpile하고, 소유 React hooks/JSX stub과 지연 Promise fetch로 실제 load/launch 콜백을 실행했다. 첫 GET은 정상 요약, 둘째 GET은 지연, POST는 정상 계약의 {runId:"prior-failure",status:"FAILURE",replayed:true}다. 표시 구조에서 alert를 검사한 뒤 지연 GET을 완료했다.

관측:
    {
      "failure_before_pending_get_completes": true,
      "failure_after_get_200": false,
      "get_count": 2,
      "render_has_prior_failure_link": true,
      "real_browser": false,
      "network": false
    }

이는 이전 P2-02의 직접적인 성공 notice 오류와 별개인 비동기 상태 소유권 오류다. 이번 수정의 “실패 replay이면 void load를 호출하지 않는다”(:57)는 이미 진행 중인 GET을 통제하지 못한다. 일반 예약 HTTP 실패도 같은 공용 error 경로다. 실행 기록 링크가 남는 것은 실패 안내 소실을 정당화하지 않는다.

권고: loadError와 launchError를 분리하여 상태 GET 성공은 자기 조회 오류만 지우게 한다. 동일 순서의 지연 GET → 실패 replay → GET 성공 회귀를 실제 React/브라우저에서 추가하고, 취소 및 예약 네트워크 실패도 함께 확인한다. 판정·시간 제한을 완화하거나 실패 replay를 새 예약으로 처리하지 않는다.

Root에게 이 반례를 전달했고, root는 별도 loopback 브라우저 재현 및 두 소비자 오류 상태 분리 후 새 commit/CI/post-fix 리뷰를 진행하겠다고 답했다. 본 보고서는 그 미래 변경을 포함하지 않고 exact788175의 OPEN을 보존한다.

## 이전 지적 closure

### W111-A-P2-01 — 단일 프로세스 동시 POST

app/api/address-refresh/route.ts:12 및 :69–71에서 인증/Origin/body 검사 뒤 전역 submissionInFlight를 동기적으로 설정하고 finally에서 해제한다. 실제 POST 함수를 같은 UUID로 동시에 실행한 소유 VM fixture에서:

| Git object | 두 HTTP 상태 | launch 호출 |
| --- | --- | --- |
| c52e08b5 | 202 / 202 | 2 |
| 78817516 | 202 / 409 | 1 |

추가로 launch 오류→재시도는 502→202이며 upstream 원문 오류는 외부 응답에 노출되지 않았다. 409를 받은 클라이언트는 동일 requestId를 유지해 재확인할 수 있다. 고정 route.test.ts의 서로 다른 ID/동일 ID 경합, 가드 해제 및 replay 시험도 통과했다.

**FIXED 범위는 단일 살아 있는 Node 프로세스 내 동시 진입이다.** 이 Boolean은 여러 replica/worker의 공유 원자 예약이 아니며, 프로세스 재시작·상위 요청의 불명확한 완료 후 전역 exactly-once를 보장하지 않는다. docs/reverse-address-refresh.md는 현재 단일 프로세스와 확장 전 공유 원자 경계 필요성을 명시한다. 따라서 원래 권고의 분산 보장까지 구현했다고 닫지 않으며, 문서화한 현 배치 범위의 closure다. 현재 검토에서 다중 replica 운영 여부를 확인하지 않았다.

### W111-A-P2-02 — 이전 실패를 새 예약으로 표시

route는 이전 결과에 replayed:true, 새 결과에 false를 반환한다. 화면 :46–55가 FAILURE/CANCELED/SUCCESS/기존 진행/신규 예약을 구분하며 링크도 “실행 기록 보기”로 바뀌었다.

고정 컴포넌트의 실제 launch 분기로 다음 다섯 상태를 독립 실행했다:
- FAILURE replay: alert 있음, 정상 완료/새 예약 notice 없음.
- CANCELED replay: alert 있음, 정상 완료/새 예약 notice 없음.
- SUCCESS replay: 정상 완료 notice, 새 예약 notice 없음.
- QUEUED replay: 기존 요청 확인 notice.
- 신규 QUEUED: 새 예약 notice.

직접적인 잘못된 성공 안내는 **FIXED**다. 다만 위 새 P2-03의 앞선 상태 조회와 경합하면 해당 올바른 오류가 소실되어 전체 상태 진실성 수용은 여전히 차단된다.

## 나머지 신뢰 경계와 최종 주소 사용

인증/CSRF:
- 새 BFF는 서명 쿠키 및 durable session active를 모두 확인한다.
- POST는 exact Origin, JSON, bounded body 및 requestId UUID 한 필드만 허용한다.
- job/location/repository/config는 서버 고정이며 임의 GraphQL job 실행으로 연결하지 않는다.
- 상태 API는 require_admin이며 브라우저에 provider 키를 전달하지 않는다.
- provider/GraphQL 원문 예외 대신 고정 오류를 반환한다.
- middleware.ts, lib/session.ts, lib/origin.ts는 이전 c52와 바이트 동일함을 확인했다. 본인 실행 route9시험의 인증은 대역이므로 실제 로그인/쿠키 검증으로 확대하지 않는다.

주소/원천:
- 활성 weather_locations만 DatabaseAddressSource로 읽고 별도 entries/control에 파생 주소를 저장한다. 원천 region_code, nx/ny, 좌표, 관리자 metadata 또는 weather fact를 재주소로 UPDATE하지 않는다.
- 소유 인메모리 SQLite에서 실제 Weather adapter + Commonc067 source/엔진을 연결하고 활성1/비활성1 source를 주었다. 활성1만 처리되어 derived address가 저장됐고 두 원천 행은 바이트상 같았다. Weather ORM은 컬럼 stub이므로 실제 전체 ORM/PG 회귀를 대신하지 않는다.
- 최종 주소의 현 표시/사용처는 admin GET /v1/admin/address-refresh의 최대50개 요약 및 Admin Dagster 표다. 일반 locations/weather fact/Map 주소 DTO를 자동 대체하지 않는다. 현 가이드의 별도 파생 결과·원천 보존 범위와 일치하며 모든 일반 화면의 주소를 바꿨다는 완료 주장으로 확대하면 안 된다.
- VWorld 우선/Kakao 보조, 위치별 달력6개월, 수동 force/retry는 Commonc067으로 위임된다. 공개 API의 page(cursor,limit,now,cycle,force), retries, 필수 pending_failures를 source 메서드 그대로 전달하는 연결을 소유 실행으로 확인했다.
- migration0021/API/OpenAPI/TS는 이전 r1의 선언·필수 필드 비교 범위와 동일하다. 이번 변경에서 DB schema/API DTO를 임의로 바꾸지 않았다. uv.lock c52→788은 Common Git pin 3곳만 바뀌었고 같은 버전의 다른 dependency 선택 변경은 없다.

반응형·접근성:
- named section/제목/button, role=alert/status, table caption/헤더 및 표 스크롤 영역 tabIndex0이 있다.
- min-width640 표를 overflow-x:auto 영역에 넣어 작은 화면에서 스크롤한다. CSS는 c52와 바이트 동일하다.
- 실제 키보드/스크린리더/모바일 브라우저 조작은 NOT_RUN. root 두 크기 결과를 본인의 새 관측으로 기록하지 않는다.

Daemon:
- 등록/sensor는 Daemon start/restore를 수행하지 않는다. OFF일 때 수동 예약은 대기한다는 안내를 유지한다.
- 실제 자동 갱신 주기·run 저장·provider·복구를 운영에서 확인하지 않았다. 소비자 source 수용과 운영 Daemon ON을 혼동하지 않는다.

## 독립 실행 및 정산

| 확인 | 결과 | 한계 |
| --- | --- | --- |
| Manifest 실제 SHA 및 immutable head | 일치 | CI 재조회 아님 |
| 고정 BFF route.test.ts, 소유 임시 archive | **9 PASS**, test23ms/전체341ms, exit0 | Next/session/fetch 대역, 실제 네트워크 없음 |
| 실제 POST old→new 동시 UUID | launch2→1, 202/202→202/409 | 한 Node VM 프로세스 |
| 실제 POST 오류 후 재시도 | 502→202, 원문 비노출 | upstream 응답 대역 |
| 고정 TSX 다섯 replay/예약 상태 | 기대 구분 전부 충족 | hooks/JSX stub, 실제 React DOM 아님 |
| 고정 TSX 지연 GET/실패 replay | 새 P2-03 재현 | 실브라우저 재현은 root 후속 |
| Weather adapter + Commonc067 인메모리 source | 활성1 처리, 파생 결과 표시, 원천 행 불변 | 실제 PG/전체 Weather ORM/SDK 없음 |
| middleware/session/origin/CSS c52→788 | 바이트 동일 | 정적 보존 확인 |
| git diff --check base→788 | **exit2** | frontend/lib/api.ts:376 “new blank line at EOF” 1건 |

마지막 whitespace는 비차단 형식 메모다. diff-check PASS로 바꿔 적지 않았으며 제품 수정은 하지 않았다. 기존 r1 full 회귀 결과와 root full CI/UI 수치를 본인 9개 시험에 합산하지 않는다. Python658/frontend211 전체 suite, 실제 provider/키, 실제 PG/DDL/경합, 실제 Dagster GraphQL/native run, 브라우저 E2E는 본인 NOT_RUN이다. Transport formal gate는 제외한다.

새 P2를 수정한 immutable head에서 원 반례 및 수정 delta를 다시 검토해야 한다. 이번 보고서에 미래 fix를 소급하거나 예전 실패 원문을 삭제하지 않는다.

## 전달받은 이번 리뷰 요청 원문

```text
Weather111 formal FULL post-fix r2 독립 적대 리뷰를 진행하세요. 동일 manifest /home/digitie/dev/reverse-address-weather-review-manifest-20261018-r2.json SHA256 bfc6246e4361a371761ac28810e7aced666f6676e664fd8ce5afcad51bd1d3a0. Weather base28f731e6dcb12d5f4cde2ad86fd25acbf3da2eac -> exact head78817516561968868bb7a77c6cb17c6b90cefee5, 이전본인c52e08b5. 최종 CI39090100738 Python658 및 frontend211/lint/type/build/OpenAPI 모두 SUCCESS. Commonc067 고정 및 새 callback, 단일 Node 프로세스 BFF 동시 진입 가드, 이전 실행의 실패/취소/성공 replay 상태표시를 보강했습니다. UI11앱별(총22) fixture provider loopback 실브라우저 통과·스크린샷 test-results 두 크기. 통과시키는 것이 아니라 결함을 찾는 목적이며 애매하면 재현을 시도하세요. 본인 이전 동시POST/실패replay 지적 closure와 frontend/live UI 렌즈(반응형·키보드·상태·auth/CSRF·오류 복구), 전체 base→head 회귀/공개계약을 검토하세요. 복수 replica 전역 exactly-once는 미보장·현재 단일 프로세스라는 문서 한계도 검토하세요. Transport는 context만, formal gate 별도. 읽기 전용 Git object/소유 fixture만 사용; 다른 작업자가 있으니 소스/index/branch 변경·되돌리기 금지. 운영/SSH/실provider/키/Daemon/Docker 조작 금지. 상대 새 결과 비공개. /home/digitie/dev/reverse-address-weather-review-james-r2.md에 원문·SHA·P0/P1/P2·파일:행·실패시나리오·판정/한계 및 이 전달요청 원문을 보존해 주세요.
```

종료 UTC: 2026-10-18T02:38:31.628674+00:00
