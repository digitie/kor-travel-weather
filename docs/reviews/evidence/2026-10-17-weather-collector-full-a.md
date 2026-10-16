# Weather PR87 독립 제품 적대리뷰 A

## 판정 / 정확 범위

**SOURCE_PASS — 열린 P0/P1/P2 = 0/0/0.** 운영 controller 리뷰와 별개인 PR 제품 전체 독립 리뷰다. 이 역할의 기존 작성 소유는 PR86/BFF 등이며 PR87 제품을 작성하지 않았다. 다른 reviewer 원문/모형은 읽지 않았다.

- PR: https://github.com/digitie/kor-travel-weather/pull/87
- head: acc8c71fb2dfb60f10654403081b7b4b21d75f62
- base: f60d2c21a98973aca62e5623b26488430c850514
- 3 commits: 18a786b 산악 지표, 0adb97f regional chunk, acc8c71 anchor shared lock.
- FULL diff: **제품 3파일 + 테스트 2파일 + 문서 1파일**. 마지막 commit의 2파일만 읽은 검토가 아니다.
- diff SHA256: aa7a648237f9296c2c361131b8247be1cf3575d570848c619cd5ae768888dd74
- 공개 GH 현재 OPEN/draft, exact head 일치. python/frontend 각각 SUCCESS: https://github.com/digitie/kor-travel-weather/actions/runs/38713660217
- 운영/private/auth/SSH/DB/Docker/SDK/provider/HTTP 실행 0. 원 WT/source 변경 0. Git 객체와 공개 소스만 읽었고 새 모형/보고서만 저장했다.

AGENTS.md를 exact head에서 확인했다. Weather의 원천 raw/lineage, provider public boundary, timezone/unique-upsert, admin fail-closed 규칙을 기준으로 읽었다. 리뷰 전용으로 전체 DB suite를 재실행하지 않았으며 기존 테스트/작성자 수치는 독립 실행 수로 합산하지 않았다.

## 제품·호출 문맥 검토

### 산악 누락

src/kortravelweather/providers/krforest.py:205–217의 최신 mark 선택은 temperature_2m 한 필드 대신 정규화가 이미 사용하는 _METRIC_FIELDS 12개 중 하나라도 None이 아닌지 검사한다. 풍속·습도·10m 기온·강수 등만 있는 정상 관측을 과거 mark로 버리던 경로를 보강하고, 0은 유효하다. converter는 여전히 None을 제외하고 metric/height/source key/raw mapping을 유지한다. 좌표 없는 관측소를 임의 (0,0)에 저장하지 않는다.

one-mark fetch/pagination, provider public client, 시간 mark/12회 lookback, page_size/max_records, HTTP client timeout·async context 종료는 변경되지 않았다. HTTP timeout은 개별 client 설정이며 전체 run deadline으로 승격하지 않았다. 모든 지표가 None인 응답은 유한 lookback 후 빈 결과이며 실제 데이터 수집 성공이라고 주장하지 않는다.

### Regional streaming / 부분 실패

packages/kor-travel-weather-dagster/src/kortravelweather_dagster/regional_sources.py:119–235 전체 publish와 각 KHOA/KRForest/dust/KREX 호출자를 읽었다.

