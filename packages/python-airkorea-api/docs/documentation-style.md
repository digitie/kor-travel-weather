# 문서 작성 규칙

`python-airkorea-api`의 저장소 문서와 Python 내부 문서는 한글을 기본으로 합니다. 코드 식별자와 외부 API 원문처럼 의미가 고정된 값은 원문을 보존합니다.

## 저장소 문서

- Markdown 문서는 한글로 작성합니다.
- 파일 위치를 적을 때는 프로젝트 루트 기준 상대 경로만 씁니다.
- 좋은 예: `src/airkorea/client.py`, `docs/implementation-status.md`
- 로컬 시스템의 드라이브나 홈 디렉터리에서 시작하는 절대 경로는 저장소 문서에 남기지 않습니다.
- 명령어, URL, endpoint, API 필드명, enum 값은 원문을 유지합니다.
- API 동작이나 응답 스키마가 불확실하면 확정된 내용만 쓰고, 확인 필요 상태를 명시합니다.

## Python 내부 문서

- 모듈, 클래스, 함수, 메서드 docstring은 한글로 작성합니다.
- 유지보수자가 읽는 설명 주석도 한글로 작성합니다.
- 타입 이름, public API 이름, endpoint, 응답 필드, 외부 오류 메시지는 원문 그대로 둡니다.
- 주석은 코드가 이미 말하는 내용을 반복하지 말고, AirKorea API의 예외적 동작이나 좌표/인증/응답 shape 같은 맥락을 설명할 때만 추가합니다.

## 함께 갱신할 문서

- API 목록이나 endpoint가 바뀌면 `airkorea-api.md`와 `docs/implementation-status.md`를 함께 갱신합니다.
- 테스트 방침이 바뀌면 `docs/testing.md`를 함께 갱신합니다.
- 반복되기 쉬운 함정이 새로 확인되면 `docs/repeated-mistakes.md`를 함께 갱신합니다.
- 문서 작성 규칙이 바뀌면 `AGENTS.md`, `CONTRIBUTING.md`, 이 문서를 함께 갱신합니다.
