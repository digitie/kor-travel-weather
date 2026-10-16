# Weather87 제품 독립 FULL 검토 D

판정: **SOURCE_PASS / OPEN P0·P1·P2 0**. 이 문서는 운영 controller 리뷰나 앞선 merge-binding 감사를 제품 리뷰로 전용하지 않고 새로 수행한 독립 제품 검토다.

정확 HEAD: acc8c71fb2dfb60f10654403081b7b4b21d75f62
전체 기준: f60d2c21a98973aca62e5623b26488430c850514(PR85 main)
직전 parent: 0adb97f3100988c6435a4800bffb826201f933c1
기능3commit: 18a786b(산악 gate) → 0adb97f(streaming) → acc8c71(shared locks)
전체6파일 diff SHA256: aa7a648237f9296c2c361131b8247be1cf3575d570848c619cd5ae768888dd74

D는 #87 제품 작성자가 아니다. 당시 제어기 source 검토 참여는 제품 작성/검토와 별개다. 이번 제품6파일 전체 diff·산악 adapter/지역 publication 전체·이전 chunk helper·repository 잠금/lineage/게시/부분실패 caller 및 관련 기존·신규 시험을 읽었다. 대상 AGENTS.md도 Git blob에서 읽었다. 새 A 제품 리뷰 원문/모형을 읽지 않았다. 기존 ops 리뷰와 GH·장부는 범위 구분에만 사용했다. immutable Git object만 사용했으며 타 WT·원본 소스 변경0이다.

## 산악 최신 시각 선택

유효 metric의 정본은 기존 _METRIC_FIELDS 12개다. TEMP 하나만 확인하던 조건을 해당 정본 전체의 None 여부로 바꿔 최신 유효 관측을 유지한다. 0은 유효하며 전부 None이면 기존10분 간격/최대12표본 walk-back을 따른다. 새 HTTP 재시도·pacing·provider API·관측 시각 규칙을 구현하지 않는다. 정규화가 같은 필드를 사용하므로 선택 gate와 게시 가능한 metric 집합이 일치한다.

실제 _fetch_mountain_weather/_fetch_one_mark AST를 가짜 async provider에 실행했다.12개 metric 각각0값인 최신 관측을 선택하며 allNone/empty walk-back·12회 상한·page별2/2/1개 합계5개 제한을 확인했다. 구 f60은 TEMP 이외11개에서 최신 관측을 놓치고 더 오래된 온도 관측을 선택했으며, 새 source는12개 모두 최신을 유지한다. provider 네트워크 호출은0이다.

## 정규화 staging과 publication

source와 해당 관측의 metric set을 만든 뒤 전체 max_values를 누적 value_count로 검사한다. chunk flush가 전체 budget을 초기화하지 않는다. source list·values·location set은 성공 ingest가 반환한 뒤 비우며 pending chunk와 다음 관측 하나만 정규화 상태로 보관한다. 한 관측은 쪼개지 않으므로 단일 관측이 chunk 상한을 넘을 수 있다. 원본 응답 records 자체는 여전히 전체를 보유하며 이를 전체 프로세스 RSS 감소 보장으로 해석하지 않는다.

same-location 관측이 입력에서 떨어져 있으면 이전 whole-location regrouping과 달리 다른 transaction이 될 수 있다. 변경된 원자성 단위는 하나의 원천 관측/그 metric set이다. 전역 max_values가 자르는 입력 위치와 source/fact identity 및 전체 입력 순서는 유지된다. 모든 source는 인용 fact와 함께 ingest하므로 마지막 publish_and_finish는 빈 묶음과 이미 실제 게시한 offset만 전달한다. source_count/value_count는 flush 후에도 총계를 보존한다. disabled·좌표 없음·metric없음은 게시하지 않는다.

실제 old/new publication AST에 transactional fake repository를 연결한 고정seed120개 입력에서 반환 통계·최종 source/raw·fact 내용이 같고 새 각 commit의 fact/source lineage가 온전히 대응했다(각각 비교120+lineage120). globalbudget, 관측단위 oversize, interleaved duplicate의 신규 loaded만 집계, 늦은 normalizer/ingest 실패·lease소실 뒤 과거 chunk 보존도 별도로 확인했다. 가짜 repository는 실제 PG 잠금/DDL을 실행하지 않는다.

