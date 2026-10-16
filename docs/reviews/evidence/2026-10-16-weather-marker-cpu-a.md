# Weather104 마커 CPU 독립 FULL5 검토

대상568ae8544ecf7296e430cfbd396c4b1765e321de / 기준10237b3dd58632094af64a180644bde25160eabe807.
SOURCE_PASS / 열린 P0·P1·P2 없음. root 작성자 및 다른 reviewer의 모형·원문을 읽지 않았다.
공개 immutable Git5파일 전체 diff와 repository.marker_values_many/route 분기/특보 판정/
기존 generic-plan·복합키 문맥을 직접 읽었다. 원본 파일은 수정하지 않았다.

## SQL 및 캐시

- 관측 경로는 locations와 metric allowlist를 서로 독립 unnest하여 원래 위치×metric 조합을
  유지한다. fallback만 같은 missing_pairs에서 두 배열을 만들고 PostgreSQL 다중unnest 한 번으로
  zip한다. 배열 길이와 순서가 같아 padding·의도 밖 Cartesian pair를 생성하지 않는다.
- API의500개 상한·중복제거·유효 위치 필터는 그대로이고 repository의 비PostgreSQL fallback도
  변경하지 않는다. query별 list(bind default)를 별도로 만들어 전역 mutable 캐시를 추가하지 않는다.
- ARRAY(String)은 위치/metric 요청값을 typed bind로 보낸다. SQL과 인덱스의 고정 부분
  predicate만 기존 literal_execute를 유지한다. 한도/시각/JSON key는 계속 bind다.
- 후보4개·known_at/source/id 정렬·관측 우선/없는 pair의 forecast fallback·station/sequence 경보
  revision/age·활성 판단·위치별한도·fact 재JOIN의(value_id,known_at)는 byte동일하다.
- 생성 cache key가 같다는 검사에 더해 실제 SQLAlchemy compiled.construct_params에 다음
  CacheKey.bindparams를 전달하여3query의 추출 입력이 새 statement.params와 정확히 같고
  이전 요청 params와 다름을 확인했다. 이전 요청의 위치/개수/시각을 재사용하는 캐시가 아니다.
  실제 Engine/psycopg 서버 prepared cache의 동작·계획 비용은 이 순수 검사의 범위 밖이다.

## API 의미 보존

shared marker mapping을 받으면 한 번 split한 latest/alerts를 같이 사용한다.
별도 latest/alerts mapping이 있는 compatibility 경로는 기존 filtered_alerts 또는 latest_alerts
fallback을 유지한다. 이 변경은 API의 반복 판정을 줄인 것이며 repository 내부 특보판정까지
전체1회가 되었다고 주장하지 않는다. 실제시각 경계의 별도 source/DB snapshot을 추가하지 않는다.

실제 구 route 함수(기준Git AST)와 새 route 함수를 같은 synthetic 객체로 실행해 data DTO를
대조했다. production 공유mapping/별도alerts/빈alerts fallback/같은mapping 재사용/legacy
단일위치repo5경로에서 일치했다. 중복/없는 위치 제외·관측latest와alert·forecast 제외도 유지했다.
별도 관측 후보가 일부 이미 있는 actual method를 실행하여 남은 pair만 정확 zip되는지 확인했다.

## 실행 결과

- immutable archive + production 환경/서로 다른 임의 synthetic admin·metrics token으로
  새 marker_cpu 및 generic_plan의 DB 없는 선택12 PASS/1 deselect.
  원 root conftest는 --confcutdir=tests로 제외했고 psycopg.connect를 금지 대역으로 덮었다.
  token/환경값은 출력하지 않았다. PG fixture를 선택하지 않았다.
- 독립 추가 실제 cache 재결합3·전체 fallback zip1, 기존/새API DTO5·부분 fallback pair1 PASS.
- source5SHA/Python4 compile PASS. 모델r1 및 r3는 원문 SHA로 보존한다.
  최초 r2 DTO fixture는 필수 weather_domain이 없어 검증 단계에서 실패했다. 별도r3에 그
  synthetic 필드를 보강해6조건을 얻었으며 제품 결함/실제PG 실패나 성공으로 세지 않았다.
- SDK·HTTP·PG·SSH·Docker·운영·private/auth runtime 파일·타인 source 변경0.
  생산 설정은 앱 import의 create_schema를 막기 위한 로컬 synthetic 설정이며 실제 운영 설정을
  읽거나 복제하지 않았다. 모든 소유 tool terminal이다.

## 아직 증명하지 않은 것

실제 PostgreSQL custom/generic array bind 계획·동일 데이터 결과·driver adaptation은 root의
별도 시험으로 확인해야 한다. set-returning function의 generic row estimate와 실제 캐시/부하가
달라질 수 있어 기존 partial-index 계획 비용을 그대로 보장하지 않는다. DB/fullsuite/ARM/
운영 UI25초·CPU·전체504 해결은 이 리뷰가 입증하지 않는다. 문서는 이를 후속으로 구분한다.
수집·보존·Common/lock·Docker·프런트·DTO/OpenAPI 변동은 없고 다른 개선을 섞지 않았다.

증거:
- weather104-marker-review-model-a-r1-20261016.py 및 safe.json(캐시/시험)
- weather104-marker-review-model-a-r2-20261016.py(원fixture 실패), r3.py 및 safe.json(DTO/pair)
- source별 SHA와 실행 범위는 같은 review prefix의 safe.json에 고정했다.
