# Backend package

FastAPI는 sync SQLAlchemy repository를 `run_in_threadpool` 경계 안에서 호출해 event loop를
막지 않는다. provider I/O는 API가 아니라 Dagster가 소유한다. `repository_from_settings()`는
`postgresql://`를 `postgresql+psycopg://`로 정규화하고 PostgreSQL `timestamptz`와
foreign-key/trigger 계약을 사용한다. PostgreSQL 이외의 DSN은 거부한다.
