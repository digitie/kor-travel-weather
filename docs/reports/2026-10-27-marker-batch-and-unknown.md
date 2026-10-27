# 지도 마커 배치 조회·미확인 표시 개선

## 운영에서 확인한 사실

2026-10-27 KST 조사 시 운영 소스는 `95020bd525ba930bcdd0e9a61036ad53f60cec11`이다.
API·code-server·웹의 정확한 이미지 ID가 PR #171 공식 빌드 원장과 모두 일치한다.
그 뒤 main `f25ea9c`는 문서 변경만 포함한다. API/CS는 2026-10-26 21:23Z 같은 이미지로
재생성됐고 웹은 06:38Z부터 실행 중이었다. 새 제품이 없는 상태의 강제 재생성은 하지 않았다.
이 문서의 후속 코드는 아직 운영에 배포하지 않았다.

실제 공개 UI에서 로그인·로그아웃·보호 화면, 목록 선택, 지도 위 상세, Escape, 가로 넘침을
확인했다. 1440×1000의 지도 높이는 824px, 375×812에서는 615px(top193/bottom808)이었다.
자료가 로드된 상세까지 포함한 14개 검사가 통과했지만, 마커 값과 응답시간의 수용은 아니다.
가평 상세에는 기온 10.59와 수집 06:41:47 KST가 표시되는데 마커는 `— / 구름`이었다.
60ID 배치 3개는 오래 대기하다 조건 변경으로 취소됐고, 이어진 1ID 요청은 약 30초 후 504였다.
같은 지점의 단일 내부 공개 marker GET은 200·0.925초·TEMP 10.59를 반환했다.

기존 한 지점 SQL의 읽기 전용 EXPLAIN ANALYZE는 current 2315.8ms, forecast 378.5ms,
alert 342.1ms였다. current/forecast는 지점별 metric 6개에서 이력을 반복 읽었다.
current는 shared read 1167 blocks였다. 서로 캐시·수집 상태가 달라 0.925초와 비율로 비교하지
않는다. 22:04Z 관측에서 30초 이상 잔류 query는 없었으며 영구 잔류·교착을 확정하지 않았다.

## 채택한 변경

기존 metric 우선순서, 최신 현재값/가장 가까운 예보의 정렬, 종류·제공처 필터와 각 그룹의
행 예산을 유지한다. 원본은 항상 `(value_id, known_at)` 복합 키와 실제 시각 범위로 읽는다.
API DTO·SDK pin·제공자 호출·수집 정책은 바꾸지 않는다.

`0034_marker_kind_indexes`는 projection에 다음 부분 covering 인덱스 4개를 추가한다.
대상 metric은 TEMP/T1H/TMP/WEATHER_CODE/SKY/PTY, 현재 종류는 observed/nowcast,
예보 종류는 ultra_short/short/mid다. 기존 다른 경로의 인덱스는 삭제하지 않는다.

| 이름 접미사 | 앞쪽 키 | target 방향 |
| --- | --- | --- |
| `marker_current` | location_id, metric_key | DESC |
| `marker_forecast` | location_id, metric_key | ASC |
| `marker_provider_current` | location_id, provider, metric_key | DESC |
| `marker_provider_forecast` | location_id, provider, metric_key | ASC |

모두 known_at/source_record_key의 DESC NULLS LAST, value_id DESC까지 포함한다.
필터 판정에 필요한 provider/forecast_style/weather_domain/dataset_key는 키 또는 INCLUDE에 둔다.
현재 종류에서 상수인 선호 CASE만 정렬에서 제외한다. 부분 인덱스를 증명할 고정 metric/종류만
literal이며 요청의 위치·제공처·종류·시각은 계속 bind한다. 다중 제공처는 각 제공처의 topN을
UNION ALL한 뒤 전체 topN을 정해, IN 조건 뒤 전체 이력을 정렬하는 generic plan을 피한다.

