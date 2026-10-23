# 공개 SDK 원문 재해석 기반 export

## 현재 단계

AirKorea·산림청 산악/청정넷·KREX 휴게소·KHOA 해수욕장의 기존 SDK parser와
기존 Weather 정규화를 함께 재사용하는 순수 검증 경계를 구현한다.
단일 원문의 수치/시각/관측소 증명이며, 전국 응답/페이지 완결 증명이 아니다.
아직 실제 수집 배선·누적 source inventory·전체 제공처 기본 조회·KMA 중기예보는
이 단계의 완료 범위에 포함하지 않는다. 운영 활성화/배포도 하지 않는다.

## 정책

- SDK parser를 Weather에 복제하지 않는다. 공개 parser가 해석한 DTO를 기존 Weather
  adapter에 전달해 저장 raw로부터 독립적인 기대 값을 만든다.
- 사전 선언한 관측소 코드/이름/좌표와 비교한다. 청정넷은 좌표가 별도 승인 catalog에만
  있으므로 측정 원문에서 좌표가 검증됐다고 주장하지 않는다.
- 실제 관측 시각이 없는 원문은 승인하지 않는다. 기존 native 수집의 결측 처리 정책은
  바꾸지 않는다. 관측 원문은 조회 시각 이전 36시간까지만 허용한다.
- KHOA 원문 장소/좌표, 날짜·오전/오후 중복, 가까운 미래 범위를 검증한다.
- 발표 revision이 없는 원천은 `latest-retrieval-v1`이다. 최신 발표 보장은 하지 않는다.
- 유효값이 없는 원문, 비정상 수치, 위조 source key는 export를 승인하지 않는다.
- KREX HTTP client는 일부 파싱 실패행을 제외하고 정상행을 반환할 수 있다. typed DTO
  재해석 성공을 전국 완결 성공으로 사용하지 않는다. pre-HTTP cohort와 페이지/원문
  개수 증거를 별도로 연결하고 미확인 응답은 이전 승인 집합을 stale로 유지해야 한다.

SDK pin은 AirKorea `8a43798`, KHOA `9c952bb`, KREX `0fc9da9`다.
AirKorea는 path dependency이므로 vendor 실제 소스를 함께 갱신했다.
원본 라이선스는 줄바꿈을 정규화해 동일함을 확인했으며 provenance는 vendor 문서에 남긴다.

## 검증

공개 parser→기존 adapter 동등성, raw 불변, 5종 source key/naive 시각 거부,
4종 관측 시각 결측/오래된 값 거부, 잘못된 anchor, KHOA 중복/미래 없음 등
집중 테스트 21개가 통과했다. 최종 CI·두 독립 리뷰·실제 HTTP/UI 수용은 후속 통합 뒤 수행한다.
