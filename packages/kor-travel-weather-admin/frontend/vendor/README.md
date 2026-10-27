# 공용 UI 고정 산출물

토큰 원천: [kor-travel-common](https://github.com/digitie/kor-travel-common/tree/50d2db4c7c7e0154dd863defb62d4b9fa3b5bec3), commit `50d2db4c7c7e0154dd863defb62d4b9fa3b5bec3`.
`npm run build` 후 `npm pack ./packages/tokens`, `npm pack ./packages/ui`로 생성했다.
각 archive의 GPL LICENSE·NOTICE·THIRD_PARTY_NOTICES와 package metadata를 유지한다.
registry 발행 대신 이 개발 후보를 고정하며 추후 이관 시 버전과 lock을 함께 갱신한다.

| 산출물 | SHA-256 |
|---|---|
| kor-travel-tokens-0.1.0.tgz | `22b7613085e55885987ce05720a178b65ac649b097763db650b8c7b8b4acaa84` |
| kor-travel-ui-0.1.0-dev.6.tgz | `e4945d01d9eb89ed505a95b551899fd0ecf41be66c9ee6b76246701350447e6d` |

UI dev.6는 Common `091d4fe1dda8eacf43b453ad39b4f2e42e9e80fc`에서 `npm pack`으로 만들었다.
UI 소스는 main `efcd671207d7f9e2fb01251bab6854a8d543279a`와 동일하다.
실행 검색·상태 필터·50행 페이지·상세를 포함하며 인증과 API는 소비 앱이 소유한다.

## 공용 지도 작업면 dev.7 (2026-10-24)

Common source `e79816afb3c19b1b1e2fc1486b809c2ee3d34d6e`. `kor-travel-ui-0.1.0-dev.7.tgz` SHA256 `4445c694013bd19fb57d3655cfc25d86e99a6733d655524718be3bf769ba7422`. Map/Transport/Weather 동일 산출물이다. 기존 후보는 보존한다. [검증](../../../../docs/reports/2026-10-24-shared-map-ui.md).

## 남은 높이·필터 접기·상세 슬롯 dev.8 (2026-10-25)

Common 제품 source `a74ac808752fd6d6062e529909fd0dd656724f08`의 `git archive`에서 만든
`kor-travel-ui-0.1.0-dev.8.tgz`를 고정한다. SHA256은
`71d9f6ce8c178e3c66a47593a70a7fd9274938587ef9d96c1c7023673370059f`다.
격리 Docker의 fresh `npm ci`·type/build·UI76·tokens7 검증 pack과 preview의 byte 일치를 확인했다.
문서만 정산한 Common head `8cb0e51cac650e757dc9fefc45fa8c42e1ab12ef`와 제품/lock 차이는 없다.
`MapWorkspace`·`MapFilterPanel`·`MapStage`와 `MapDetailOverlay`의 header/body·접기를 소비하며,
공용 canvas/overlay 순서·양방향 필터 초점·boolean 빈 슬롯 보강을 포함한다.
API·기상 종류·선택·인증은 Weather가 소유한다. dev.6/dev.7은 이력으로 보존한다.

## 로그인 hydration 준비 경계 dev.9 (2026-10-27)

Common main `aa232ad5f255233184de80eceaab2e9b6ccb00c9`(PR #47, T-217)의 `git archive`를 격리 Docker
(Node 22.22.2·npm 11.19.1)에서 fresh `npm ci`·build·check·UI79 시험 뒤 `npm pack -w packages/ui`로 만든
`kor-travel-ui-0.1.0-dev.9.tgz`를 고정한다. SHA256은
`b738d8824e36eb6e58d8cc6b78af7dc31233b6763248d634286044def795b403`(27파일)이며 Common T-217 기록·Transport 채택
산출물과 byte 단위로 같다. tokens는 `0.1.0` 그대로다(같은 source의 새 pack은 내용이 같고 파일 mode만 달라 교체하지 않는다).
`LoginForm`은 SSR·최초 hydration 동안 아이디·비밀번호·제출을 native disabled로 두고 client handler가 준비된 뒤 연다.
준비 전 제출은 callback 없이 막는다. 공개 props·슬롯·인증 정책은 같아 Weather `components/auth/LoginForm.tsx`는
바뀌지 않으며, 그 서버 렌더가 세 슬롯을 닫는지는 `components/auth/LoginForm.test.ts`가 고정한다.
기존 로그인 e2e는 `fill()`/`click()`이 활성화를 기다리는 Playwright 동작상 그대로일 것으로 보지만 CI는 Playwright를
돌리지 않으므로 시험 결과가 아니다. hydration 전에는 Weather 전역 `button:disabled`(opacity 0.55) 때문에 제출 버튼이 잠시
흐리게 보이고, 브라우저 자동완성은 비활성 입력을 채우지 않을 수 있어 저장된 계정을 목록에서 고르게 될 수 있다.
JS가 로드되지 않으면 폼이 계속 비활성이다. dev.6~dev.8은 이력으로 보존한다.