특보 후보는 server cursor로 512행씩 읽는다. 기존 최근 3일 조건에 이미 해당하는 후보는
그 조건만으로 통과한다. 그보다 오래된 후보는 512개 이하의 복합 키 배열 2개와 known_at
범위를 통해 `valid_until > now`를 확인한다. 통과한 후보 전체에서 지점별 최종 topN을 유지한
뒤 원본을 수화한다. 명시된 종료 시각이 미래인 장기 특보를 먼저 LIMIT하거나 임의의 새
날짜 기준으로 버리지 않는다. 원래 시작·종료·해제/대체 판단은 기존 특보 reducer가 수행한다.

키 배열은 scalar bind 수를 후보 수만큼 늘리지 않는다. 최대 500개 위치에서도 임시 후보는
512행, 유지 winner는 지점별 한도만큼이다. 최종 원본 수화는 지점 수×그룹 한도로 제한된다.
전체 이력을 검토해야 하므로 총 조회 수는 오래된 후보 수/512에 비례한다. 이력이 무한히 커져도
총 시간이 일정하다고 주장하지 않는다. 실제 경로는 SQLAlchemy+psycopg 3이다.

미확인은 실제 구름과 구별해 `? / 정보 없음`으로 표시하며 두 마커 렌더 경로가 같은 함수를
쓴다. 실패는 성공 cache에 넣지 않고 지도에서만 짧은 오류와 읽기 재조회 버튼을 제공한다.
목록 전환 뒤 무반응 버튼이 남지 않는다. 완료 chunk 보존과 기존 취소/세대 격리를 유지한다.
전체 API timeout을 늘리거나 응답에 없는 값을 채우지 않는다.

## 버린 안과 검증 경계

지점별 window rank와 위치×metric 전체 벡터 window는 결과 의미 회귀를 통과했지만,
운영 60ID 읽기 전용 shadow에서 각각 5.280초·5.188초에 SQLSTATE57014로 끝났다.
scan loop 수만 줄었다는 이유로 성능을 수용하지 않았다. 기존 특보 60ID도 5.873초에 끝나지
못했다. 단일 SQL의 특보 CTE/window 합류도 5.645초에 끝나지 못해 채택하지 않았다.

중간의 전체 후보 일괄 skinny fact 조회는 3240후보·원본 4440.6ms로 5초 내에 끝났지만,
4066 read blocks였고 여유가 적었다. 최종 512키+최근 후보 생략 방식의 운영 shadow는
후보 3240개 0.530초, 원본 7개 chunk 각각 0.124~1.019초, 전체 5.440초로 완료됐다.
각 원본 statement는 2초 상한이었다. 이는 일반 공개 endpoint 전체나 동시 3배치의 수용이
아니며, 이전 조사로 읽힌 cache와 수집 시점 차이도 있어 전후 배수로 환산하지 않는다.

전체 후보 일괄 shadow의 첫 시도는 진단기의 literal timestamp 배열에 cast가 빠져 42883이었다.
실제 bind/cast로 바로잡은 뒤의 결과와 구분한다. 초기 UI 진단 2회도 로그인 URL glob·hydration
판정 문제였으며 수정된 검사기의 통과 결과로 이력을 덮지 않는다.

## 격리 검증

WSL2의 전용 PostgreSQL 16에서 다음을 확인했다. 운영 DB에는 DDL·수집 변경·강제 query
취소를 수행하지 않았다. 원본 체크아웃의 미커밋 metrics 파일 4개는 보존했다.

- 60지점×6metric×80시간×현재/예보의 57,600행: 제공처 미지정·부재·오래된 제공처·다중
  제공처, 실제 custom/generic PREPARE 결과가 같았다. 각 계획은 대표 후보 360행 이하만 읽었다.
  최종 fixture에서 계획 시간은 0.995~5.801ms, read/temp는 0이었다. 운영 ARM 성능은 아니다.