- 원천 응답 목록은 여전히 resident다. 제한하는 것은 정규화된 sources/values의 현재 chunk와 다음 관측 하나이며 운영 RSS 전체가 아니다.
- 50 distinct locations/5000 values 경계와 전체 max_values를 유지한다. 한 관측의 metric set은 쪼개지 않으므로 단일 관측이 chunk cap보다 크면 그 관측 단위로 초과할 수 있다. 현재 산악 12지표 등 실제 converter는 작고 유한하다.
- 전체 budget은 flush 뒤 비워지는 len(values)가 아니라 value_count로 검사한다. 관측 중간 절단, cache clear 후 전체 budget 재시작, produced_nothing을 flush 후 빈 list로 오인하는 경로를 막는다.
- 같은 위치가 떨어져 재등장하면 이전처럼 전체 위치별 재정렬하지 않고 다른 transaction으로 게시할 수 있다. 각 source와 해당 metric은 함께 전달되고 raw/명시 source key/run association은 유지된다.
- repository.ingest_batch는 동기 transaction 반환 뒤 목록을 비운다. alias·unique/upsert·payload 충돌·projection CAS는 변경하지 않았다.
- 각 chunk는 원 run_id를 갖고 repository가 run 행 lock/status=running을 검사한다. 늦은 변환/게시 실패는 앞 chunk를 되돌리지 않으며 finish_sync_run(status=failed, values_loaded=None)이 저장량을 보존한다. 마지막 publish_and_finish([],[],values_loaded_offset=published)의 ownership CAS를 유지한다.
- no-coordinate/disabled/empty observation은 source만 덧붙여 가짜 fact lineage로 만들지 않는다. produced_nothing은 생성값 0 여부이며 replay로 새 insert가 0인 정상 응답과 구별한다.

KREX provider 구현·공개 client lifecycle·block 판별, chunk helper, Dagster asset/job/schedule 선언은 base→head bytes 동일하다. KREX async context와 fetch를 같은 asyncio.run 안에서 사용/종료하며 upstream block은 여전히 실패로 전달한다. provider disable이 credential 요구 전에 작동하는 기존 gate도 그대로다. PR87은 KREX 인증/차단/원천 누락 전부를 해결하는 패치가 아니다. regional job의 별도 asset step을 통합하거나 다른 provider 실패를 성공으로 바꾸지 않았다.

### Shared anchor lock / 동시성

repository.py:1000–1070의 네 잠금 helper와 catalog 4경로, _resolve_fact_session, _ingest_batch_session, ingest_batch/skip_locked, retry/finish 문맥을 읽었다.

- 게시자: pg_advisory_xact_lock_shared + 실제 SQLAlchemy FOR KEY SHARE. catalog create/upsert/patch/grid: 기본 exclusive + FOR UPDATE 유지.
- held cache는 scope→mode와 transaction 객체 identity를 함께 보관한다. shared 획득으로 exclusive 승격을 건너뛰지 않고, exclusive는 shared 요청을 만족시킨다. 실패한 try advisory 또는 advisory SQL은 새 mode를 확정하지 않는다. advisory 획득 뒤 row-lock SQL이 실패하면 transaction을 rollback해야 하며, 기존 ingest retry가 rollback 후 새 transaction에서 수행한다.
- batch 위치 정렬, run ownership lock, 원천별 exclusive lock, fact/projection unique/CAS, FK key 삭제 보호는 유지한다. 동일 source/projection 경합까지 제거했다고 해석하지 않는다.
- row KEY SHARE는 advisory 규칙을 우회한 임의 SQL의 non-key UPDATE를 모두 차단하는 계약이 아니다. 앱 catalog 경로는 exclusive advisory를 따르며 문서도 이 한계를 명시한다.
- INGEST_LOCK_ATTEMPTS=20 / RETRY_SECONDS=3.0, lock timeout·rollback 후 retry 조건은 원본과 동일하다. 전체 hard deadline이나 timeout/재시도 감소를 새 효과로 주장하지 않는다. hash 충돌/교착 가능성을 0으로 만들었다고 하지 않는다.

## 최신 582 통합 보존

최신 제품 58230797b1b3c932537b28fc8ca5139e8075c49e와 직접 대조했다.

- krforest.py 및 regional_sources.py는 PR87 head와 **파일 전체 SHA 동일**.
- repository.py 전체는 후속 retention/marker 등으로 달라졌으나 네 잠금 helper, retry/timeout helper, catalog 4메서드의 AST 10개는 동일.
- _resolve_fact_session와 _ingest_batch_session의 모든 location lock 호출에서 shared=True가 head/582 모두 유지됨을 검사했다.
- 이 보존 판정은 후속 repository 전체를 PR87 리뷰로 승인하는 뜻이 아니다. 후속 독립 리뷰/통합 suite는 별도다.

