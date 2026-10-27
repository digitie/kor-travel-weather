"""실제 Uvicorn/TCP에서 대기 중인 GET 연결 끊김이 새 DB 작업을 만들지 않는다."""

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event, Lock, Thread
from uuid import uuid4

import httpx
import uvicorn
from kortravelweather_api.app import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from kortravelweather.models import WeatherLocation
from kortravelweather.settings import WeatherSettings


def test_queued_tcp_disconnect_never_enters_database(weather_test_database_url):
    with ExitStack() as cleanup:
        database = "marker_tcp_" + uuid4().hex
        admin = create_engine(weather_test_database_url, isolation_level="AUTOCOMMIT")
        cleanup.callback(admin.dispose)

        def drop_database():
            with admin.connect() as connection:
                connection.exec_driver_sql('DROP DATABASE "' + database + '"')

        with admin.connect() as connection:
            connection.exec_driver_sql('CREATE DATABASE "' + database + '"')
            cleanup.callback(drop_database)
        url = make_url(weather_test_database_url).set(database=database)
        engine = create_engine(url)
        cleanup.callback(engine.dispose)
        gate, started = Event(), Event()
        lock = Lock()
        counts = {"calls": 0, "active": 0}

        class Repository:
            def create_schema(self):
                pass

            def get_locations_by_ids(self, ids, **kwargs):
                return [WeatherLocation(location_id=key, name=key, latitude=37, longitude=127)
                        for key in ids]

            def marker_values_many(self, ids, **kwargs):
                with lock:
                    counts["calls"] += 1
                    counts["active"] += 1
                try:
                    # 실제 자기 UUID DB에 진입한 앞 두 요청만 gate에서 대기한다.
                    with engine.connect() as connection:
                        assert connection.execute(text("SELECT 1")).scalar_one() == 1
                    if counts["active"] == 2:
                        started.set()
                    assert gate.wait(10), "TCP fixture gate 해제 실패"
                    return {key: [] for key in ids}
                finally:
                    with lock:
                        counts["active"] -= 1

        app = create_app(WeatherSettings(env="development", database_url=str(url)), Repository())
        listener = socket.socket()
        cleanup.callback(listener.close)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
                                             lifespan="off"))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)

        def stop_server():
            gate.set()
            server.should_exit = True
            if worker.ident is not None:
                worker.join(timeout=10)

        cleanup.callback(stop_server)
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert worker.is_alive() and time.monotonic() < deadline
            time.sleep(.01)
        with (
            httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            front = [pool.submit(client.get, "/v1/weather/markers",
                                 params={"location_id": key}) for key in ("one", "two")]
            try:
                assert started.wait(5)
                with socket.create_connection(("127.0.0.1", port), timeout=3) as cancelled:
                    query = "location_id=cancelled" + "".join("&kind=" + kind for kind in
                        ("observed", "nowcast", "ultra_short", "short", "mid", "alert"))
                    cancelled.sendall(("GET /v1/weather/markers?" + query
                        + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n").encode())
                    deadline = time.monotonic() + 3
                    while app.state.marker_limiter.statistics().tasks_waiting != 1:
                        assert time.monotonic() < deadline
                        time.sleep(.01)
                    cancelled.shutdown(socket.SHUT_RDWR)
                gate.set()
                assert all(response.result(timeout=5).status_code == 200 for response in front)
                deadline = time.monotonic() + 3
                while (app.state.marker_limiter.borrowed_tokens
                       or app.state.marker_limiter.statistics().tasks_waiting):
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                assert counts == {"calls": 2, "active": 0}
            finally:
                gate.set()
    assert not worker.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
