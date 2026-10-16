# Weather marker 후보 hydration 축소 — 분석·구현 인계 D

## 상태

**제품 source 동결·push 완료, 실제 성능/배포 NOT_RUN.** 작성자 인계이며 독립 리뷰 승인으로 세지 않는다.

- WT: /home/digitie/dev/kor-travel-weather-codex-marker-winner-d-20261016
- branch: codex/marker-winner-hydration-20261016
- base: 0acb11fc94d1427f7e920d3feed8a69fac515c21
- HEAD: 049488fb9453e7c7648023b3478da9a48ea4cf7d
- checkpoint: weather-marker-winner-source-d-r1-20261017.checkpoint.json
- checkpoint SHA256: fe8d01dfa8a3a30d95473545e8efbef83dc1dbf0c489c3a2be69a0ead1e52edb
- diff: weather-marker-winner-source-d-r1-20261017.diff
- diff SHA256: e144658655ebd73748ed7c841a7f35721eb16a1c420f20f9111696462151a177

별도 ext4 WT만 편집했다. 원 integration/root 문서/기타 작성자 작업을 수정하지 않았다. remote feature push 완료. 초안 PR은 local integration의 이름 codex/weather-finish-integration-20261016이 원격 branch가 아니라 생성되지 않았다. 임의로 root branch를 push하거나 main 대비 통합 전체 변경을 새 PR에 싣지 않았다. root가 원격 base를 정하면 같은 feature HEAD로 draft 생성 가능하다.

## 실제 근거와 원인 구분

root 전달 R24: 정상 12배치, 692 고유 위치(60×11+32), 최초 취소 포함 693 참조/중복1. 3동시 4waves가 .185/7.52/13.47/19.23초에 시작했고 마지막 정상 본문은 24.66초에 완결했으나 전체 25초 settle은 실패했다. 모든 정상 응답 200, 요청/반환 cardinality 일치. 따라서 frontend 반복 폭증으로 귀속하지 않는다.

근거 safe 파일 weather-r24-marker-timings-cardinality-root-r1-20261016.safe.json SHA256 7cad9a683d2e75f71374803c3725ded5558201b31a95aa49b1579c642d6b21d5. 이번 작업에서 실제 운영 요청·DB 프로파일은 실행하지 않았다. 앞선 CPU3175→2558ms/60위치·170facts 표본 역시 root 실측이며 이번 최적화의 결과가 아니다. 9배치=540위치라는 초기 설명은 R24 12배치/692 고유 위치로 정정한다.

다음 비용은 source로 확정되지만 각 비중이나 Python GIL이 주원인인지는 미확정이다. integration HEAD의 repository/models/alerts/API router/response 다섯 blob은 운영 source b708과 각각 동일함을 확인했다.

1. base repository.py:2666–2678은 위치·metric의 top4 후보를 select(WeatherValueRow) 전체 ORM으로 읽는다. 2681–2695/2745–2757에서 Python 최대값 하나만 남기므로 버릴 후보도 JSON payload·전체 시각/숫자 result processing·ORM hydration을 거친다.
2. base repository.py:980–1006 _value_model은 승자마다 WeatherValue(Pydantic) 생성과 payload=dict 복사를 수행한다. 실제 중간 클래스는 WeatherValue다. API weather.py:335–362 value_out은 payload를 출력하지 않지만 source_record_key·시각·숫자·identity를 그대로 사용한다.
3. **payload 일괄 제거는 불가**: alerts.py:42–87은 station/title/action 등 raw를 실제 사용한다. repository.py:2760–2786의 alert SQL도 JSON station/sequence로 revision을 구분한다. shared generic read와 /resolve 등의 raw 계약도 보존해야 한다.
4. API weather.py:839–856은 async handler 안에서 await 없이 분류·WeatherValueOut·WeatherMarkerOut 생성과 model_dump(mode=json)을 한다. 반환 dict는 response_model=Envelope[list[WeatherMarkerOut]] 검증/직렬화를 거친다. 읽은 로컬 FastAPI routing.py:301–338도 coroutine 응답 검증을 직접 수행한다. 이 로컬 라이브러리가 운영 exact dependency와 동일하다는 attestation은 이번에 하지 않았다. 이러한 동기 구간이 존재한다는 것과 실제 event-loop/GIL 비중은 구분한다.
5. active_alert_values는 identity_key/JSON hash를 여러 정렬·중복제거 단계에서 사용한다. repository와 API에서 경보 재판정도 남아 있으나, release/expiry/custom repository 의미를 조사하지 않고 삭제할 범위가 아니다.

## 구현한 최소 변경

소유 다섯 파일만 commit했다: repository.py, 기존 marker CPU/generic/pair 테스트의 Session double, 신규 test_marker_winner_hydration.py.