- 한 지점에 종료된 특보 53개와 8일 전 시작한 유효 특보를 섞은 60지점 조회가 유효 특보를
  보존했다. 최종 fixture 조회는 0.197초였다.
- 500지점·1000개 장기 유효 특보: 키 512개 이하 2chunk, bind 수 10개 미만, 모든 위치의
  전역 top1을 보존했다. 원본 복합 키·partition·generic 계약을 포함한 집중 검증 27개 통과다.
- metadata/전체 Alembic 인덱스 정의 일치, 유효 OID 보존, invalid 재생성, downgrade/re-upgrade,
  timeout 상한·더 엄격한 호출자 설정·예외 후 복원을 확인했다. 구조 회귀를 새 2단계 특보
  경로의 실제 복합 JOIN/시각 범위 계약으로 바꾼 뒤 집중 검증 15개가 통과했다.
- 최초 frontend257개·후속 빈 위치 회귀를 포함한259개, type-check/lint/build와 저장소 전체
  Ruff를 통과했다. 기존 admin-shell lint 경고1개는 별개다. 목록 전환·빈 위치 뒤 오류 버튼과
  위치 복귀 후 새 지도 요청도 회귀에 포함했다.
- 전체 pytest의 첫 후속 실행은 기존 구조 테스트 1개 실패·178개 통과 후 중단했다.
  이 실패는 위 구조 회귀 갱신으로 해결했고 정확한 후속 전체 실행·CI·독립 2인 리뷰는 진행 중이다.
  이전 window 실험의 전체 실행도 178개 후 중단한 기록이며 전체 통과로 세지 않는다.
- 첫 후보 `5ba2fd1`은 CI Python 1299개·frontend 257개 및 lint/type/build를 통과했다.
  그 소스의 로컬 전체 실행은 원래 worktree를 불변 보존한 채 자연 완료를 기다린다.
  아래 migration 보호 보강은 별도 worktree/DB에서 검증하며 새 정확 SHA의 CI·두 리뷰를 받는다.
- migration 보호를 포함한d421 CI도 통과했다. 후속 UI만 바뀐1bda CI는1318통과/1실패였다.
  실패 지점은 조회 측정 전57,600행 projection fixture INSERT의 FK 검사로, fixture전용30초
  한계에 걸렸다. timeout/FK/행 수는 유지하고 known_at source 파티션 조건과10지점씩6개
  INSERT·총57,600행 검산으로 한 statement의 적재 작업량을 제한했다. 조회 측정5초 예산과
  custom/generic 비교는 그대로다. 이전 충돌 회귀 DB를 재사용한 첫 후속은 의도된 wrong-index
  잔여 때문에 대표 scan2개가 실패했다. 새 전용 DB에서는3개 집중 검증이31.165초에 통과했고
  60지점 계획은0.884~5.088ms/read·temp0, 장기 특보는0.221초였다.

인덱스 비용은 별도의 전용 DB 두 개에 동일한 합성 projection 10만 행을 넣어 측정했다.
4개 concurrent build는 각각 0.147~0.197초, 추가 인덱스 합계 약 23.35MB였다.
1000행의 포인터 revision UPDATE는 기존 30.109ms/WAL 1,037,552B, 신규 인덱스 포함 시
31.122ms/WAL 1,511,776B였다. FK를 제거한 projection만의 합성 비용으로 수집 전체나
운영 빌드 시간으로 외삽하지 않는다. 쓰기 증폭과 공간 비용이 실제로 추가된다.

## 리뷰 후 migration 보호 보강

독립 리뷰에서 이름만 같은 다른 table의 invalid index를 회수할 위험과 valid wrong-definition을
건너뛸 위험, current_schema와 실제 table schema가 다른 경계를 발견했다. 후속0034는
`to_regclass`가 가리키는 실제 table OID·namespace를 사용해 4이름 전체를 DDL 전에 검산한다.
키/방향/NULL 정렬·INCLUDE·부분 predicate·표현·default opclass/collation·unique/live/ready를
대조하고 충돌하면 index 삭제나 revision 승격 전에 실패한다. 생성·삭제는 실제 schema를
명시하며, 동일 table·정확 정의의 invalid index만 회수한다. 관측 가능한 다른 index build가
진행 중이면 재시도하지 않는다. 생성 후에도 정확 정의와 valid/ready를 다시 확인한다.