## 실제 독립 실행

실행: /home/digitie/dev/weather87-product-review-a-r1-20261017/independent.py / terminal EXIT0.

**15개 묶음 PASS**:
1. base/head 실제 publish 함수 AST로 seeded 50개 입력쌍: 중복 위치/0~3 metrics/좌표 누락/전체 budget의 facts와 결과 필드 동등.
2. 반복 위치·chunk 경계·source와 metrics pairing.
3. 단일 관측 초과 cap의 원자성·전체 max budget 절단.
4. disabled/no-coordinate/empty가 새 데이터로 오인되지 않음.
5. 늦은 normalize 실패에서 앞 commit/failed 저장량 유지.
6. 다음 ingest 실패에서 앞 commit 유지·함수 자체 retry 없음.
7. 합성 sink의 replay 중복 0 및 produced와 inserted 구분.
8. 실제 산악 fetch 함수에서 12개 지원 지표 각각 0값 latest 채택.
9. all-None은 12회에서 종료·빈 결과.
10. transport 예외 전파·추가 retry 없음·client close.
11. upstream 초과 반환을 max_records로 절단.
12. 실제 SQLAlchemy PostgreSQL compile: shared KEY SHARE/exclusive FOR UPDATE 및 cache 승격.
13. transaction identity 변경 시 cache reset.
14. 실패 try lock은 획득/승격으로 오인하지 않음.
15. advisory SQL 실패는 held mode 확정하지 않음.

SDK/Weather module import 없이 AST를 추출했다. SQLAlchemy는 SQL compile만 사용했고 connection/DB는 없다. 합성 sink의 저장·replay 모형은 **실제 PG unique/upsert/경합 증명으로 세지 않는다**. 기존 tests/test_ingest_locking.py의 PG thread·pg_locks 검증과 regional 실제 PG 테스트는 전체 source를 읽었지만 재실행하지 않았다. 작성자 benchmark/CI는 별도 provenance이며 본인 15묶음에 합산하지 않았다.

## 열린 운영 gate / 한계

새 source blocker 없음. 이 원문은 PR87 독립 제품 리뷰 1건이며 merge 또는 운영 검증 2건을 대신하지 않는다. 최신 KREX/KRForest 실제 원천 수집→저장 성공, 차단/자격/좌표 상태, PG 다중 publisher 동시성·처리량, 운영 RSS/CPU/실패율 개선은 미실측이다. 오래된 KREX20/forest0 같은 수치를 최신 성공·실패 판정으로 재사용하지 않았다. 기존 controller 리뷰는 제품 FULL 증거에 합산하지 않았다.

## 고정 파일 SHA256

| PR87 파일 | SHA256 |
| --- | --- |
| regional_sources.py | 02bd6641d5c1f347e02c466904b15fb7ac4dafd44b24a1e19924a055cdf53465 |
| providers/krforest.py | 414b332245575c0f00b5b96d8a69e5e2f1475cabaa1d53ca1509395b2df5873e |
| repository.py | 8de6e9bddd37a693cb9b76ddca8e3cc42e60a718d6a4d3de98183822b08855a5 |
| tests/test_ingest_locking.py | 09d345127cd9511acd688c13937955350e1d18bffd8a5aff725b65a22e610ae8 |
| tests/test_regional_sources.py | 20c55797f01b0f802de6db1d8a2220552b97d66246b7e5b16632e22d997be032 |
| docs/reviews/2026-10-15-weather-collector-stability.md | b5d59ef9831d8d32c6cfafd7e8b777b3d883d9660f52355e70e9f1264b8f12f1 |

safejson: weather87-product-review-a-r1-20261017.safe.json
SHA256 f298bd4d0ccabfd49525692ee880ae6932aee2394c1d0a84c10e3c107fc4eff8.
