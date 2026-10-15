# Weather Dagster 실패 상세를 선택 시 조회

## 문제와 변경

기존 `getDagsterSnapshot`은 overview 응답 뒤 실패 실행마다 상세 이벤트를
`Promise.all`로 자동 조회했다. Common UI에서 상세를 숨겨도 데이터 조회는
발생했다. 실패6개를 반환한 로컬 원본 재현에서 초기 요청은 실제7회였다.
이 추가 부하는 운영에서 관측한 GraphQL10초 Abort의 원인 후보지만,
단독 원인으로 확정하거나 CPU 절감률을 측정한 것은 아니다.

초기 snapshot은 overview1회만 조회한다. 실행 행은 실패 여부와
“실패 원인 미조회”를 구분해 표시한다. 사용자가 실행을 선택하면 해당
실행 하나만 조회하며, 성공 실행은 오류 로그를 자동 조회하지 않는다.
Common의 showRunDetails/onSelectRun/selectedRunId/renderRunDetail을
재사용하며 Common 제품·vendor는 변경하지 않았다.

상세는 한page씩 읽고 마지막 실패 요약만 보존한다. 기존 page당10초와
최대20page를 유지하고, caller 취소와 전체20초 예산을 결합한다.
20page 상한은 incomplete로 표시하고, 전체20초 경과는 상세 오류로
표시한다. 이벤트 없음·미조회·상세 조회 실패를 같은 null로 숨기지 않는다.
HTTP/GraphQL 오류나 scoped run 부재도 상세 오류로 명시한다.

선택 변경·새로고침·unmount는 이전 caller 요청을 취소하고 버전을 무효화한다.
응답이 취소를 무시하고 늦게 와도 현재 선택/새 snapshot을 덮지 못한다.
상세 오류는 정상 overview나 실행 목록을 지우지 않으며, 선택 실행만
다시 조회할 수 있다. 동시에 같은 실행을 반복 클릭해도 pending 요청은
하나만 유지한다.

기존 named operation/proxy allowlist/location scope/auth는 그대로다.
일반 fetch 재시도나 mutation을 추가하지 않았다. 기존 server proxy의
backend fetch는 별도10초 제한이므로 browser 취소가 backend 계산까지
강제로 중단한다고 주장하지 않는다. 이번 개선은 불필요한 요청을 처음부터
생성하지 않고 현재 UI의 요청/응답 수명을 분리하는 것이다.

## 검증

- OLD RED: 원본a7+새 초기조회 회귀1개에서 실제7회가 발생해1회 기대 실패.
  나머지18개 선택 제외는 실패 건수에 더하지 않았다.
- NEW GREEN: frontend 전체140개 PASS, 그중 Dagster26개.
- 초기 overview1회, 선택run1개만 요청, scope 유지, 상세 빈 결과/오류/불완전
  구분,20page/20초/caller 취소 상한을 검증했다.
- 실제 React와 설치 Common UI를 사용한 로컬 비공개 DOM 모형13조건 PASS:
  선택·중복선택·선택변경취소·늦은응답차단·상세오류와목록격리·선택상세재조회·
  refresh취소/응답차단·unmount취소를 포함한다. 운영/browser/network 호출0.
- type-check, lint, production build PASS.

제품 변경은 page/lib/test/이 문서뿐이며 Python/API DTO/OpenAPI 변경은 없다.
별도 BFF 위치GET 복구 commit66fa는 이 패치에 포함하지 않는다.
독립 코드리뷰·이 commit의 실제 운영 배포/live E2E·PR merge는 후속 단계다.
