# Weather 전담 공통 UI 전환 — 통합 후보

## 상태와 소유 경계

정본 clone: `/home/digitie/dev/common-unification-20261111/kor-travel-weather`.
branch `agent/codex-common-unification-20261111`, 시작 HEAD
`b55857f7e40fc4f649e65a57172beafda4af8b62`. 기존 Weather 미커밋 전환 초안과 지도 vendor/CI 변경을 보존해 이어 구현했다.
원본 `/mnt/f/dev`와 다른 소비자·common·upstream 저장소는 수정하지 않았다.

**CANDIDATE_NOT_RELEASED**. UI 후보3의 동일 bytes·원장을 vendor/lock에 반영했다.
최종 source/산출물 freeze, 독립 2인 리뷰·원격 CI·push·PR·merge·배포는 아직 완료하지 않았다.
완료/릴리스 승인 근거로 쓰지 않는다.

## 구체 blocker — 부모 공통 수정 요청

공통 RHF LoginForm 전환 뒤 `defaultUsername="admin"`이 SSR input의 `value="admin"`으로
표시되지 않는다. Weather의 기존 `components/auth/LoginForm.test.ts:20`이 후보3에서도
실패한다. **공통 `packages/ui/src/login-form.tsx` username input에
`defaultValue={defaultUsername}`를 복원하고 회귀 검증 뒤 재pack**하는 것이 요청 API/동작이다.
소비자 monkeypatch나 테스트 약화는 하지 않았다. 입력 disabled/name/autocomplete와 실제
브라우저 로그인은 통과했다. 상위 스레드 메시지 도구가 native ancestor 전송을 거부하여
commentary와 이 보고서에 전달 요청을 남겼다.

`PopoverClose` 부재는 controlled 공통 Popover와 공통 Button의 닫기 callback으로 해결해
새 공통 export가 필수인 blocker는 아니다.

## 구현

- 위치·주소·실행·실행별 원본·시간별 예보의 다섯 로컬 표를 공통 TanStack Table9/Virtual의
  `VirtualTable`로 전환했다. 실행 상세는 `renderRowDetails`로 해당 행 바로 아래 유지한다.
  잘못 유입된 `<tr><th...` 헤더 문자열을 제거하고 안정 domain ID를 사용했다.
- 위치 생성은 실제 RHF+Zod resolver가 문자열 trim·좌표 범위·정수 양수 격자를 검증한다.
  제출 대기·reset·비활성화·페이지 이동을 보존했다. 실행 펼침 상태는 인스턴스별 Zustand다.
- 안내·출처 메뉴는 공통 shadcn/Radix를 소비한다. Base UI와 개별 Radix menu 직접 구현/의존성을
  제거했다. Radix `forceMount`+hidden으로 지도 탭을 보존하고, `--radix-popover-content-available-height`로
  긴 출처 팝오버의 화면 내 스크롤을 복원했다.
- Pretendard variable CSS를 실제 import하며 브라우저 font loaded를 확인한다. Weather의 navy와
  밝은/어두운 의미 토큰 별칭을 유지한다.
- 지도 생성·제거·스타일/카메라·일반 marker 생명주기는 고정 `vworld-map-web`의 실제
  `VWorldMapView`/`Marker`에 맡긴다. 무키 환경은 공개 sentinel+로컬 PNG transformRequest로
  외부 타일 요청 없이 기존 중립 지도/탐색을 제공한다. Weather 전용 지역/묶음·회복 상태는
  어댑터에 남겼으며 최초 load 이전 실패도 보존한다. 라이브러리 코드/산출물은 수정하지 않았다.
- 시간별 64px 열과 그래프 중심을 맞추고 행 머리글 76px를 보존했다. 가로로 낮은 화면에서는
  기온 글자를 본문 안에서 전부 볼 수 있게 제한했다.
- ESLint10.14.0은 지원되는 Next plugin/typescript-eslint/react-x/react-dom/import-x/jsx-a11y-x/Hooks
  조합으로 이관했다. `eslint-config-next`의 구 plugin 묶음과 force/legacy-peer 설치는 사용하지 않는다.
- Doctor0.9.22 CLI help를 확인하고 `--scope full --blocking warning --no-telemetry --no-cache`를
  실제 스크립트/CI gate에 연결했다. 응답 상태 처리·시간대·렌더 중 ref 쓰기·반복 includes를 고치고
  큰 지도 UI를 상태 모델/안내/출처/측정/상세로 분리했다. 배포 worker 복사본만 검사에서 제외한다.
- PostCSS는 Next8.5.23/Tailwind8.5.28/Vite8.5.32 중첩 분기를 관측한 뒤 targets8.5.32 exact
  직접 의존성과 override로 모두 정렬했다. 공통 lucide1.72/MapLibre6.17도 중복 없는 해석을 확인했다.
