# ADR-089: current summary and source revision

fact key는 `(location, provider, dataset, weather_domain, forecast_style,
metric_key, target_at, source_record_key)`다. source key는 response-level raw
lineage와 correction revision을 함께 식별한다. current/latest는 source key를
그대로 모두 노출하지 않고 logical target마다 `known_at` 내림차순의 하나를
선택한다. rebuild/history는 immutable rows와 run/source association을 이용한다.
