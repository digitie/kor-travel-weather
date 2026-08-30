# 외부 weather provider migration

기존 KMA fact와 source record는 변경하지 않는다. 새 provider는 다음 순서로
활성화한다.

1. 환경별 secret manager에 provider key를 등록한다. `.env`와 fixture에 실제 key를
   커밋하지 않는다.
2. `KOR_TRAVEL_WEATHER_ENABLED_PROVIDERS`에 provider key를 추가한다. `open_meteo`와
   `wttr_in`은 key 없이도 사용할 수 있다.
3. admin의 provider/dataset catalog와 Dagster `external_weather_job`에서 응답을
   확인한다.
4. 성공한 응답만 source record와 normalized facts로 atomic publish한다. 실패한
   실행은 failed sync run만 남기며 기존 immutable fact를 변경하지 않는다.

외부 provider job은 기본 schedule에 자동 연결하지 않는다. 유료 API의 quota와
provider별 cadence가 서로 다르므로, 운영자가 대상 provider resource와 budget을
검토한 뒤 `external_weather_job`을 수동 launch하거나 별도 schedule을 명시한다.

기존 `weather_values`/`weather_source_records` schema migration은 필요 없다. 새
dataset은 기존 source lineage의 `provider`, `dataset_key`, `source_record_key` 축을
사용한다. `WeatherValue.identity_key()`는 source response key를 revision으로
포함하므로 같은 fixture를 replay해도 추가 fact가 생기지 않는다.
