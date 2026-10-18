# Weather113 독립 Popper/Backend·SQL·DB·health/packaging·ops FULL 리뷰 r1

판정: **PASS — WEATHER_SOURCE_AND_OWN_FIXTURE_ONLY**.
OPEN P0=0 / P1=0 / P2=0. 새 차단 결함을 발견하지 못했다. 이는 운영 배포/실제 제공자/PostgreSQL 경합/CPU·RSS 수용 판정이 아니다. Transport155 및 다른 소비자의 formal gate를 닫지 않는다.

## 관찰한 불변 대상과 격리

- manifest: `/home/digitie/dev/reverse-address-weather-summary-review-manifest-20261018-r1.json`
- 실제 읽은 manifest SHA256: `9215b8d655e363579c004e8f2c9981ca31c68b51e56cfc0a253d440c1f9d8ac1` (요청과 일치)
- Weather base: `36c11950e1f5b6476f2c9b15396a097300a0e1e5`; tree `8d5014030a338e9eda2b5cd5474440c305659ffd`.
- Weather head: `2bd77942a21c5b449347963488a38814d55f0990`; tree `2d96106a190ddef454b5f4527e867de7091abc06`.
- Common pin: `522525aed3db934767c1c9e742b3bfc68e91679c`, Common40 merge `27cd146ed9e83d3ef481757d2b48e16f232dc88c`는 manifest 귀속.
- repo: `/home/digitie/dev/kor-travel-weather-codex-reverse-address-20261018`.
- `git show`와 immutable tree/diff만 사용했다. HEAD/branch/index/worktree를 수정하지 않았다. 해당 head의 AGENTS.md 전체를 읽었다. nested AGENTS/CLAUDE는 tree에 없었다.
- WSL Ubuntu-26.04, 자신 소유 TemporaryDirectory와 SQLite만 사용. read-only 기존 venv interpreter에 isolated `-I -B`와 명시적 소유 source 경로, sterile HOME/TMPDIR/PATH를 사용했다. pytest plugin 자동 로드를 끄고 `--noconftest -c /dev/null -p no:cacheprovider`로 저장소 PG conftest를 차단했다. settings는 `_env_file=None`, resolver는 주입 fixture였다.
- 실제 환경변수/secret/private 파일·production·provider·SSH·Docker·Daemon·SDK 실행·실브라우저 접근 없음. 새 상대 Weather113 보고서/모형을 읽거나 요청하지 않았다.
- manifest created_utc 2026-10-18T03:48:03.877577+00:00는 작성자 시각으로, 리뷰 시작 시각이 아니다. 본인 검토의 최초 시작 UTC는 별도 보존하지 못했다. 실제 후반 불변/pin 검증 관찰 UTC는 03:52:05.644910 및 03:52:18.857857이다. 종료 UTC는 문서 끝에 기록한다.

## 전체 delta 및 연결 계약

base→head 8파일 70 additions/9 deletions 전체를 읽었다. 변경은 root 및 Dagster package Common pin, lock, health assertion 상수, actual Weather 모델 회귀와 docs 3개다. 제품 Weather Python/DTO/OpenAPI/TS/schema/migration/Compose/배포/Frontend 소스는 변경되지 않았다.

