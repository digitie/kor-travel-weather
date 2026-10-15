# 공통 Dagster UI 최신화

운영 live UI 검사에서 weather의 실행 검색 입력이 없어 검색·빈 결과·초기화 검증이 실패했다.
Geo에서 사용하는 공통 UI dev.6를 동일하게 설치해 검색, 페이지 이동, 실행 상세와 제한된 행 렌더링을 공유한다.
소비 저장소에 공통 컴포넌트의 복사본을 만들지 않는다.

- Common 소스: `091d4fe1dda8eacf43b453ad39b4f2e42e9e80fc` (UI 내용은 main `efcd671207d7f9e2fb01251bab6854a8d543279a`와 같다).
- vendor: `kor-travel-ui-0.1.0-dev.6.tgz`
- SHA-256: `e4945d01d9eb89ed505a95b551899fd0ecf41be66c9ee6b76246701350447e6d`
- API·인증 계약 변경 없음. 실행 이력 조회 한도와 공통 UI 페이지 크기를 유지해 전체 행을 DOM에 쌓지 않는다.
- Geo 자원·배치·동시성 튜닝값을 변경하지 않는다.

## 검증 상태

- 변경 전 live UI: 전체 79 PASS / 5 FAIL. 이 앱의 실행 검색 부재가 실제 실패 중 하나다.
- 로컬 관리자 UI: type-check·lint PASS, 단위 테스트 65 PASS, production build PASS. 51개 실행 중 첫 페이지 50행만 렌더링하는 vendor 계약도 통과했다.
- Python 전체 검사: 진행 중. CI·독립 적대적 리뷰 2인·운영 배포·live E2E/UI: NOT_RUN.
- 운영 조회의 명시적 화면 이동으로 취소된 요청과 안정 화면의 실제 오류를 구분한 뒤 모든 필수 사례를 재검증한다.
- 실패 결과를 통과로 바꾸거나 검색 검사를 제외하지 않는다. 최종 검증 뒤 이 문서를 갱신한다.
