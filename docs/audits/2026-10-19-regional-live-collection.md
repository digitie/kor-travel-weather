# 지역 수집의 수동 실행 수용

제품 #119의 배포·UI36 이후 휴게소와 산악 관측을 한 번씩 직렬 수집하는 별도 수동
수용을 진행했다. 제품 코드는 683e446d로 유지하고, 문서 기준 main은 #119 머지
fd2bf1a3이다. daemon은 OFF이며 대기 큐3개를 실행하지 않는다.
이 기록은 전체 regional job, 예약 주기 또는 daemon 자동 복구 수용을 의미하지 않는다.

## 시도와 실패 구분

| 시도 | 제공자·DB | Dagster 결과 | 판단 |
| --- | --- | --- | --- |
| r1 | 실행 전 | 사전 probe 실패 | 독립 metrics 디렉터리가 없어 definitions import 중단 |
| r2 | 두 수집 DB 성공, 총6403건 저장 | FAILURE | 결과 파일의 handle_output이 읽기 전용 경로에서 실패 |
| r3 | 두 수집 DB 성공, 연결6403건·신규5256건 | SUCCESS | 휴게소 정상 재수집·산악 새 관측 저장 |

실패 native run을 삭제하거나 성공으로 바꾸지 않았다. r2의 DB 저장은 이후 감사로
직접 확인했으며 Dagster FAILURE와 별개로 보존했다. 자동 재실행은 하지 않았고,
실패 원인 수정·독립 검토 후 별도 소유 시도를 사용했다.

## r2 실제 저장과 관측 시각

시각은 UTC다. DB 감사 시각은 2026-10-18 22:50:42.640445Z이다.

| 데이터 | 원천 | 연결 사실 | 신규 저장 | 관측 최소·최대 | 감사 시점 나이 |
| --- | ---: | ---: | ---: | --- | ---: |
| 휴게소 | 187 | 1147 | 1147 | 둘 다 2026-10-18 21:00Z | 6642.640445초 |
| 산악 | 438 | 5256 | 5256 | 둘 다 2026-10-18 22:40Z | 642.640445초 |

두 DB sync 상태는 success이며 run-source 연결로 확인했다. 반복 fact identity와
빈 raw source/fact payload는 모두0이다. 이 값만으로 제공자가 약속한 최신 관측이라고
판정하지 않는다. 수집 성공, 신규 저장, 실제 관측 시각·나이는 서로 구분한다.
r2는 IO 처리 뒤 materialization에 도달하지 못해 native asset metadata가 비어 있다.
따라서 r2에서 새 제외 counter나 예산 잘림 여부까지 실측했다고 주장하지 않는다.

## 실행 도구 보강과 사전 검증

실제 code server와 같은 public Dagster SDK1.13.24를 사용하고 두 asset만 선택했다.
제품의 max_concurrent1, 제공자 timeout20초, regional_max_records1000,
max_values_per_run8000000은 변경하지 않았다. worker 제한은 CPU1·메모리1GiB,
임시 파일용 tmpfs128MiB이며 NVMe의 소유 디렉터리에 증거를 저장했다.

별도 bootstrap이 instance 환경을 주입한 뒤 multiprocess metrics 디렉터리를 소유
tmpfs로 분리하고 metrics listener 환경키를 제거한다. instrumentation은 유지하면서
기존 CS의 metrics 파일·포트를 사용하지 않는다. 결과 파일·compute log는 소유 NVMe
디렉터리에 저장하고, 수집 전에 실제 SDK 경로가 그 하위인지와 쓰기 가능 여부를 검사한다.
기존 서비스 설정과 읽기 전용 루트 파일시스템은 유지한다.

감사에서는 known_at과 run.started_at을 비교하지 않는다. 제품은 fetched_at을 먼저
정하고 run을 만들기 때문에 정상 사실도 시간조건에서 제외될 수 있다. 실제 PostgreSQL
fixture에서 이 반례와 동일 payload 재수집을 검증했다. 새 저장0이지만 유효 원천·사실이
이미 연결된 경우는 already_current로 구분한다. native와 DB의 원천수·신규 저장수를
대조하며 빈 응답·skip·잘림·상한 도달은 정상 noop과 구분한다.

로컬 회귀21개가 통과했다. 실제 읽기 전용 Docker의 SDK 검증에서는 state mount가
없을 때 결과 쓰기 실패, 소유 mount가 있을 때 artifact와 compute log 생성 및 SUCCESS를
확인했다. 이 로컬 fixture를 운영 수집 성공으로 대신하지 않는다.

## r2 측정 범위

수집·결과 감사 구간 wall90.883초, worker cgroup CPU67.266초,
worker cgroup peak265404416바이트였다. parent RSS peak154620KiB와
종결 자식 RSS peak169116KiB는 별도 high-water이며 합산하지 않는다.
같은 구간 공유 PostgreSQL CPU는25.437초였고 다른 SQL을 포함한다.