1. `src/kortravelweather/address_refresh.py:14–24`의 실제 원천은 WeatherLocationRow PK와 현재 longitude/latitude/name이며 enabled IS TRUE다. PK이므로 source join은 target별 유일하다. `:31–36`은 source.summary를 공개하고, `:51–58`은 page/retries/pending_failures 필수 callback을 정확히 전달한다. `:60–62`의 owned resolver close도 보존됐다.
2. `src/kortravelweather/repository.py:148–183`의 longitude/latitude는 Numeric(9,6), enabled는 NOT NULL이다. nullable은 nx/ny/region_code 등이다. 실제 모델을 nullable 좌표 허용으로 오해하지 않았다. 원천/격자/admin metadata/fact를 쓰는 새로운 경로가 없다.
3. Common exact5225의 `reverse_address.py:551–556` MAX는 source 현재 SELECT와 provider/target join 후 동일 좌표 조건을 적용한다. `_joined:439–447`가 provider와 target을 함께 결박한다. 전체 provider MAX였던 과거 확인 시각 누출을 소비자의 실제 모델에서 다시 공격했다. NULL 비교는 SQL unknown으로 제외하며 source PK 때문에 MAX cardinality 증폭 문제는 없다.
4. root pyproject, Dagster package pyproject, uv.lock, health test를 **5225→c067 역치환하면 각각 base bytes와 동일**함을 직접 확인했다. SDK/optional extra/lock 다른 dependency/health Test·interval·timeout·start/retries·run monitoring/retries를 함께 바꾸거나 assert를 약화하지 않았다.
5. Common c067→5225의 실제 grpc_health.py 및 해당 tests는 byte/hash 동일하다. health source SHA256 `2aa9cccee16c65d04590c4c4ef2c2c7404ea7c27ef932df731bb84a7398a8f15`. 따라서 새 pin만으로 health 구현/프로토콜을 변경하지 않는다.
6. docs/reverse-address-refresh.md:82–87은 current-source/same-coordinates MAX와 same-point failed last-good 보존을 정확히 설명한다. :57–60의 단일 Node 프로세스 제출 경계, replica 전역 exactly-once 미보장, Admin/API 파생 결과만 표시하며 일반 source 응답을 치환하지 않는 한계가 유지된다. :20 및 운영 NOT_RUN 기록도 유지된다. 기존111/112/이전 검증과 새 pin 수용을 구분한다.

## 본인이 직접 수행한 반증 검사

### A. 실제 Weather 모델 + exact Common5225: 11 PASS / 1.11초

exact Weather src와 exact Common src를 소유 temp에 물질화했다. 별도 old c067 모듈도 같은 소유 fixture에서 불러왔다. 제품 source 파일/테스트 파일을 수정하지 않았다. 7개 자체 실행 case + exact 제품 test_address_refresh.py 4개가 통과했다.

- **4가지 old→new 반례**: 현재 정상 주소를 한 번 저장한 뒤 enabled=false, 원천 삭제, longitude+0.000001, latitude+0.000001 각각 적용. old c067 summary는 과거 last_resolved_at을 반환함을 assert하고, new5225는 None/items=[]임을 assert했다. 과거 파생 행은 삭제/덮어쓰기되지 않았다. pytest를 실패시키고 통과로 감춘 것이 아니라, 양쪽 실제 동작을 명시 assertion으로 비교했다.
- **Decimal·nullable·쓰기 금지**: 실제 Numeric(9,6) 좌표 127.123456/37.123456과 null nx/ny/region_code를 사용. 현재 정상 행은 summary/sample에 남는다. summary SQL을 포착해 SELECT만 발생함을 검사했다. enabled/longitude/latitude에 NULL 쓰기는 각각 실제 SQLite NOT NULL 위반으로 거절되고 rollback되었다. source/admin metadata와 entry/control 전체 snapshot은 그대로였다.
- **현재 동일 좌표 실패 + provider/orphan 격리**: 정상 확인 후 200일 지난 실패를 주입해 failed 상태에서도 최초 정상 resolved_at/주소가 남는지 검사. 더 미래 날짜의 orphan entry와 다른 provider의 동일 key를 만들어 현재 MAX에 섞이지 않음을 검사했다. summary 전후 source/entry/control snapshot 동일.
- **실제 sensor pending 연결**: SDK를 import하지 않고 exact Dagster adapter의 _pending 함수 AST와 actual DatabaseAddressSource를 실행했다. AUTO=false에서 진행 중 manual error는 backoff0/119초에는 pending=false,120초에는 true, 정상 재개 후 remaining=0/pending=false였다. 불필요 backoff-only sensor run과 manual 실패 영구 중단 회귀가 없었다.
- exact 제품 모델 회귀 4개도 같은 sterile 실행에서 통과했다. 이 중 새 parametrized 3개는 비활성/longitude/latitude 변경이다.

