"""원천·관리자 좌표를 바꾸지 않는 최신 주소 갱신."""

from kortravelcommon.address_providers import VworldKakaoAddressResolver
from kortravelcommon.reverse_address import (
    DatabaseAddressSource,
    ReverseAddressStore,
    refresh_reverse_addresses,
)
from sqlalchemy import select

from .repository import WeatherLocationRow


def address_source(repository):
    row = WeatherLocationRow
    return DatabaseAddressSource(
        ReverseAddressStore(repository.engine, VworldKakaoAddressResolver.provider_id),
        select(
            row.location_id.label("target_key"),
            row.longitude,
            row.latitude,
            row.name.label("display_name"),
        ).where(row.enabled.is_(True)),
    )


def configured(settings):
    return bool(settings.vworld_api_key or settings.kakao_rest_api_key)


def address_summary(repository, settings):
    return {
        **address_source(repository).summary(),
        "configured": configured(settings),
        "auto_enabled": settings.address_refresh_auto_enabled,
    }


def run_address_refresh(repository, settings, *, force=False, resolver=None):
    if not configured(settings) and resolver is None:
        return {"status": "not_configured"}
    source = address_source(repository)
    client = resolver or VworldKakaoAddressResolver(
        vworld_key=settings.vworld_api_key.get_secret_value() if settings.vworld_api_key else None,
        kakao_key=settings.kakao_rest_api_key.get_secret_value()
        if settings.kakao_rest_api_key
        else None,
        timeout=settings.address_refresh_timeout_seconds,
    )
    try:
        return refresh_reverse_addresses(
            source.store,
            client,
            source.page,
            source.lookup,
            batch_size=settings.address_refresh_batch_size,
            force=force,
        )
    finally:
        if resolver is None:
            client.close()
