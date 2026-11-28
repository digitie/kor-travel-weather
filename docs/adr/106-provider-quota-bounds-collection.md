# ADR-106: 외부 provider 수집 범위는 무료 quota 안에서 정한다

- 상태: 채택
- 결정일: 2026-09-12

## 맥락

외부 provider(openweathermap, weatherapi, open_meteo 등)는 KMA의 보조 비교군이다. 무료 한도를
넘겨 전 지점을 매시간 훑자 vendor throttle로 요청당 0.3초가 15초가 됐다. 한 sweep이 6.6시간
걸려 run 큐를 채웠고, 그동안 국내 원천(해수욕장·산악·고속도로)이 19시간 갱신되지 못했다.

## 결정

- 외부 provider의 수집 범위는 각 vendor의 공개 무료 한도에서 20% 마진을 뺀 값 안에서 정한다.
  유료 전환이나 한도 초과를 전제로 범위를 넓히지 않는다.
- 위치 수를 줄일 때는 id 순으로 자르지 않고 전국에 고르게 표본을 남긴다.
- 월 한도 외에 분당 한도도 요청 간 최소 간격으로 지킨다.

## 검토한 대안

- 전 지점 매시간 수집: throttle이 run 큐를 막아 KMA·국내 원천까지 늦춘다.
- 유료 plan: 보조 비교군에 비용을 쓸 이유가 없다.
- id 순 상위 N개: 특정 지역만 남아 비교군의 의미가 사라진다.

## 결과

- 외부 provider 값은 일부 위치와 3시간 주기로만 있다. 소비자는 외부 값이 모든 위치에 있다고
  가정할 수 없다.
- 구체 값은 설정 `KOR_TRAVEL_WEATHER_PROVIDER_LOCATION_CAPS`,
  `KOR_TRAVEL_WEATHER_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS`와
  [`../architecture/dagster-boundary.md`](../architecture/dagster-boundary.md)가 정본이다.