### B. exact pin/health metadata 계약 2 PASS

exact `test_common_health_is_pinned_installed_and_scoped_to_dagster_extra` 및 `test_code_server_health_is_liveness_without_changing_monitoring` AST를 소유 fixture로 가져와 실제 TOML/lock/Compose/instance와 read-only 설치된 Common direct_url.json metadata를 검사했다. 테스트 본문의 assert를 변경하지 않았다. 새 pin root/API·Dagster extras·lock·설치된 commit이 일치하며 optional grpc dependency 3개, health Test/90s/10s/5/30s와 monitoring/retry 설정이 통과했다. grpc server/socket/SDK import는 하지 않았다.

### C. static 불변 검사

- `git diff --check base head`: exit0.
- 모든 changed path를 전수 열거하고 전체 diff를 읽음.
- source/Schema/API/Frontend 불변은 changed-name set와 연결 파일 byte 비교로 확인했다.
- old pin 역치환 4파일이 base와 byte-identical.
- reviewer 실행 실패 없음(의도한 old 동작/NOT NULL 예외는 테스트 assertion에 포함). 도구 orchestration 첫 hex 생성에서 V8 TextEncoder 미제공 오류 1회는 명령 실행 전에 발생했으며 percent-encoding 방식으로 바로 정정했다. 제품/pytest 실패가 아니다.

## 작성자 증거와 본인 검사의 분리

공개 evidence SHA256를 직접 확인했다:
`31892dbe8c2348052e902438840c5c4028e94fd4ff2e24172015e34579074d1a`.

그 파일과 manifest의 root 귀속 증거는 CI39094583346 Python661/213.48s, isolated PG661/205.07s, Ruff, 집중11, fresh API/native fixture 및 **변경 없는 compiled UI** 13 PASS다. 본인이 이를 재실행하거나 운영을 검증한 것으로 세지 않는다. 초기 Weather Dagster manifest old-pin 1 FAIL과 수정 후 focused11 PASS 이력도 evidence에 남아 있으며, 최종 불변 대상의 pin 전집을 별도로 검증했다. UI sourcebindings의 component/BFF/spec hash와 이전111 최종 source 흐름을 읽되 Transport 증거를 이 Weather gate로 대체하지 않았다.

Common40에 대한 기존 본인 FULL source 리뷰 PASS는 연결 근거일 뿐, 이번 소비자 검사를 대체하지 않았다. Transport155 current CI/formal review는 이 보고서 범위 밖이다. 전체 Track1/2 완료나 운영 주소 수용 완료를 주장하지 않는다.

## 판정 한계 / NOT_RUN

- 실제 PostgreSQL 엔진/동시 transaction·lock/lease 경합은 이번 독립 리뷰에서 실행하지 않았다. SQLite 모델 검증과 SQL source 읽기는 PostgreSQL 실측 PASS가 아니다. 작성자 PG 결과는 별도로 귀속한다.
- provider HTTP·quota·latency·실키·운영 migration/배포·Daemon ON·native health socket·runtime UI·pool/RSS/CPU/복수 replica exactly-once는 NOT_RUN.
- 기존 c067 엔진의 경합/lease/달력6개월/커서wrap 계약은 Common40의 최소 SQL aggregate delta 외에는 동일하며 이번 newpin에서 확장/완화되지 않았다. 주입 Clock과 SQLite로 재개 연결을 확인했지만 모든 운영 interleaving을 증명한 것은 아니다.
- summary의 여러 SELECT는 하나의 직렬화된 publication snapshot을 보장하지 않는다. 이것은 기존 계약이며 source mutation과의 모든 동시 read 시점 일치를 새로 보장한다고 문서/판정하지 않는다.
- 새 schema/DTO/배포 파일 변경은 없으므로 새 운영 실행을 본인 리뷰의 추가 승인요건으로 만들지 않는다.

