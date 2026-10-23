# 다중 제공처 일간 요약의 승인 범위

W5는 KMA 초단기실황과 SDK·HTTP 현재 관측의 승인 artifact를 기존 작은 요약 표에
사전 계산한다. 요청 시 fact/날짜 파티션/비공개 시간 winner를 다시 읽지 않는다.
산림청·KREX normalizer의 `℃`도 `deg_c`와 같은 섭씨 단위로 허용한다. 의미가
확인되지 않은 누적·순간 강수는 합산하지 않으며 KMA RN1 시간 구간만 채택한다.

REST는 현재 dataset registry의 실제 승인 위치를 사용한다. 같은 위치에 여러
원천이 있으면 **각 namespace의 최신 승인 generation**이 reducer coverage에
도달했는지 확인한다. 최초 coverage가 없고 이전 성공 집합도 없으면503, 이전
성공 집합이 있으면 단조 issue_order의 최신 snapshot roster를 유지하고 stale로
표시한다. TTL이 지났으면 새 handle로 복제하여 옛 cursor를 연장하지 않는다.
이전 coverage만 있어도 기존 요약을 유지하고 stale로 표시하여 정상304로 감추지 않는다.
한 제공처가 준비됐다는 이유로 다른 제공처의 reducer 지연을 정상으로 처리하지 않는다.

예보만 있는 KHOA 위치는 승인 관측이 없는 상태와 구분한다. 관측 원천의 대상이
아닌 위치는 없는 구간을 null/sample_count=0으로 내보낼 수 있으며, 빌려 쓸 원천
수집 시각이 없으면 source_collected_at=null/source_status=stale다. 기본 요청의
필수 현재 dataset이 미준비면 전체를 축소 성공하지 않고 이전 전체 집합의
stale/최초503을 유지한다. 기존 cursor도 새 원천의 미계산을 건강도에 반영한다.
SDK가 새 station을 발견해 HTTP 전 cohort에는 아직 없더라도 canonical
measurement_point의 활성 관측 provider/network가 확인되면 최초 관측 coverage를
요구한다. 미승인/아직 모름은 예보 전용의 무관측0과 구분하며 개별 최초 조회는503이다.
이 판단은 provider/dataset/location별 실제 승인에 적용한다. 같은 위치의 다른
제공처 관측이 준비됐더라도 아직 가입하지 못한 원천의 첫 승인을 대신하지 못한다.

source별 신선도는 current와 같은 정책을 따른다(지역 SDK36시간, HTTP·실황3시간).
선택 위치의 원천/coverage/작은 summary/checkpoint는 한 REPEATABLE READ에서
확인한다. 14일 기본·31일 최대, KST 세 구간, 미완 오늘 구간partial, 시간당 winner와
복수 제공처/재수집 중복 배제 정책은 기존 W5 계약을 유지한다.

이 단위는 schema/DTO를 바꾸지 않는다. 기존 과거6시간 이력 보존 정책을 바꾸지
않고 별도 작은 사전 요약을 사용한다. 운영 활성화/공유 artifact 권한·pin/GC·측정
비용 gate는 별도로 수용하며 이 PR에서 운영 수집을 추가 실행하지 않는다.
