# Test strategy

- domain/provider: camel/snake fixture, 범위·NaN·미만 qualifier, 중기 fan-out
- repository: PostgreSQL timezone/FK/trigger, immutable replay/revision, migration
- API: envelope/request id, auth, disabled location, pagination·temporal validation,
  public metadata/raw 비노출
- Dagster: grid dedupe, wrong grid/empty/quota, retry, atomic no-partial publish,
  DB target merge와 run→source lineage
- frontend: `npm run type-check`, `npm run build`, proxy token 비노출

root smoke는 PostgreSQL test database를 띄운 뒤
`TMPDIR=/tmp uv run --extra dev --extra dagster pytest -q`로 실행한다.
OpenAPI drift는 `scripts/export_openapi.py` 결과가 checked-in 문서와 동일한지
검증한다.

## 2026-11-11 frontend 공통 전환 gate

frontend에서 다음 순서로 확인한다. CI도 같은 검사와 Chromium 설치를 수행한다.

```bash
npx --yes npm@12.0.1 ci --ignore-scripts --no-audit --no-fund
npm run check:dependencies
npm run lint
npm run doctor
npm run type-check
npm test
npm run build
npx playwright install --with-deps chromium
npm run test:e2e:fixtures
npm run test:e2e:tiles
```

일반 fixture 20건은 매 실행 임의 포트·메모리 인증 API·랜덤 일회용 계정/세션 값을 사용해
production Next 서버에서 실행한다. 운영 API·계정·DB를 쓰지 않는다. 지도 타일 fixture 5건은
공개 더미 식별자로 **별도 build**한 뒤 타일 HTTP를 전부 가로채 load 전/후 지속 실패,
재조회·pan·zoom 회복을 검증한다. `test:e2e:tiles` 뒤 `.next`는 fixture build이므로 배포에
재사용하지 않는다. 배포는 merged source에서 실제 공개 설정으로 새 이미지를 빌드한다.
두 실행의 screenshot/trace는 frontend `test-results/`에 생성되며 저장소에 commit하지 않는다.

Doctor는 경고도 실패한다. 배포 라이브러리에서 prebuild가 복사한 `public/maplibre/**`만
소유 소스가 아니어서 제외한다. 공급망 검사는 기본값을 유지하고 telemetry/score 전송만 끈다.
소스의 제한적 예외는 GET 내부 POST-only 로컬 Uint8Array 복사와 외부 카탈로그/지도 요청 큐의
생명주기 동기화이며 각각 코드에 근거를 적었다. 오류·보안 진단을 전역 해제하지 않는다.
기존 Compiler 비도입 설정은 유지하며 실제 Hook 순서·의존성 규칙은 활성화한다.

백엔드 PostgreSQL 회귀는 기존 Python CI의 격리 Postgres service에서 수행한다. 이번
프런트 변경을 검증하려고 소유권이 불명확한 공유 시험 DB를 truncate하거나 DB lifecycle을
시작하지 않는다. 원격 CI/독립 리뷰/배포 결과는 해당 실행 전에는 NOT_RUN으로 기록한다.
