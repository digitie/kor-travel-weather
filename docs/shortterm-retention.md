# KMA 단기예보의 지난 대상시각 보존정책

## 확정 요구와 범위

단기예보의 대상시각 valid_at이 현재시각보다6시간 넘게 지난 행만 정리한다.
발표시각 issued_at이나 수집시각 known_at의 age로 삭제하지 않는다.
경계와 같은 행 및 미래 예보는 보존한다. timezone-aware 입력만 허용하고 UTC로 비교한다.

정확한 대상은 provider=python-kma-api, dataset_key=weather_domain=kma_short_forecast,
forecast_style=short다. 초단기/중기/실황과 다른 provider는 포함하지 않는다.
이 문서와 후속 구현은 기존101 기준 별도 feature이며 health/Common pin/Docker/주기 변경은 없다.

## 구현 계획과 참조 보존

기존 fact는 known_at으로 일별 partition되므로6시간 valid_at 정책에 partition DROP을 쓰지 않는다.
최대1000개의 정확한 복합키(value_id, known_at)를 한 batch로 선택하고,
같은 transaction에서 해당 fact를 참조하는 projection부터 지운 뒤 fact를 지운다.
다른 known_at의 동일 value_id는 보존한다. source/raw와 sync-run association은 삭제하지 않는다.
기존 append-only DELETE 허용은 transaction-local PURGE_GUC만 사용한다.
timeout/lock 실패는 rollback하며 자동 재전송·강제취소를 추가하지 않는다.

recurring 정리 연결과 batch 상한은 후속 코드에서 명시한다. 실행 주기 사이에는
경과 데이터가 남을 수 있으므로 소스 완료를 실데이터 정리 완료로 표기하지 않는다.
현재 daemon OFF/배포·초기정리는 root의 별도 검토·운영 영역이다.

## 검증과 운영 순서

경계1마이크로초 전/같음/후, 미래와 오래된 발표의 미래행, UTC/KST 동등성,
다른 provider·dataset·style·domain·NULL valid_at,
복합키 projection 참조와 원천공유, batch/rollback을 검증한다.
기존 fact를 전부 count하거나 원천 raw/주소를 출력하지 않는다.

운영은 root만 count/정확predicate와 실행계획·소유성 확인→bounded batch 삭제→
삭제수·미래/경계/타원천 보존·참조 정합 재확인 순서로 수행한다.
이 PR은 운영 삭제·새job 실행·서비스 변경을 수행하지 않는다.
