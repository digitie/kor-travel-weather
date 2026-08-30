from __future__ import annotations

from datetime import datetime, timezone

from airkorea.metadata import (
    is_credential_param,
    load_env_value,
    load_service_key,
    make_cache_key,
    make_call_context,
    normalize_service_key,
    sanitize_request_params,
)


def test_sanitize_request_params_removes_common_credential_keys() -> None:
    params = {
        "serviceKey": "secret",
        "ServiceKey": "secret2",
        "auth_key": "secret3",
        "pageNo": 1,
        "nested": {"api_key": "secret4", "numOfRows": 10},
    }

    sanitized = sanitize_request_params(params)

    assert is_credential_param("serviceKey")
    assert sanitized == {"pageNo": 1, "nested": {"numOfRows": 10}}


def test_make_call_context_is_sanitized_and_timestamped() -> None:
    collected_at = datetime(2026, 5, 7, tzinfo=timezone.utc)

    context = make_call_context(
        service_name="MsrstnInfoInqireSvc",
        endpoint="getMsrstnList",
        request_params={"serviceKey": "secret", "stationName": "Jongno-gu"},
        collected_at=collected_at,
    )

    assert context.provider == "data.go.kr"
    assert context.service_name == "MsrstnInfoInqireSvc"
    assert context.endpoint == "getMsrstnList"
    assert context.request_params == {"stationName": "Jongno-gu"}
    assert context.collected_at == collected_at


def test_make_cache_key_is_stable_and_ignores_credentials() -> None:
    first = make_cache_key(
        "getMsrstnList",
        {"serviceKey": "secret", "addr": "Seoul"},
        service_name="MsrstnInfoInqireSvc",
    )
    second = make_cache_key(
        "getMsrstnList",
        {"serviceKey": "different", "addr": "Seoul"},
        service_name="MsrstnInfoInqireSvc",
    )

    assert first == second
    assert first.startswith("airkorea:v1:")


def test_load_service_key_prefers_environment_and_strips_whitespace(
    monkeypatch,
    tmp_path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text('DATA_GO_KR_SERVICE_KEY=" file-key "\n', encoding="utf-8")
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", " env-\nkey ")

    assert load_service_key(dotenv_path=dotenv) == "env-key"


def test_load_service_key_reads_local_dotenv_when_environment_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\ufeffOTHER=value\nexport DATA_GO_KR_SERVICE_KEY=' local key '\n",
        encoding="utf-8",
    )

    assert load_env_value("DATA_GO_KR_SERVICE_KEY") == " local key "
    assert load_service_key() == "localkey"
    assert normalize_service_key(" key\r\nwith spaces\t") == "keywithspaces"