첫 게시 시점의 독립 OLD/NEW 모형: 6개 관측/1location chunk에서 원본은6개 모두 정규화한 뒤 게시, 새 source는2개 정규화 시 첫 게시이며 총 loaded6은 같다. 작성자 실제 PG 부분실패/replay 회귀와 문서의 2000관측 tracemalloc 실측은 읽었지만 본인 새 실행 수로 합산하지 않았다.

## shared anchor와 배타 catalog 계약

새 location advisory lock은 publication에서 shared, catalog upsert/patch/grid/create는 기존 default exclusive다. source-key advisory도 exclusive 그대로다. 실제 PostgreSQL dialect로 생성된 row SQL은 shared일 때 FOR KEY SHARE, catalog일 때 FOR UPDATE였다. app catalog의 advisory exclusive는 publisher와 배타성을 유지하고 FK key 삭제 보호는 row KEY SHARE가 담당한다. raw SQL로 이 advisory 규칙을 우회하는 외부 writer까지 같은 계약을 보장하지 않는 문서 한계는 맞다.

실제 네 helper AST를 fake session과 실제 SQLAlchemy statement 생성에 실행했다.
- shared 반복은 cache로 생략하되 shared→exclusive는 실제 exclusive advisory와 FOR UPDATE를 다시 요구한다.
- exclusive 보유는 shared를 만족하고 다른 transaction object에서는 cache가 초기화된다.
- try-shared 실패는 cache에 기록하지 않으며 row lock을 실행하지 않는다.
- try-shared 성공 뒤 try-exclusive 승격도 새 advisory/row lock을 요구한다.
- catalog4 public 경로와 fact source-key 호출의 default exclusive를 AST로 확인했다.
- 기존 ingest의 정렬된 location 잠금 순서, source immutability/alias/FK/unique/rollback·유한 lock retry·projection 같은 transaction과 run row 누적 저장량 계약은 이 diff가 바꾸지 않는다.

실제 PostgreSQL의 동시 publisher/catalog 대기/FK delete 차단을 이번에 재실행하지 않았다. 신규 PG 시험의 thread·listener·실제 pg_locks 관측 및 finally release/join을 소스에서 검토했다. 선언된 lock 종류·cache 행태와 실제 DB 동시성 실증은 분리한다.

## 결과 및 현 통합 보존

독립 actual-function/AST 최종 **276조건 PASS**:
- weather87-product-review-d-r1-20261017.model.py
- weather87-product-review-d-r1-20261017.safe.json
- safe SHA256 ebeebbd8f51a64d33fcefe2ee5beb82d1e7adea6be938f5be600260544a802e5

추가 old negative: 12 metric 비교의 OLD11FAIL/1기존PASS → NEW12PASS 및 첫 게시 시점6→2. 이것을 276건에 재합산하지 않는다.
- weather87-product-review-d-r1-20261017.old-negative.safe.json
- SHA256 5613aa137849931c18871996fb139bd9c1c806130a5b016994cd4206b363aead

현 통합58230797b1b3c932537b28fc8ca5139e8075c49e와도 직접 AST를 비교했다. _fetch_mountain_weather/_fetch_one_mark/publish_regional_records 및 잠금 네 helper 총7개가 acc와 exact다. 이후 repository의 broader fact/projection/replay/retention 변경은 기존89/95/96/103/104/106의 별도 검토 영역이며 이7함수와 publication shared 호출을 보존한다.
- weather87-product-review-d-r1-20261017.integration.safe.json
- SHA256 167d665e9365f5d83b0373bd59b3555e89e05688771d90a7a421a9cee3481dfc

## 한계와 실행0

실제 PG/기본 weather_test·원본 conftest/SDK remote/HTTP/provider/SSH/Docker/운영/private/인증 접근 0. 설치된 interpreter에서 SQLAlchemy import·statement compile만 했고 DB engine/connection을 만들지 않았다. 원래 test 파일은 읽기·compile만 하여 default test DB TRUNCATE를 실행하지 않았다. 전체 pytest·frontend·성능 시험은 중복 실행하지 않았고 새 제품 검토는 소스 및 격리 모형 범위다. 과거472/full 또는 root 통합628/R25는 본인의 실제 시험 결과가 아니다.

이 검토로 운영 job 성공률·API/UI 성능·RSS·CPU 개선을 확정하지 않는다. 소스 gate 관점에서 새 blocker는 없으며, 모든 도구 terminal이고 다른 작업자의 소스/증거를 수정하거나 되돌리지 않았다.
