# ADR-072: weather bitemporal

`known_at`은 provider response를 받은 시각이고 `target_at`은 해당 값이
가리키는 관측/예보 시각이다. `known_at`을 fact identity에 넣지 않는다. 같은
응답 replay는 같은 source revision이고, 수정 응답은 새로운
`source_record_key`로 append된다. public current projection은 target별 가장
최근 known/source revision 하나를 선택하며, explicit history/as-of query만
revision 전체를 반환한다.


## KMA 단기예보의 보존 후 재입장 예외

지난 대상시각6시간 밖만 정리하고 미래는 제한하지 않는 정책에 따라, 보존할 KMA 단기예보의
원 fact가 없고 원 수신 clock이 retention gap에 있어 저장할 수 없을 때에만
실제 재입장 시각을 fact.known_at으로 사용한다. 정상 첫 입장과 DEFAULT에 수용 가능한
원 clock은 그대로 둔다. 원천 source.fetched_at이 원 canonical known_at을 보존하며,
source_record_key/collected_at/issued_at/valid_at/raw payload는 변경하지 않는다.
이미 retained row가 있으면 최초 row의 복합키를 우선 재사용하여 반복 replay로 행을 늘리지 않는다.
이는 기존 잊힌 replay 재입장 예외의 좁은 확장이며 일반 provider의 clock 정의를 바꾸지 않는다.
