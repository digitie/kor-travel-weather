# Weather 조사·설계 문서 검토 기록

대상: [조사](../research/2026-10-25-weather-ux.md), [설계](../design/2026-10-25-weather-map-improvements.md),
작업/현재 상태·아키텍처의 관련 링크. Transport와의 전체 정합·독립 리뷰 정본은
[공동 검토 기록](https://github.com/digitie/kor-travel-transport/blob/main/docs/reports/2026-10-25-mobility-design-review.md)이다.
기준 소스 Weather `d4e5aa2`, Map `c06345841`, Common 참조 `294e0dfc`.

## 이번 문서의 게이트

사용자 지시대로 문서를 두 차례 정리하고 독립 적대적 리뷰어 1명이 양쪽 저장소를 검토한다.
CI 결과를 기다리지 않고 문서 PR을 머지한다. 이 예외는 후속 기능·운영 PR에 적용하지 않는다.

## 1차 정리

소스에 이미 있는 overlay·종류/출처 API·현재/미래 분리와 후속 변경을 구별했다.
운영 로그인 이후 치수·버전은 미측정, 앱은 공식 정적 화면·설명·후기 조사임을 표시했다.
아키텍처 문서의 과거 OSM/목록 overlay 설명을 현재 VWorld/선택 상세 구조에 맞췄다.

## 2차 정리

Transport와 C-MAP-01·W-UX 작업 ID·선행 관계·가용 높이/치수·테마·API 의미·미구현 상태를 대조했다.
신규 보고서의 로컬·교차 저장소 링크 대상을 확인했고 `git diff --check`가 통과했다.
양쪽 각각 Markdown 7개만 변경되었으며 코드/설정/잠금·운영 파일 변경은 없다. 외부 자료의 확인 수준·조회 실패는 조사에 남겼다.

## 독립 적대적 리뷰와 머지

Popper 1명이 양쪽 문서를 읽기 전용 검토했고 P0/P1 없이 Weather P2 1건을 지적했다.
즐겨찾기/DB 등록의 의미와 미등록 목적지 이름→좌표→저장 anchor 연결·provider/kind·부재 수용이 모호한 문제다.
현재 nearby/resolve의 필터 부재와 역지오코딩 경계를 확인하고 조사·설계·W-UX-03 및 수용 시나리오를 보완했다.
기존 Geo 계약 검증/지도 지점 대안·대표성/미제공·자동등록/추가 수집 없음도 명시했다.
동일 리뷰어가 Weather `abc05abe271188559c1255970691a5cc83c9c2ce`/Transport `10f37d8073a8b038498f3b7ef0e0f00a0fe5c29b`를
재검토해 **PASS, 기존 P2 해소·잔여 P0/P1/P2 없음**을 확인했다. 이후 수정은 결과·원장·인수인계 정산뿐이다.

문서 Draft PR: [Weather #168](https://github.com/digitie/kor-travel-weather/pull/168),
[공동 Transport #130](https://github.com/digitie/kor-travel-transport/pull/130).
최종 머지 상태·SHA는 두 PR의 GitHub 메타데이터가 정본이다. 사용자 지시대로 이번 문서 PR은 CI 결과를 기다리지 않는다.

신규 문서의 상대 링크 6개·교차 저장소 링크 3개 대상 존재와 양쪽 문서 전용 범위를 확인했다.
최종 정산은 양쪽 각각 Markdown 8개이며 `git diff --check`를 통과했다. 외부 참조 전체의 실시간 가용성 검사는 아니다.

애플리케이션 테스트·빌드·Docker·운영 배포는 실행하지 않는다. 문서의 수용 시나리오는 후속 구현 목표다.
