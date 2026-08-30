# kor-travel-weather Dagster

`python-kma-api`의 `KmaClient`/`DataGoKrClient`를 기존 KMA resource에서 직접
생성한다. 외부 provider는 `ExternalWeatherProviderResource`와
`external_weather_job`을 사용한다. 두 경로 모두 응답을 먼저 stage한 뒤 한
transaction으로 source record와 normalized fact를 publish한다. 빈 응답·격자
불일치·credential 오류는 run을 실패시키며 부분 fact를 publish하지 않는다. MVP에는
durable cursor가 없어 매 실행에서 응답을 재검증하고, raw response idempotency로
중복 저장을 막는다.

중기예보를 켤 때는 target마다 육상 `mid_land_region_code`와 기온
`mid_temperature_region_code`를 함께 지정한다. 두 KMA API의 지역 코드 체계가
다르므로 legacy `mid_region_code`는 호환용으로만 사용한다.

```bash
# Run from the repository root so the shared .env and catalog are used.
cd ../..
uv sync --extra dagster
PYTHONPATH=packages/kor-travel-weather-dagster/src uv run dagster dev -m kortravelweather_dagster.definitions -p 14102
```

n150 운영 Dagster UI는 `https://weather-dagster.digitie.mywire.org`에서 제공한다.
