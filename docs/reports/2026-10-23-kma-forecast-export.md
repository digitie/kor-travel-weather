# KMA 예보 export 수집 경로

## 범위

W3 공개에 필요한 KMA 초단기/단기예보의 원본 수집·불변 artifact 승격 경로를 추가한다.
기존 기본값은 OFF이며 운영 설정과 daemon은 변경하지 않는다. REST 예보 reader,
중기/외부 제공처, status, 일간 요약은 후속 작업이다. 이 PR만으로 W1~W5 완료가 아니다.

## 저장·검증

- HTTP 전에 dataset/grid/위치 선택과 정책을 고정한다. SDK의 실제 전체 페이지 receipt와
  발표 base를 검사하며 발표 시각·격자를 자체 계산하지 않는다.
- 기존 Weather normalizer를 그대로 사용하고 raw payload/lineage를 보존한다.
- 초단기는 필수 7개 category가 있는 연속 6시간, 단기는 필수 8개 category가 있는
  연속 72시간을 최소 증거로 요구한다. 전체 pagination 검증을 대체하지 않는다.
  확장 기간의 3시간 간격·정성형 강수량을 강제로 1시간 숫자로 바꾸지 않는다.
- 이전 승인 집합의 절반 미만으로 줄어든 집합은 승인하지 않고 재확인을 요구한다.
  이 조건이 정상 원천 변경과 충돌하면 원본 fact는 보존하고 공개 승격만 중단한다.
- 하나의 격자는 최대 50개 위치/100,000 point다. 전체 값 객체를 fan-out하지 않고
  기존 위치별 2,500 fact 게시 배치로 저장한다. 한 위치의 원천 자체가 2,500개를
  넘는 경우 기존 정책과 같이 해당 위치를 쪼개지 않되 5,000개 상한을 유지한다.
- chunk별로 실제 normalizer 결과의 부분집합인지 검증하고 최종 승격에서 전체
  count/위치/semantic digest를 다시 대조한다. 누락된 chunk는 성공 head가 되지 않는다.
- 최종 승격은 최대 64 chunk/128 MiB로 제한하며 파일은 한 번에 하나씩 읽는다.
  reconcile은 기존 tick당 최대 8 chunk/16 MiB 검증을 유지하고 다음 sweep에서 재개한다.

## 운영 전 조건

collector, publisher, reader의 실제 전체 wall-clock/최대 RSS 및 GC 수명 측정이
필요하다. 현재 파일 guard의 timeout은 잠금 획득 대기 상한이며 전체 승격 시간
상한이 아니다. Odroid 자동 활성화는 이 PR에서 수행하지 않는다.

## 근거

기상청 public API와 최신 가이드의 6시간 초단기/확장 단기예보 정의를 참고한다.
추가 category(예: POP)는 최소 필수 집합 외에도 원본과 정규화 결과에 유지한다.

- https://www.data.go.kr/data/15084084/openapi.do
- https://apihub.kma.go.kr/apiList.do?seqApi=10