운영 DB는 기존 읽기 전용 관측의 SHOW로 PostgreSQL 16.10(160010)을 확인했다.
predicate는 PostgreSQL16의 원래 IN과 pg_get_indexdef 재생성에서 생기는 두 정확한
binary-coercion 형식만 허용한다. 임의 SQL 문자열 정규화나 추측으로 같은 의미라고 판단하지
않는다. 맞지 않는 valid 인덱스도 자동 재생성·삭제하지 않는다.

이 후속은 기존 migration 11개와 다른 table valid/invalid 충돌, 8종 잘못된 정의,
search_path 첫 schema가 다른 경우, downgrade 직전 foreign index 교체를 합친 31개 회귀를
별도 전용 DB에서 통과했다. 초기 보호 코드의 SQL alias/canonical 표현 검산 실패 이력도
보존한다. 새 source를 원래 전체 실행 도중에 덮어쓰지 않았다. 간헐적인 WSL launcher
0x8007274c는 Python 진입 전 실패로 구분하며, 기존 실행·수집 실패로 추정하거나
WSL/VM/Docker 서비스를 재시작하지 않았다.

## 후속 UI 리뷰의 빈 위치 경계

첫 보호 후보의 독립 리뷰는 지도504 후 검색 결과가0이 되거나 모든 종류를 해제했을 때
이전 오류와 실행되지 않는 재조회 버튼이 남는 경계를 발견했다. 실제 effect와 오류 노출
표현식을 실행하는 두 회귀에서 먼저2실패/16통과를 확인했다. 지도 모드이면서 조회할
위치가 존재할 때만 마커 오류·재조회 버튼을 표시하도록 한 조건을 보강했다. 위치가 다시
생기면 기존 effect가 오류를 초기화하고 실제 요청·성공 cache 갱신을 재개함을 확인했다.
원래5ba2 전체 실행 worktree는 계속 불변으로 보존하며 이 변경은 별도 worktree에서 검증했다.

## 수집과 배포 조건

2026-10-26 23:25:34Z 관측에서 비정기 단기예보는 287,900값으로 계속 증가했다.
지역 묶음은 해변80·휴게소1135·산림 dust3269·산악4824 저장 후 자연 종료했다.
비정기 초단기예보 30,162값·실황 3,635값, WeatherAPI current1650/forecast52,020값도
자연 종료했다. OpenMeteo current는2660값 저장 후 성공했고 forecast는34,944값으로
진행 중이었다. 정기 실황은 queued, 단기예보/OpenMeteo의 실제 worker가 남아 있었다.
Claude Code가 시작한 batch의 정확한 귀속은 확인되지 않았으며, 비정기 run 관측과 구별한다.

후속23:38:20Z 관측에서 단기예보는23:35:42Z에321,869값/300격자로 자연 성공 종료했다.
OpenMeteo forecast는65,856값·23:37:49Z heartbeat로 진행 중이며, 정기 AirKorea도 시작했다.
대기 중이던 실황은23:34:02Z에3,490값으로 성공해 queued는0이지만 실제 worker4015/5112가
남아 있었다. 이 관측도 전체 수집 종료나 배포 완료로 해석하지 않는다.

2026-10-27 00:01:00Z에는 OpenMeteo forecast가135,744값·00:00:45Z heartbeat로
계속 진행 중이었다. 정기 AirKorea는4,156값으로23:50:52Z에 성공했고 새 초단기예보는
queued였다. 실제CS worker4015가 남아 있으므로 이미지 교체 조건은 여전히 충족하지 않았다.

