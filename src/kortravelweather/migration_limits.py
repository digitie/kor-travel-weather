"""alembic migration 연결의 기본 lock 대기 상한(2026-11-24 Map 호출 검토 L7).

migration의 ``ALTER TABLE``·``CREATE INDEX``가 열린 수집·읽기 transaction 뒤에서 lock을 기다리는
동안에는 뒤에 온 지도 읽기도 그 요청 뒤에 줄을 선다. 기본 상한이 없으면 그 대기가 끝없이 이어진다.
그래서 migration 연결은 시작 옵션(``-c lock_timeout=...``)으로 기본 상한을 갖는다.

시작 옵션은 연결의 기본값(reset 값)이 되므로 migration 자신의 설정이 늘 우선한다.

- ``SET LOCAL lock_timeout``·``set_config(..., true)``는 그 transaction에만 덮어쓴다.
- ``SET lock_timeout``(0012의 ``SET lock_timeout = 0`` 포함)은 그 연결의 남은 migration에 덮어쓴다.
- ``SET LOCAL lock_timeout TO DEFAULT``(0017)·``RESET``은 이 기본값으로 돌아간다.
- 0022·0032~0043처럼 "현재 값과 상한 중 더 엄격한 값"을 고르는 migration은 이 기본값이 더
  엄격하면 그대로 둔다(그 migration들의 문서화된 규칙이다).

``CREATE INDEX CONCURRENTLY``처럼 더 오래 기다려야 하는 새 migration은 자기 transaction이나
autocommit 블록에서 명시적으로 늘린다. 재시도 헬퍼는 migration마다 다르게 두고 표준화하지 않는다
(``docs/write-path-bounds.md``).
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

#: migration 연결의 기본 ``lock_timeout``. Map 호출 검토 권고값.
MIGRATION_LOCK_TIMEOUT = "5s"


def migration_connect_args(database_url: str) -> dict[str, str]:
    """migration engine의 ``connect_args``: 기본 ``lock_timeout``을 시작 옵션으로 준다.

    URL에 이미 ``options``가 있으면 그 앞에 붙인다. 같은 설정을 여러 번 주면 뒤의 것이 이기므로
    운영자가 URL로 준 ``lock_timeout``이 이 기본값보다 우선한다.
    """
    default = f"-c lock_timeout={MIGRATION_LOCK_TIMEOUT}"
    given = make_url(database_url).query.get("options")
    if isinstance(given, tuple):
        given = " ".join(given)
    return {"options": f"{default} {given}" if given else default}
