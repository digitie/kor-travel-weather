# KMA weather ETL

1. enabled `WeatherLocation`을 읽고 grid를 dedupe한다.
2. `KmaClient` 또는 `DataGoKrClient`로 응답을 받아 grid/base 시각을 검증한다.
3. 응답 전체를 response-level `SourceRecord`로 hash/보존한다.
4. KMA row adapter가 metric/unit/target window를 정규화한다.
5. complete manifest만 source/fact 단일 transaction으로 적재한다.
6. sync run과 source association을 기록하고 admin에서 상태를 노출한다.

현재 MVP에는 durable cursor가 없으므로 매 hourly run에서 응답을 재검증한다. 빈
응답은 성공으로 간주하지 않고 run을 failed로 남기며 fact를 publish하지 않는다.
같은 raw 응답 replay는 no-op이고 수정 payload는 immutable revision이다. 호출 비용을
줄이는 base-time/membership cursor는 후속 운영 범위로 명시한다.
