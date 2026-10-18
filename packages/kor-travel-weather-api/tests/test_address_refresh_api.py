"""주소 상태는 인증된 관리자만 읽고 provider를 호출하지 않는다."""
from pydantic import SecretStr


def test_address_summary_requires_admin_and_does_not_expose_keys(api_client, monkeypatch):
    from kortravelcommon.address_providers import VworldKakaoAddressResolver

    def no_provider(*args):
        raise AssertionError("상태 조회에서 provider를 호출했습니다.")

    monkeypatch.setattr(VworldKakaoAddressResolver, "reverse", no_provider)
    settings = api_client.app.state.settings
    settings.admin_token = SecretStr("a" * 32)
    settings.vworld_api_key = SecretStr("private-provider-key")
    path = "/v1/admin/address-refresh"
    assert api_client.get(path).status_code == 401
    result = api_client.get(path, headers={"x-admin-token": "a" * 32})
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["refresh_months"] == 6
    assert data["configured"] is True
    assert len(data["items"]) <= 50
    assert "private-provider-key" not in result.text