- 기존 candidate SQL의 observed 우선·partial literal·정확 missing pair·top4·SQL order를 유지한다.
- 후보 rejoin은 정확 (value_id,known_at)를 그대로 쓰되 SELECT 필드는 location_id/metric_key/known_at/source_record_key/value_id 다섯 개뿐이다. ORM scalars 대신 scalar result row를 받아 **기존과 동일한 Python tuple 비교**로 승자를 고른다.
- 선택된 winner의 ID[]와 aware timestamp[]를 다중 unnest로 zip하여 같은 (value_id,known_at) 쌍만 전체 ORM으로 재조회한다. PK 한 축이나 두 IN의 곱집합으로 확대하지 않는다. VARCHAR[]/TIMESTAMP WITH TIME ZONE[] 및 두 축 JOIN의 실제 SQL compile을 확인했다.
- ORM 반환 순서는 selected의 원 삽입 순서로 복원한다. 기존 _value_model이 모든 raw/lineage/시각/검증을 그대로 수행한다.
- 다음 READ COMMITTED 조회 전에 retention으로 사라진 정확한 승자는 누락시키며 다른 partition/차순위 fact로 대체하지 않는다. 이전 구현이 이미 hydrate한 뒤 삭제된 행을 반환할 수 있었던 것과 이 경계의 관측 차이는 명시한다. transaction snapshot/isolation 보장을 새로 주장하지 않는다.
- generic latest/forecast/timeline read와 _value_model을 포함하여 marker 메서드 바깥 repository 전체가 byte 동일하다. marker의 alert_station부터 기존 return까지도 byte 동일하다. API/DTO/schema/시간제한/메트릭한도/provider/수집/retention 코드는 변경0.

**tradeoff:** selected가 있을 때 winner 전체 조회 한 번이 추가된다. 빈 후보는 이 쿼리를 실행하지 않는다. 감소 목표는 버릴 후보의 전체 ORM/raw 처리이며 SQL round trip 감소가 아니다. winner 쿼리의 실제 generic/custom 계획·partition prune·총 CPU/latency는 아직 검증하지 않았다. 후보 limit1이나 SQL locale 최대값으로 바꾸지 않아, 같은 known_at에서 Python source_record_key/value_id 비교와 DB collation이 다른 경우를 보존한다.

## 검증

- 원 conftest를 로드하지 않고 psycopg.connect를 금지한 순수 회귀 **23 PASS/4 PG 시험 deselect**, 최종 1.04초.
- 신규 회귀를 base source에 실행: **5 FAIL/2 PASS**. 이는 새 hydration 경로/추가 winner 쿼리/새 read-gap 경계를 고정하는 음성이며 이전 API 반환값 5건이 잘못됐다는 뜻이 아니다.
- 별도 동일 fixture 여섯 시나리오의 **전체 WeatherValue+payload JSON digest old==new**: 네 후보/observed+fallback/alerts/all sections/Python tie/동일 UUID 다른 partition.
- top4 중 승자만 _value_model 변환, raw 및 source lineage 보존, observed로 채워진 pair의 fallback 제외, 정확 복합키, 선택 순서, 사라진 winner의 비대체, 특보 raw/reducer, batch 크기와 무관한 cache key/별도 bind를 검증한다.
- 기존 marker partial literal/generic compile/pair/cache/API alert-once 순수 검증 통과. 실제 generic PREPARE 결과 검증 및 기존 실제 PG 3case는 실행하지 않았다.
- 전체 Ruff/diff check PASS, WT clean, push 완료.
- author safe proof weather-marker-winner-author-proof-d-r1-20261017.safe.json SHA256 422a32df48cf6cfac9006d1620aaaedaae1fde89be1ff0305f8802d1146c81bb.
- 최초 자체 특보 fixture가 고정 NOW+1시간에 만료되어 1F/22P였던 로그와 최초 baseline double가 이전 ORM 경로를 지원하지 않아 7F였던 로그도 보존했다. fixture만 미래 expiry로 정정하고 baseline 지원 double로 다시 검증했다. source 오류/운영 장애로 분류하지 않는다.

## API 쪽 제안 — 이번 코딩0

다음 작업은 root 수용 후에만 별도 범위로 수행한다.

1. 임시 동일 대표 배치로 SQL execute/fetch-result+ORM/WeatherValue 생성/API DTO 구성/최종 response-model 직렬화의 CPU·wall을 나누어 확인한다. HTTP 전체 CPU만으로 GIL을 확정하지 않는다.
2. API에서 validated WeatherMarkerOut 목록과 typed Envelope를 한 번 만들고 FastAPI response_model에 같은 모델로 반환하는 후보는 model_dump→dict→response validation 중복을 줄일 수 있다. 반환 DTO/extra-forbid/숫자·시각 JSON·identity·정렬·오류 응답 동등 회귀가 선행되어야 한다. JSONResponse로 계약 검증을 우회하거나 model_construct로 shared WeatherValue 검증을 끄지 않는다.
3. DTO 구성의 sync 구간을 threadpool로 이동하는 것은 loop 응답성 격리 후보이며 CPU/GIL 절감 자체의 증거가 아니다. 실제 phase 측정 없이 이것만 먼저 도입하지 않는다.
4. alerts의 payload 생략 또는 공통 identity 계산 정책 변경은 이번 최소안에 넣지 않는다.

남은 것: 두 독립 source 리뷰, root의 bounded 실제 PostgreSQL winner-query 계획/동일 raw·DTO digest/대표 60배치 CPU와 12배치 live25초 gate. 기존 예산·batch·동시성·DTO를 완화하지 않는다. 운영 SSH/Docker/DB/SDK/provider·빌드/배포 실행0, 다른 작업자 수정0, 도구 모두 terminal.
