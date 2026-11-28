# kor-travel-weather 작업 규칙

## 범위

이 저장소는 기상 데이터 수집·정규화·제공만 소유한다. PinVi 사용자/여행계획,
`kor-travel-map`의 장소 CRUD, provider client 자체의 구현은 이 저장소에 넣지 않는다.

## 식별자 정본

| 항목 | 값 |
| --- | --- |
| distribution | `kor-travel-weather` |
| Python import | `kortravelweather` |
| API package | `kor-travel-weather-api` |
| Dagster package | `kor-travel-weather-dagster` |
| env prefix | `KOR_TRAVEL_WEATHER_` |
| provider | `python-kma-api` |

## 개발 규칙

- `main`에 직접 push하지 않고 feature branch와 PR을 사용한다.
- KMA 격자/발표 시각을 재구현하지 않는다. `python-kma-api`의 public API를 직접 호출한다.
- 원천 필드와 raw payload를 weather fact에 남기고, `source_record_key`로 lineage를 추적한다.
- 모든 날짜/시간은 timezone-aware KST로 입력받고 저장 시 UTC 변환이 가능한 형태를 유지한다.
- 동일 identity를 재수집해도 결과가 하나만 남도록 unique key와 upsert를 함께 유지한다.
- admin write endpoint는 token이 설정된 환경에서 fail-closed하고, 삭제 대신 비활성화를 우선한다.
- API DTO를 변경하면 OpenAPI export와 TypeScript client 타입을 같이 갱신한다.
- 문서·주석은 한국어를 기본으로 하되 코드 식별자, URL, provider 원문은 그대로 둔다.

## 변경 후 확인

```bash
uv run ruff check .
uv run pytest -q
cd packages/kor-travel-weather-admin/frontend
npm run check:dependencies && npm run lint && npm run doctor
npm run type-check && npm test && npm run build
```

PR은 초안으로 먼저 만들고, 기능 단위로 작은 커밋을 남긴다. 원본 저장소의 로컬
미커밋 변경은 복사 대상이 아니며 되돌리지 않는다.

프론트엔드 도구는 루트 .nvmrc의 Node26.13.1과 private anchor의 npm12.0.1을 사용한다.
설치는 npm ci --engine-strict와 기존 lifecycle 옵션을 유지한다. 경고·빈 테스트 성공 예외를 추가하지 않는다.

## 이미지 빌드 캐시

- 반복 빌드는 기존 persistent builder와 필요 시 외부 `--cache-from`/`--cache-to`를 사용한다.
- GDAL·Python·npm 등 의존성 cache는 단순 source 변경이나 나이만을 이유로 삭제하지 않는다.
- 공간 정리는 정확한 own unused 앱 산출물부터 수행한다. cache 적중을 실제 로그로 확인한다.
- 캐시와 관계없이 source/lock/runtime 검증을 유지한다. 세부 명령은 docs/architecture/weather-normal-build-producer.md를 따른다.
