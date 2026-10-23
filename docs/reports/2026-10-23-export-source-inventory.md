# 실제 수집 source inventory와 quota 회전

## 문제와 설계

W2/W3의 기본 조회를 활성 위치×모든 provider의 Cartesian product로 정의하면 실제
요청하지 않는 조합까지 요구해 영구503이 된다. 반대로 최신 quota 선택 목록만 읽으면
이번에 선택되지 않은 이전 승인 위치가 사라진다. 원천 source plan과 승인 위치 projection을
분리하고, 실제 수집 전 선언한 대상 및 마지막 승인 위치의 합집합을 사용한다.

`weather_export_approved_locations`는 provider/dataset/location/source_part마다 마지막 승인
namespace/generation, 정확한 좌표/grid, 원문 수집 시각과 publication 시각만 보존한다.
raw·fact·임의 metadata는 복사하지 않는다. 같은 위치의 재승인은 publication CAS upsert로
한 행을 갱신하며 quota 밖 이전 위치는 삭제하지 않는다. source head와 같은 transaction에서
작성하므로 API가 반쪽 승인 상태를 읽지 않는다.

기본 source_part는 `main`이다. 중기예보는 `land`/`temperature`를 구분해 같은 지점의
육상/기온 원문 승인이 서로 덮이지 않도록 한다. API에서는 같은 발표의 두 부분을 조합한다.

- 처음인 provider/필수 source 미승인은503이다. 기존 승인 집합 뒤 실패·좌표 변경·미확인
  축소는 이전 승인 vector를 stale로 유지한다. 작은 부분집합을 전체 정상으로 꾸미지 않는다.
- 최신 pre-HTTP cohort의 native 성공·해당 run 승인은 별도로 요구한다. 과거 quota에서
  승인된 위치에 최신 run과 같은 run_id를 요구하지는 않는다.
- KMA는 실제 선언·승인 source를 사용한다. 무관한 산림청/KHOA 위치에 KMA head를
  요구하지 않는다. 기존 위치/그룹 잠금과 원본 수집의 bounded chunk는 유지한다.
- source scope별 신선도를 판정하고 전체 메타에는 실제 채택 집합의 가장 오래된 원문
  clock을 제공한다. 최신 다른 위치가 오래된 위치/실패를 가리지 않도록 한다.
- 별도 청정넷 catalog 승인이 없는 최초 상태는 정상0건으로 확정하지 않는다.
  기존 승인/anchor가 있던 source는 실패 후에도 기대 집합에서 제거하지 않는다.
- W5에서 관측 source를 기대하는 위치의 미승인과 forecast-only 위치의 관측 없음은
  source registry/cohort에 따라 구분한다. 후자는 null/sample0이고 전자는503/stale다.

## 이전 승인 bootstrap과 파일 수명

이전 버전 head는 PK namespace 키셋으로 한 번에 기본2개/최대16개씩 inventory에
재투영할 수 있다. fact/파일을 읽지 않으며 기존 승인 원문 clock을 보존한다. 완료 cursor를
기억하고 전체 head 목록을 매 tick 재순회하지 않는다. 이 helper의 자동 호출은 아직 없다.

inventory의 generation FK는 `ON DELETE RESTRICT`다. head가 교체/삭제됐어도 inventory가
참조하는 generation과 artifact는 live root다. 향후 GC는 heads, inventory, pending receipt,
유효 snapshot/cursor, W5 coverage/checkpoint의 참조를 모두 합쳐야 한다. FK만으로 파일
삭제가 방지된다고 주장하지 않는다. 아직 파일 GC는 없으며 cursor/guard race·비용 검증과
공유 NVMe artifact 권한 검증 전에 GC/운영 활성화를 하지 않는다.

inventory와 source head의 generation_id 자식 index를 함께 두어 향후 parent generation
삭제/FK 검사 때문에 작은 projection 전체가 반복 순회되지 않도록 한다.

W1 catalog source clock은 현재 null/stale이며 updated_at으로 대체하지 않는다. Map 인계에는
독립 reference의 최초 snapshot 수용 계약을 명시해야 한다.

## 현재 구현과 남은 task

- [x] 작은 승인 table/원자적 publication upsert/이전 위치 유지
- [x] namespace 키셋 legacy bootstrap helper
- [x] quota 회전·오래된 projection replay·FK live-root·bootstrap PostgreSQL 회귀
- [x] SDK의 실제 pre-HTTP source cohort와 atomic native intent 연결
- [x] 내부 inventory/current reader의 승인 합집합과 매 페이지 RR 건강 확인
- [x] regional/AirKorea 실제 capture·KHOA 페이지 조합 source lineage
- [x] 중기 descriptor dispatch·지역 anchor·발표 시각 bind와 역할별 inventory seam
- [ ] KMA base의 실제 pre-HTTP cohort 및 REST 기본 query 연결
- [ ] forecast inventory reader·전체 provider 기본 query
- [ ] KMA mid의 동일 발표 land/temperature 조합과 grouped REST
- [ ] W5 관측 대상/온도 단위 확장 및 모든 provider 수용
- [ ] OpenAPI/TS·실제 HTTP/UI·두 적대리뷰·CI와 배포 전 비용/GC gate

현재 내부 reader와 SDK native 배선 단위이며 전체 API/운영 배포 완료가 아니다.

내부 frozen current reader의 명시 source_inventory 모드는 승인 합집합을 선택하고
선택 밖 artifact point는 검증 후 출력에서 제외한다. source별 clock/필수 coverage 및
최신 native 실패를 매 페이지의 같은 RR에서 재확인하므로 이전 cursor도 stale를 유지한다.
기본 router는 아직 이 모드로 전환하지 않았다. 실제 PostgreSQL current quota·cursor 회귀와
기존 frozen API/SDK/외부 수집을 합쳐107개가 통과했다. 이후 0032를 실제 통합하고
기존 AirKorea/asset·0033 upgrade 집중43개를 확인했다. 실제 SDK 네 관측망과 부분
KREX 원문 거절 회귀도 통과했다. 최종 고정 SHA 집중/CI/두 리뷰는 별도로 수용한다.

처음 canonical anchor가 없는 regional source는 원래 native 수집으로 catalog를 만든다.
다음 수집부터 기존 anchor의 원천 코드/좌표를 HTTP 전에 선언할 수 있다. 최초 승인 전에는
503이며 별도 SDK catalog가 없는 원천의 전국 완결을 single-record parser로 주장하지 않는다.
KREX는 raw list 개수와 typed items/전체 개수를 맞춰 부분 파싱을 거절한다. KHOA는
동일 크기 연속 페이지·원문 전체 수·장소별 중복 날짜/구간을 검증한 조합만 승인한다.
미완료 계획은 원래 수집 결과를 훼손하지 않고 실패로 마감한다.

export opt-in은 station별 추가 metadata/capture transaction을 사용한다. OFF의 원래
50위치 chunk는 유지하며, 실제 ARM CPU/메모리·잠금 대기·wall clock 실측 전에는
이 배선이 성능 개선을 완료했다고 주장하거나 운영 flag를 켜지 않는다.
