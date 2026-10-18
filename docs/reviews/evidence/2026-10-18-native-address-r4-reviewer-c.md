# Weather115 / Transport86 — C 독립 FULL 소비자 리뷰 r4

## 정확 기준과 판정

**CONSUMER_SOURCE_PASS_DEPENDENCY_HOLD**다. 소비자 코드 자체의 새 열린 P0/P1/P2/P3는0이며, Common41 a68의 native statement_timeout 소실 C41-C-P2-02는 별도 OPEN이다. 전체 통합/머지/운영 수용 PASS로 집계하지 않는다. Common 수정본으로 pin을 바꾼 최종 delta 검토가 필요하다.

| 저장소 | base | 정확 head | manifest SHA256 |
|---|---|---|---|
| Weather115 | 332cd1f2fc74dd263473ea57ebc2b0594265f883 | 5d8547e8b377176fc2669e29fbe163b72209cb18 | 2af750b4d7690cd4ba9865625fd6b78ec03e413a3b5b046a7970a5b690475093 |
| Transport86 | 61a566dee0b1cc40f858652c0683883c26e56411 | bb191137406b95a43f6ed8e477b35b786f88df0d | a18256ee4805fbccd362dbd907e1549a08f58981277f232a1525c4ea53fd0a3c |

두 manifest는 /home/digitie/dev/reverse-address-{weather,transport}-native-review-manifest-20261018-r4.json이다. Weather15/Transport43개 전체 파일 bytes를 git show exactHEAD로 읽어 hash 일치를 확인했다. Transport의 기존 review raw 파일은 독립성 정책에 따라 hash만 검증하고 원문/판정은 읽거나 근거로 사용하지 않았다.

읽은 제품 범위는 BFF route·GraphQL scope 문서·UI·수집 원천 SELECT·sidecar·등록 정의·설정·모델/DTO/migration/OpenAPI·집중 회귀·e2e/지연 proxy·검증 문서다. 저장소 AGENTS 및 존재하는 CLAUDE/SKILL·관련 라우터/운영 범위 문서를 먼저 읽었다. 원 WIP·index·branch 수정0, 다른 agent 결과 원문 열람0이다.

## 보안·요청 식별자

양쪽 route는 세션 인증 후 같은 Origin/JSON/body 한도/단일 canonical UUID를 검사한다. 임의 selector/job/config를 브라우저에서 받지 않고 고정 location/repository/address_refresh_job/force=true만 사용한다. readonly 일반 Dagster operation allowlist에는 새 mutation을 넣지 않았다.

UUID는 이전 요청 조회·실행 tag·launchIdempotentRun에 동일한 소문자로 전달된다. 이전 terminal/진행 상태는 새 실행 없이 replay하며 NOT_STARTED만 같은 native 요청으로 제출 복구를 요청한다. 다른 active run은409다. 늦은 첫 생성이 previous 이후 active 조회에 보이는 경우도409 후 같은 UUID로 확인하므로 새 무작위 launch로 우회하지 않는다.

GraphQL unsupported/error/legacy LaunchRunSuccess는502로 실패한다. timeout/UNKNOWN 후 UI의 requestId ref는 유지되고 성공 응답 뒤에만 비운다. BFF 단일 프로세스 submissionInFlight는 전체 replica의 영속 exactly-once로 과장하지 않는다. 공용 webserver 선행 배포와 Common native 저장소의 durable 계약이 별도 선행이다.

Weather GET은 서버의 관리자 토큰을 private fetch에만 붙이고 Transport도 전용 admin token을 사용한다. source에는 키/원문 예외를 응답하는 새 경로가 없다. 실제 인증/session 원천 값은 읽거나 사용하지 않았다.

## 데이터·UI 계약

Weather의 서비스는 enabled weather_locations를 읽고 원천 주소·KMA 격자·region_code·metadata를 쓰지 않는다. 원 서비스는 base에 이미 존재하며115의 변경과 회귀 검토에서 보존을 확인했다.

Transport는 FuelStation/BusTerminalReference/FerryPort/RailStationReference/RestAreaReference의 유효 국내 좌표만 읽는다. 기존 지도와 같은 data_go_kr_port_guideline FerryPort를 제외하며 admin_manual/NULL 출처는 유지한다. 원천 SELECT를 쓰기 대상으로 사용하지 않는다.

두 소비자는 Common의 reverse_address_entries/control sidecar만 갱신한다. 현재 원천과 같은 좌표인 결과만 sample/최근 확인일에 포함하고 비활성·legacy·이동 전 결과는 숨긴다. 저장된 옛 sidecar와 원천 행은 보존한다. Transport0025 migration의 고정 컬럼/index와 Base.metadata 등록, GET-only OpenAPI Summary DTO/프론트 타입을 대조했다.

