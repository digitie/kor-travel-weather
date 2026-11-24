"""공개 읽기 한 요청의 조회를 연결·transaction 하나로 묶는다(호출 검토 B9).

예전에는 handler 하나가 repository 읽기 함수마다 새 세션을 열었다. 함수마다 pool checkout과
``pool_pre_ping``(``SELECT 1``) 왕복, 새 transaction의 마감 설정 왕복이 따로 들었다.
:func:`shared_read` 안에서는 첫 읽기가 연 연결과 transaction을 같은 요청의 다음 읽기가 이어
쓴다. 연결은 처음 필요할 때 열고(읽기가 없으면 열지 않음) 묶음이 끝나면 rollback으로 닫는다.

- 읽기 전용이다. ``sessionmaker.begin()``(쓰기)은 묶음 안에서도 자기 연결과 transaction을 쓴다.
- READ COMMITTED이므로 문장마다 새 snapshot을 보는 것은 예전과 같다. 달라지는 것은 묶음이
  끝날 때까지 읽은 테이블의 ACCESS SHARE lock을 쥐는 것뿐이다(공개 읽기 마감 15초 안).
- 격리 수준을 따로 쓰는 읽기(``region_locations``의 REPEATABLE READ)는 이 묶음을 쓰지 않는다.
- 같은 thread 안에서만 이어진다. 다른 worker thread로 넘긴 읽기는 자기 연결을 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker


class _SharedRead:
    __slots__ = ("_connection", "engine")

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._connection: Connection | None = None

    def connection(self) -> Connection:
        if self._connection is None:
            connection = self.engine.connect()
            try:
                # 세션들이 이 transaction에 참여만 하고(commit·close 안 함) 묶음이 끝낸다.
                connection.begin()
            except BaseException:
                connection.close()
                raise
            self._connection = connection
        return self._connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()


_SHARED: ContextVar[_SharedRead | None] = ContextVar("weather_shared_read", default=None)


@contextmanager
def shared_read(engine: Engine) -> Iterator[None]:
    """이 블록 안의 ``engine`` 읽기가 연결·transaction 하나를 함께 쓴다. 중첩하면 바깥 것을 쓴다."""
    current = _SHARED.get()
    if current is not None and current.engine is engine:
        yield
        return
    shared = _SharedRead(engine)
    token = _SHARED.set(shared)
    try:
        yield
    finally:
        _SHARED.reset(token)
        shared.close()


def shared_connection(engine: Any) -> Connection | None:
    """``engine``의 공유 읽기 묶음 안이면 그 연결, 아니면 None."""
    current = _SHARED.get()
    if current is None or current.engine is not engine:
        return None
    return current.connection()


@contextmanager
def read_connection(engine: Engine) -> Iterator[Connection]:
    """읽기용 Core 연결. 공유 읽기 묶음 안이면 그 연결을 쓰고 닫지 않는다."""
    shared = shared_connection(engine)
    if shared is not None:
        yield shared
        return
    with engine.connect() as connection:
        yield connection


class ReadScopedSessionmaker(sessionmaker[Session]):
    """``sessionmaker``와 같되 공유 읽기 묶음 안의 ``Session()``은 그 연결에 묶는다.

    외부 연결에 묶인 세션은 이미 열린 transaction에 참여만 한다(SQLAlchemy 기본
    ``join_transaction_mode``). ``close()``는 연결을 닫지 않고 transaction도 끝내지 않는다.
    """

    def __call__(self, **local_kw: Any) -> Session:
        if "bind" not in local_kw:
            shared = shared_connection(self.kw.get("bind"))
            if shared is not None:
                local_kw["bind"] = shared
        return super().__call__(**local_kw)

    @contextmanager
    def begin(self) -> Iterator[Session]:  # type: ignore[override]
        # 쓰기 transaction은 공유 읽기 연결에 섞지 않는다. commit이 읽기 묶음을 끝내면 안 된다.
        with sessionmaker.__call__(self) as session, session.begin():
            yield session
