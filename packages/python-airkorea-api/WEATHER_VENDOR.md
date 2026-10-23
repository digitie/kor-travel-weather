# Weather vendor 출처

- 원본: https://github.com/digitie/python-airkorea-api
- 반영 revision: `8a43798de7c1d5b93a99ddb2dec173f58e0b6802` (PR #11)
- 라이선스: 동봉 `LICENSE`, GPL-3.0-or-later. 원본과 내용 동일.
- `src/airkorea`의 Python 15개 파일은 위 revision과 동일하다.
- Weather의 path dependency와 Docker COPY가 이 디렉터리를 사용한다.

공개 `parse_measurement`는 기존 HTTP client와 같은 단일 행 parser이다.
전국 목록/페이지 완결성, 누락 없음, 조회 시각은 이 함수가 증명하지 않는다.