CPU는 ready/go부터 result/release까지로 부모의 ready 이전 준비를 제외하며
자식 import·초기화와 결과 감사를 포함한다.
cgroup 메모리 peak는 import부터의 절대 high-water다. 실패 시도1회의 절대값이므로
성능 개선율·호스트 전체 CPU·장시간 메모리 안정성의 근거로 사용하지 않는다.

[공개 요약 증거](2026-10-19-regional-live-collection.json)는 비밀·제공자 raw payload 원문·
실행 도구의 개인 경로를 제외했다. r3 종결 및 수집 후 UI36 증거를 포함한다. 문서의 독립 최종 리뷰는 별도로 수행한다.


## r3 최종 수동 수용

native run c632e290-ef7e-4561-8af6-354ebb8bfa62는 두 asset 모두 SUCCESS이며
DB 대조도 완료했다. 휴게소는 응답187·원천187·연결 사실1147건이며 신규 저장0건이다.
같은 원천 identity의 정상 재수집인 already_current이며 누락으로 판단하지 않는다.
산악은 응답513건 중 좌표 없는59건을 제외해454개를 처리했고, 유효값 없는16개를
제외한438개 원천에서5256건을 새로 저장했다. 두 asset의 비활성·잘림은 없고
기록 상한에도 도달하지 않았다. 반복 fact identity와 빈 raw source/fact는 모두0이다.

DB 감사 시각2026-10-18 22:58:29.354294Z 기준 휴게소 관측 최소·최대는 모두
21:00Z(나이7109.354294초), 산악은 모두22:50Z(509.354294초)였다.
수집 완료와 신규 저장, 관측 나이는 별개다. 제공자 SLA를 새로 정하거나 HTTP200을
최신성으로 판정하지 않는다. 실행 도구의 원시 freshness 필드는 감사 전 상태 문자열을
유지하므로, 실제 시각 판정은 observation_audit를 따른다.

수집·감사 wall87.630초, worker cgroup CPU66.304초이며 준비 CPU20.520초는 별도다.
worker cgroup 절대 peak273838080바이트(약261.15MiB), parent RSS154884KiB와
자식 RSS169120KiB는 각각의 high-water다. 공유 PostgreSQL CPU23.518초에는
다른 SQL도 포함된다. r2와 r3는 실패 여부·관측 시각·신규 저장량이 달라 전후 성능
개선 비교가 아니다. 전체 job 또는 장시간 메모리 안정성으로 확대하지 않는다.

owned probe와 worker는 exit0·OOM 없음으로 종료하고 증거를 보존했다. daemon OFF,
기존 큐3개와 다른 서비스 상태를 유지했다. 실제 NVMe artifact2개(582바이트)와
compute log6개(0바이트)를 확인했다. 성공 실행의 ERROR 로그 수준에 따른 빈 로그이며
UI 다운로드 검증은 아니다.

수집 후 동일 제품으로 UI36을 다시 실행해36 PASS·0 FAIL을 확인했다.
실행 구간은2026-10-18 23:00:17.506Z부터23:00:48.788Z까지이고 runtime은 동일하다.
로그인·메뉴·지도·수집 이력·Dagster 모니터 및 기존 오류 주입 후 UI 회복 범위이며,
서버 장애 또는 자동 복구 수용과 구분한다.

## Weather Track1/2 현재 수용 범위

| 항목 | 현재 상태와 남은 완료 조건 |
| --- | --- |
| 지역 두 asset 수동 수집·중복 방지·run-source 연결 | 이번 r3와 DB 감사로 완료 |
| 제외 사유 및 관측 시각 확인 | 좌표59·빈값16 실측, 관측 시각·나이 기록 완료 |
| 배포 제품 UI | #119 배포 UI36 및 이번 수집 후 UI36 완료 |
| 주소 공급자 정상/fallback | 기존 대표 좌표 호출 완료; 전체 주소 갱신 수용을 대신하지 않음 |
| 전체 주소 수동 갱신 | 대표 좌표 외 전체 대상·admin 실행 결과 수용은 이 기록에서 미검증 |
| 6개월 주소 갱신·6시간 보관 자동 진행 | daemon OFF 의도적 보류; 자동 주기 실행 및 결과 확인 필요 |
| 자동 재시도·중단 복구·장시간 전체 수집 | daemon OFF 의도적 보류; 실제 자동 실행과 장시간 CPU/RSS/OOM/swap 수용 필요 |

다른 저장소의 Track 수용이나 전체 provider 수집을 이 문서로 완료 처리하지 않는다.
추가 성능 튜닝보다 남은 Track 수용을 우선하며, daemon 재개는 별도 지시를 따른다.
