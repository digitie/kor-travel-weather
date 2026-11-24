"""예보 첫 페이지는 If-None-Match가 없어도 head가 같으면 완성 페이지를 재사용한다(호출 검토 A5).

hit마다 새 freeze로 최신 승인·head vector를 확인하므로 "첫 페이지는 바로 반영" 계약은 그대로다.
"""

import pytest
from test_frozen_service_exports import HEADERS, _client, _collect_forecast
from test_frozen_service_exports import repository as repository

from kortravelweather.export_database_artifacts import DatabaseExportArtifactStore
from kortravelweather.export_forecasts import FrozenForecastExports
from kortravelweather.export_heads import ExportHeads

URL = "/v1/service/exports/forecasts?style=ultra_short&limit=1"


def test_unconditional_first_forecast_reuses_page_while_head_vector_is_unchanged(
    repository, tmp_path, monkeypatch,
):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    first = client.get(URL, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.headers["x-export-read"] == "generated"
    freezes = []
    original = FrozenForecastExports._freeze_forecasts

    def counted(self):
        freezes.append(1)
        return original(self)

    def no_payload(*args, **kwargs):
        pytest.fail("같은 head vector의 첫 예보가 artifact 본문을 다시 읽었습니다.")

    monkeypatch.setattr(FrozenForecastExports, "_freeze_forecasts", counted)
    monkeypatch.setattr(DatabaseExportArtifactStore, "_payload", no_payload)
    monkeypatch.setattr(DatabaseExportArtifactStore, "read_many", no_payload)
    # Map처럼 If-None-Match 없이 다시 요청한다.
    again = client.get(URL, headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.headers["x-export-read"] == "reused"
    assert again.headers["etag"] == first.headers["etag"]
    before, after = first.json(), again.json()
    before["meta"].pop("generated_at")
    after["meta"].pop("generated_at")
    assert after == before
    # 승인·head는 이 요청이 새 freeze로 다시 확인했다(첫 페이지 즉시 반영 계약).
    assert freezes == [1]
    assert repository.engine.pool.checkedout() == 0


def test_unconditional_first_forecast_rebuilds_on_head_change_and_reflects_loss_at_once(
    repository, tmp_path,
):
    _collect_forecast(repository, tmp_path)
    client = _client(repository, tmp_path)
    first = client.get(URL, headers=HEADERS)
    assert first.status_code == 200 and first.json()["meta"]["source_status"] == "ok"
    _collect_forecast(repository, tmp_path)  # 새 세대 공개: head vector가 바뀐다.
    rebuilt = client.get(URL, headers=HEADERS)
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.headers["x-export-read"] == "generated"
    assert rebuilt.json()["meta"]["snapshot_id"] != first.json()["meta"]["snapshot_id"]
    assert rebuilt.headers["etag"] != first.headers["etag"]
    with repository.engine.begin() as connection:
        connection.execute(ExportHeads(repository).heads.delete())
    # head 유실은 60초 캐시 뒤로 미루지 않고 다음 첫 페이지에 바로 stale이다.
    lost = client.get(URL, headers=HEADERS)
    assert lost.status_code == 200, lost.text
    assert lost.json()["meta"]["source_status"] == "stale"
    assert lost.headers["x-export-read"] == "generated"
    assert lost.headers["etag"] != rebuilt.headers["etag"]
