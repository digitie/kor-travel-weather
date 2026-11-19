# Common 정본 산출물 채택

Pack source: 0e2a3f5aa16323df0d009360802661c9bc496dc6
Catalog/policy: d644632ea9eaf2f6b6baa49305d65fae5bbc0bd4
Publication artifact: https://github.com/digitie/kor-travel-common/actions/artifacts/14500111373

중앙에서 검증한 producer artifact를 그대로 사용한다. 소비자가 재pack하지 않는다.

- @kor-travel/ui 0.2.3: sha512-upv+JtiVcF+zg3C/KcZy9UQckx0n3vc0n4ANr3/elWM8PQ4srljxgTQmaBK4IxulEj8pd8gc7mnG3M67oFIQUQ==; SHA256 `aa19793165ae37b2214d210692e23235545e1464c4e0059f99d943411fa753d9`; 96개 파일
- @kor-travel/tokens 0.1.4: sha512-k6StI02tH/iRxKimczN0btU+MS9AvDZLlR/6Bn6xQHhZ5Mb79pkyIALd4RkoLT2GP3qlwR89YEgOaBiBxACDpA==; SHA256 `33d08d7b37c60d3e319da19608ce05137f6bf587dc8d42e3ce0dfb4b597a61fc`; 25개 파일

Weather가 사용하는 UI·tokens의 정규 파일은 합계 121개다. 사용하지 않는 Common toast는 추가하지 않는다.
기존 메뉴 정본과 Next Link·로그아웃을 유지하면서 공통 AdminShell의 왼쪽 모바일 drawer,
PC 세로 레일·접힘 저장, 한 줄 제목·설명·동작 상단 바를 사용한다.
인증·API·날씨 지도·원천 lineage는 Weather가 계속 소유한다.

Node26.13.1/npm12.0.1 lock과 lifecycle hooks를 유지한다. 검증 결과는 해당 PR에 기록한다.
