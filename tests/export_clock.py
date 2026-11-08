"""상태 캐시의 허용 60초와 갱신 경계를 실제 대기·캐시 삭제 없이 진행한다."""

from time import monotonic

from kortravelweather import export_snapshots


def install_api_clock(monkeypatch, client):
    clock = [monotonic()]
    monkeypatch.setattr(export_snapshots, "monotonic", lambda: clock[0])
    client.app.state.export_selection_cache.clock = lambda: clock[0]
    return clock
