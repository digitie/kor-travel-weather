# Common 정본 산출물 채택

## 2026-11-28 — 공통 UI 0.2.8·Tokens 0.1.9

공식 산출물의 소스는 `e131a1efb195f618561fd6ca00833007ddc2721b`이고, 정책과 SHA256·SHA512 SRI는 머지된 카탈로그에서 확인한다. [Common 0.2.8 카탈로그](https://github.com/digitie/kor-travel-common/blob/73761c5d0a14e3df409e5b1c86f954eb219dae66/docs/evidence/releases/collection-pipeline-ui-0.2.8-catalog.md)

생산 이미지 검증은 공용 패키지 6개의 272개 파일을 대조한다.

## 이전 (UI 0.2.5·Tokens 0.1.6)

Pack source는 `fe141143cc8ba75e8fd0b6134e1c9e518e294fa2`이며 중앙 원장은 Common PR #82의
[0.2.5 산출물 카탈로그](https://github.com/digitie/kor-travel-common/blob/ade7b3eb50c0197caad4a1c98fcb97cffbc2a8a4/docs/evidence/releases/field-focus-ui-0.2.5-catalog.md)다.
중앙 CI 산출물 `14782962353`의 원본 bytes를 사용한다.

- @kor-travel/tokens `0.1.6` `sha512-y9uEIo9dTG/XlohG+kQs9goL10tsEVgHLwUA81XMskts1oZJaim+7UBsG773l/Q1W54MUOFWF1BLsmlFNjGrAA==`
- @kor-travel/ui `0.2.5` `sha512-GKICXj4/I4pZQHvbrLy4qL7Bbtn757soe7g8M9phZrNXOsSXtU5bFhharNAWpjD6/qXxSyVWtfTxrZqsgAk2hA==`

입력·선택·탭의 공용 키보드 포커스 스타일과 토큰을 갱신한다.
생산 이미지 검증은 원장의 SRI와 설치된 공용 6패키지 268개 파일을 비교한다.

## 이전 (UI 0.2.3·tokens 0.1.4)

Pack source: 0e2a3f5aa16323df0d009360802661c9bc496dc6 (PR70), catalog/policy: d644632ea9eaf2f6b6baa49305d65fae5bbc0bd4 (PR71)
Publication: https://github.com/digitie/kor-travel-common/actions/runs/44459847805 (artifact `common-release-packs-0e2a3f5aa16323df0d009360802661c9bc496dc6`)

- @kor-travel/tokens 0.1.4 sha512-k6StI02tH/iRxKimczN0btU+MS9AvDZLlR/6Bn6xQHhZ5Mb79pkyIALd4RkoLT2GP3qlwR89YEgOaBiBxACDpA==
- @kor-travel/ui 0.2.3 sha512-upv+JtiVcF+zg3C/KcZy9UQckx0n3vc0n4ANr3/elWM8PQ4srljxgTQmaBK4IxulEj8pd8gc7mnG3M67oFIQUQ==

관리자 셸(AdminShell·AdminTopBar)과 버튼 focus outline 2px 정본을 위해 갱신했다. 의존성 목록은 0.2.2와 같아 lock은 두 항목의
version·resolved·integrity와 peer tokens만 바뀐다. 0.2.2·0.1.3 tgz는 이력으로 남긴다. toast는 쓰지 않아 원장에서 뺀다.

## 이전(0.2.2·0.1.3)

Pack source: 706aaa7821588dfca72f660c9161f56be2d0445f
Catalog/policy: 5c1b3857e9af060d30ec776157760fb9e9c13ded
Publication: https://github.com/digitie/kor-travel-common/actions/runs/44313659808

- @kor-travel/tokens 0.1.3 sha512-m1WsuJw1DHYxAkPsJpAIrFN5P/Udb+Ln/sZHbrmPJpItQDHx+wuf8QEFgSrBsk5yW+NAX5CQnIQZRkPIFwO97A==
- @kor-travel/ui 0.2.2 sha512-/odxLZlFlMj0IB8oPMyjE77kj1MtZO46m/lyHiCTW/gYFq7o6XUSrSPOC7rm7cyEpbjD8dAnCqcz7zmE2GWF+Q==

기존 의존성만 갱신했다. npm lock 재생성과 소비자 guard 검증은 별도로 수행한다.

## 원본 파일 검증 범위

UI 0.2.3은 96개, tokens 0.1.4는 25개 정규 파일로 합계 121개다.
기존 나머지 pack을 포함한 전체 C11 inventory는 268개이며 SRI·per-pack 검사를 유지한다.
중앙 producer artifact `14500111373`의 원본 bytes를 사용하고 소비자가 재pack하지 않는다.
