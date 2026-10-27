"""TCP 회귀의 준비 실패도 실제 DB·소켓 생성 없이 정리 순서를 검증한다."""

import ast
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url


@pytest.mark.parametrize("failure", ["app", "socket", "bind", "thread", "bind_drop"])
def test_tcp_startup_failure_releases_registered_resources(failure):
    path = (Path(__file__).resolve().parents[1]
            / "packages/kor-travel-weather-api/tests/test_marker_tcp_disconnect.py")
    tree = ast.parse(path.read_text())
    target_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == "test_queued_tcp_disconnect_never_enters_database")
    events = []
    admin, engine = MagicMock(), MagicMock()
    connection = admin.connect.return_value.__enter__.return_value

    def execute(statement):
        events.append("drop" if statement.startswith("DROP") else "create")
        if statement.startswith("DROP") and failure == "bind_drop":
            raise OSError("drop failed")

    connection.exec_driver_sql.side_effect = execute
    admin.dispose.side_effect = lambda: events.append("admin.dispose")
    engine.dispose.side_effect = lambda: events.append("engine.dispose")
    listener = MagicMock()
    listener.close.side_effect = lambda: events.append("listener.close")
    listener.getsockname.return_value = ("127.0.0.1", 12345)
    if failure in {"bind", "bind_drop"}:
        listener.bind.side_effect = OSError("bind failed")
    socket_factory = Mock(return_value=listener)
    if failure == "socket":
        socket_factory.side_effect = OSError("socket failed")
    app_factory = Mock(return_value=object())
    if failure == "app":
        app_factory.side_effect = OSError("app failed")
    worker = Mock()
    worker.ident = None
    worker.start.side_effect = OSError("thread failed")
    server = Mock()
    # 원 함수 AST만 실행해 API module의 기본 app 생성도 하지 않는다.
    # 실제 DB·소켓·서버·thread factory는 이 namespace에 존재하지 않는다.
    namespace = {
        "uuid4": uuid4, "make_url": make_url, "Event": Event, "Lock": Lock,
        "ExitStack": ExitStack, "WeatherSettings": Mock(),
        "create_engine": Mock(side_effect=[admin, engine]), "create_app": app_factory,
        "Thread": Mock(return_value=worker), "socket": SimpleNamespace(socket=socket_factory),
        "uvicorn": SimpleNamespace(Config=Mock(), Server=Mock(return_value=server)),
    }
    exec(compile(ast.Module(body=[target_node], type_ignores=[]), str(path), "exec"), namespace)
    target = namespace[target_node.name]

    message = "drop" if failure == "bind_drop" else failure
    with pytest.raises(OSError, match=message + " failed"):
        target("postgresql+psycopg://fixture@127.0.0.1/fixture")

    expected = ["create"]
    if failure in {"bind", "thread", "bind_drop"}:
        expected.append("listener.close")
        listener.close.assert_called_once()
    else:
        listener.close.assert_not_called()
    assert events == [*expected, "engine.dispose", "drop", "admin.dispose"]
    admin.dispose.assert_called_once()
    engine.dispose.assert_called_once()
    worker.join.assert_not_called()  # 실제로 시작하지 못한 thread에는 join하지 않는다.
    if failure == "thread":
        assert server.should_exit is True