당시 instigator는 21 RUNNING/2 STOPPED였다. publisher는 RUNNING이고 retention/native
recovery는 STOPPED였다. 초기 조사 22/1과 다른 외부 변경이므로 옛 원장으로 복원하지 않는다.
기존 retention FAILURE는 성공으로 덮지 않으며 `deg_c` 원문 단위 표시도 별도 미해결이다.

운영 projection은 추정 8,613,092행, heap 2,409,832,448B, 기존 index 12,493,283,328B다.
NVMe `/dev/nvme0n1`의 `/srv/ktdm-releases`·Docker 경로 여유는 약 246.4GB로 관측했다.
합성 fixture와 행 폭·분포가 같다고 가정하면 신규 4개 합계는 약 2GB 규모지만, 운영의 키 폭과
metric/종류 분포가 달라 확정치가 아니다. 배포 직전 여유를 새로 확인하고 각 인덱스 빌드 전후
증가량을 기록한다. 운영 계획에서는 이미지·임시 정렬·WAL 여유를 포함해 최소 60GB의 남은
NVMe 공간을 guard로 둔다. 이 관측으로 ARM 빌드 시간을 보장하지 않는다.

정식 반영 절차는 다음과 같다.

1. 정확 후보 SHA의 CI·독립 2인 리뷰·병합을 마친다. 요청된 비정기 수집이 자연 종료했는지
   Dagster와 DB 결과를 대조한 뒤 Weather instigator만 공식 절차로 일시 중지한다. 남은
   queued/active와 물리 worker가 모두 사라지기 전에 이미지를 교체하거나 migration하지 않는다.
2. Manager 정본의 NVMe/G→Weather R 절차로 현재 이미지 ID·환경·전체 instigator 개별 상태·
   publisher·다른 프로젝트 상태를 새로 저장한다. 공유 daemon/PG는 재생성하지 않는다.
3. 같은 이름의 기존 유효 인덱스가 있다면 격리 생성한 정확 정의와 대조한다. 이름만 같은 다른
   정의는 성공으로 간주하지 않는다. 신규 4개는 CONCURRENTLY, statement당 최대 180초·
   lock 최대 5초로 만들며 더 엄격한 호출자 설정을 보존한다. 180초는 전체 4개의 합계가 아니다.
   실패하면 해당 revision을 완료로 취급하지 않는다. 재시도는 유효 인덱스를 보존하고 invalid
   인덱스만 회수한다. 공간·WAL·진행과 유효/ready 상태를 확인해 무조건 반복하지 않는다.
4. schema0034와 정확 신규 이미지·health/version을 확인한 뒤 PC/모바일에서 60ID×3 실제
   marker200·온도/상태·같은 지점 상세·기준시각·재조회·목록 전환·로그아웃을 수용한다.
   그 뒤 원래 instigator 상태를 복원하고 자연 수집의 저장 증가/갱신을 관측한다.
5. 되돌릴 때는 새 revision을 모르는 옛 이미지로 먼저 migrate하지 않는다. 적용을 마친
   schema0034는 **신규 이미지의 Alembic으로0033까지 downgrade**하고 4개 인덱스 제거와
   revision0033을 확인한 다음 옛 정확 이미지·환경으로 돌아간다. 이미지만 되돌린 것을 DB
   복구로 보고하지 않는다. downgrade도 실패하면 새 이미지와 실패 원장을 유지하며 중복
   실행하지 않는다. 도중 실패로 version0033에 일부 valid/invalid 인덱스만 남았다면 그 잔여를
   별도로 기록한다. 자동 stamp나 알려지지 않은 revision 덮어쓰기로 정산하지 않는다.

이는 배포 계획이며 완료 기록이 아니다. 운영 새 이미지·최종 실제 배치 응답·배포 후 자연
수집 수용은 아직 남아 있다. 인증 자료·provider key·raw payload·환경 원문은 증거에 싣지 않는다.
