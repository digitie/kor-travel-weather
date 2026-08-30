# Backend package

FastAPI는 sync SQLAlchemy repository를 `run_in_threadpool` 경계 안에서 호출해 event loop를
막지 않는다. provider I/O는 API가 아니라 Dagster가 소유한다. `repository_from_settings()`는
`postgresql://`를 `postgresql+psycopg://`로 정규화하고 SQLite 개발 DB에는 foreign-key
pragma와 timezone type decorator를 적용한다.
