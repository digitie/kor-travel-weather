# Common 정본 산출물 채택

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