- common `check-dependencies.mjs`를 npm script와 CI에서 실행한다. `--source app --source components
  --source lib`를 빠뜨리지 않는다. 고정 버전·필수/금지 의존성·표/overlay 중복·bytes/원장/lock을 검사한다.

## 검증과 한계

| gate | 결과 |
| --- | --- |
| npm12.0.1 clean ci (후보3) | PASS, force/legacy 없음 |
| 공통 dependency/version/hash/source guard | PASS |
| 전체 frontend ESLint10 | PASS, 경고0 |
| 전체 frontend 타입/production build | PASS |
| Doctor0.9.22 full / warning 차단 | PASS, 85파일 진단0, 공급망 기본 검사 유지 |
| frontend unit | **303 PASS / 1 FAIL** — 위 공통 SSR 회귀, 전체 gate FAIL |
| 무키 production Chromium UI fixture | 20 PASS, 후보3+PostCSS8.5.32로 전체 재실행49.6초 |
| 공개 더미 타일 build+Chromium | 5 PASS, 후보3+PostCSS로 clean ci 뒤 재실행25.2초, 재조회/pan/zoom 회복·load 전후 지속 실패 |
| Python Ruff | PASS |
| Python PostgreSQL 전체 pytest | NOT_RUN — 공유 시험 DB truncate/DB lifecycle 실행하지 않음 |
| 원격 CI·최종 독립 2인 리뷰·PR/merge | NOT_RUN, 부모 담당 |
| 운영 read-only baseline | PASS: default context, web12205 running, /login·APIhealth/version200 |
| 새 이미지 배포·실제 로그인/대표 화면 smoke | NOT_RUN, 부모 go 이후 |

E2E에서 현재 화면과 맞지 않는 옛 `.metric-card.primary`/`20 °C` 선택자를 현재 날씨 region의
`20°`로 갱신했다. 기존 의미·실패/미제공·초점·해당 컨텐츠 실제 viewport 수용 검사는 유지한다.
타일 fixture는 현재 지역 대표 정책에 맞는 sido/sigungu를 제공하고 zoom animation 완료를 기다린다.
단위의 source AST 추출은 formatter 공백/후행 comma와 무관하게 같은 실제 effect를 실행하도록 보정했다.

실제 운영 VWorld/기상 제공처·native Dagster 지연/실행 예약·DB CRUD 수용으로 fixture 성공을 확대하지 않는다.
모든 브라우저 계정/세션/서버 토큰은 실행마다 random 임시 값이며 로그/문서/commit에 남기지 않는다.

## 후보 산출물

정본 integrity는 frontend `common-artifacts.json`, 아래 SHA256은 리뷰용 교차 검산이다.
후속 재pack이면 vendor·원장·lock과 이 기록을 함께 갱신한다.

| 파일 | SHA256 |
| --- | --- |
| kor-travel-ui-0.2.0.tgz | `f18eeaaf2f26568ec3d56546db66632b9dea9f248ccf60e72339a034baa6b5fe` |
| vworld-map-core-1.0.0.tgz | `0b4c444667a1e25ec2dc2637cc4ef3dab183e4fd436ad2505b60c2f1fc3d3554` |
| kor-travel-tokens-0.1.1.tgz | `00d9f9207a4364e3f0614fdce890c705f2f427ba82e5dc43cdd12265efc63298` |
| vworld-map-web-1.0.3.tgz | `e0cd225431660f27d52ce6af3ab0d53d2c8a21a9e8caaa2c8dd84389d1fa0c8d` |

최신 GitHub 지도 base SHA는 `9c46c57181d433c91f7e8837c4e4396204e9b583`다.
후보 web1.0.3의 build target/package metadata 보정과 최종 immutable upstream commit은 부모 소유다.
이 base SHA만으로 미커밋 후보 metadata까지 freeze됐다고 주장하지 않는다.

## 재현과 다음 단계

`docs/test-strategy.md`에 clean ci → guard/lint/Doctor/type/unit/build → Chromium20/타일5
실행 순서를 적었다. 타일 검사 뒤 `.next`는 더미 식별자 build이므로 운영 배포에 쓰지 않는다.
공통 SSR 수정/최종 pack을 받은 뒤 파일 설치로 integrity 갱신 → 같은 전체 gate를 재실행한다.
이 후보를 부모가 immutable manifest에 묶어 별도 독립 2인 리뷰·CI·개별 PR 머지한 뒤,
[웹 단독 배포 계획](../runbooks/common-ui-web-release-20261111.md)에 따라 부모 go를 받아
web image만 순차 교체한다. DB·RustFS·API/수집기 lifecycle은 대상이 아니다.
