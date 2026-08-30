# Test strategy

- domain/provider: camel/snake fixture, 범위·NaN·미만 qualifier, 중기 fan-out
- repository: SQLite timezone/FK/trigger, immutable replay/revision, migration
- API: envelope/request id, auth, disabled location, pagination·temporal validation,
  public metadata/raw 비노출
- Dagster: grid dedupe, wrong grid/empty/quota, retry, atomic no-partial publish,
  DB target merge와 run→source lineage
- frontend: `npm run type-check`, `npm run build`, proxy token 비노출

root smoke는 `TMPDIR=/tmp uv run --extra dev --extra dagster pytest -q`로 실행한다.
OpenAPI drift는 `scripts/export_openapi.py` 결과가 checked-in 문서와 동일한지
검증한다.