달력6개월은 Common에서 Asia/Seoul로 계산한 뒤 원 timezone으로 복원한다. 직접 월 경계2개를 검사했고180일 환산은 없다. UI 확인일·다음 갱신일도 명시적 Asia/Seoul이다.

UI의 실행 오류와 상태 GET 오류는 분리되어 늦은 GET 성공이 POST 실패를 지우지 않는다. FAILURE/CANCELED/SUCCESS와 단순 예약/replay를 별도 메시지로 표시하며 status를 무조건 수집 성공으로 바꾸지 않는다. configured=false에는 버튼이 비활성이다. 화면 표는 지역 overflow-x:auto와 focusable region으로 두며 실제 브라우저 수용은 이 리뷰가 재실행하지 않았다.

sensor는 due/manual 진행을 Common에 맡기며 UI/서비스가 daemon을 시작하지 않는다. 자동 갱신 OFF에서도 이미 시작한 수동 cycle의 재개 의미를 유지한다. 실제 운영 daemon OFF 제약을 변경하지 않는다.

## 직접 실행과 delta 보존

사전 검토의05f5782/80b7e82에서 정식 head로 넘어온 delta는 pin/test 기대 literal/lock와 문서다. BFF/scope/UI 제품 bytes는 같으며 두 lock 모두 기존 외부 package version/source/artifact 변화·추가·삭제가0이다. 바뀐 Common source pin과 프로젝트 metadata만 의도한 변경이다.

자기 scratch에서 새 HOME/TMPDIR/제한 PATH, 원 conftest 없이 실행했다.

| 직접 실행 | Weather | Transport |
|---|---:|---:|
| 기존 actual BFF Vitest(소유 사본) | 12 PASS | 12 PASS |
| actual 서비스·Common 엔진·소유 SQLite source/sidecar 회귀 | 4 PASS | 4 PASS |
| 본인 TS transpile+합성 전송 반례 | 14 PASS | 14 PASS |
| actual a68 native schema에 GraphQL 문서 parse/validate | 3 PASS | 3 PASS |

본인28조건은 UNKNOWN 후 late commit/retry 순서, retry가 먼저 도착하는 경우, NOT_STARTED 재개, 다른 active 차단, UUID 대소문자·잘못된 ID, 고정 scope/config/tags, guard 해제, terminal replay, unsupported/legacy mutation fail-closed다. 이는 합성 upstream 검사이며 native exactly-once/worker 실행 증거가 아니다.

SQLite8건은 활성/legacy admission, 비활성/좌표 변경/현재 summary 제외, 원천 필드와 과거 sidecar 보존을 actual SQL로 검사했다. Transport의 전체 앱 관리자 API 통합1건은 fixture의 전체 초기화를 피하기 위해 deselect했고 통과로 세지 않는다. 추가 KST 달력 경계2건도 PASS했다. 서비스/공통 sidecar 모듈은 immutable Common270과a68 사이 byte 동일하며 source 반복 실행을 별도 새 성능 증거로 만들지 않았다.

Common 자체의 P1-01/02/SQL timeout 검증은 별도 /home/digitie/dev/common41-independent-review-c-r3-20261018.md에 있다. 거기의 source HOLD를 소비자 PASS로 덮지 않는다.

## 문서/실행 한계와 후속

현재 문서는 이전23e/270 반례를 보존하고 a68의 잠금 연결 장애 보강, 공용 webserver 먼저 배포, 최종 리뷰/CI·운영 수용 미완료를 구분한다. root manifest의 native delay 앱별1 PASS/loopback UI17 PASS는 root가 제공한 metadata이며 본인의 직접 실행으로 합산하지 않는다. 그 시점 full_python/remote_ci=RUNNING·production=NOT_RUN도 유지한다.

리뷰어는 새 live browser/native consumer launch/PostgreSQL 소비자 migration/제공자/운영/SSH/Docker/daemon/worker를 실행하지 않았다. root의 기존 배포·merge 권한을 이 작업 제한으로 변경하지 않는다.

최소 다음 작업은 Common timeout P2의 정확 fix를 재검증하고 소비자 pin 변경만 좁게 대조하는 것이다. 이어 root가 정확 CI/수용 상태를 정산한다. 기존 원천 보존·운영 미검증·daemon OFF를 새 문서 완료로 바꾸지 않는다.

기계 원문: /home/digitie/dev/weather115-transport86-consumer-review-c-r4-20261018.safe.json
SHA256 a73f807ef9802885ccc0d4395e1c7d8647ba66ed65250e959f868956b2a33f92.
직접 실행 증거: /tmp/address-consumer-full-review-c-y8gwoyq7.
사전 원문도 보존: weather115-transport86-consumer-pre-review-c-20261018.md SHA e41e89e0ba13d456e6bd6c7923d74e15e6e080e3da54f8b704285c847795925b.