## 불변 changed blob SHA256

| 파일 | SHA256 |
| --- | --- |
| docs/journal.md | 75f1c76d19d1edaa79ccadf14ec519643d46d2ad303785093c3aefa029d16476 |
| docs/resume.md | 404061bd15547428e0bf8b073c7609f703562b247e00d19f2a5d12ae698c79a4 |
| docs/reverse-address-refresh.md | 939376d04b6b69e3a3868657f54ef4a8ec84bbc4754839b8aad86fb8f59fa7c2 |
| packages/kor-travel-weather-dagster/pyproject.toml | 4fa52a57897f11de4a32cab992e46f30b3f400adb4f6fb8ffe58a7b77df53081 |
| pyproject.toml | 48bdaca8069e39b2f4029a02bef5ddaa81186ad33dd12e4406ac6f6ffc5733ca |
| tests/test_address_refresh.py | 4634885db50c5a7d0468ba75db241866a029972364f5ec3a628126707763b918 |
| tests/test_common_health_adoption.py | c98d4fb67b00fc70fbe72118a903e8a775f9eb6075045b2b4feeb285c20fdf65 |
| uv.lock | 3ffad154e97446e5a3ae8717f6fb31bfc94692779ccdf2fe0cdb5a52ca40be73 |

## 실제 전달된 요청 원문

아래는 root가 이번 실행에 전달한 원문이며 요약 복원이 아니다.

```text
Weather113 새 pin 통합 FULL 독립 B/Backend·SQL·DB·health/packaging·ops 렌즈 리뷰입니다. 결함발견목적, 애매하면지적. 같은 manifest /home/digitie/dev/reverse-address-weather-summary-review-manifest-20261018-r1.json SHA256 9215b8d655e363579c004e8f2c9981ca31c68b51e56cfc0a253d440c1f9d8ac1. base36c11950e1f5b6476f2c9b15396a097300a0e1e5 → head2bd77942a21c5b449347963488a38814d55f0990, WT /home/digitie/dev/kor-travel-weather-codex-reverse-address-20261018. Common40 제품522525a 두FULL SOURCE PASS/CI8 후 merge27cd146. 이PR은 root/API·Dagsterpackage·lock/healthpin assertion·실제Weather모델3반례·문서8파일만 변경. source SELECT enabled/nullable/decimalcoords/currentlastgood·비활성/이동 이전시각제외·source/KMA격자/adminmetadata/sidecar불변, pin락/SDK/healthhash/DTO/schema계약 및 새 회귀를 whole delta에서 공격하세요. exactCI39094583346 Python661PASS/213.48s+lint/migration/OpenAPI/Admin211/type/build SUCCESS, rootownPG661/205.07s/Ruffall/집중11, freshAPI/native+unchangedUI13PASS. evidence /home/digitie/dev/reverse-address-source-summary-consumer-verification-20261018.json SHA31892dbe8c2348052e902438840c5c4028e94fd4ff2e24172015e34579074d1a에 sourcebindings/초기pin정렬실패/실행한계포함. Transport155 CI는진행중이며이Weather검토로닫지않습니다. immutableGit source/자기소유fixture만read-only, source/index/branch 편집·env/secret/production/provider/SSH/Docker/daemon 금지. 다른작업자변경되돌림금지. 새상대Weather113원문은둘다확정전읽거나요청금지. 기존확정Common/Weather111history참고가능. 실제전달요청전문·manifest/commit관찰hash·격리·시각·실행/실패/NOT_RUN한계·file:line/재현/P0-P2와 PASS/CONDITIONAL/BLOCK 및 OPEN수량을 /home/digitie/dev/reverse-address-weather-summary-review-popper-r1.md 에저장후SHA보고. 운영미검증을새승인요건으로확대하지마세요.
```

검토 종료 UTC: 2026-10-18T03:53:58.167341+00:00
