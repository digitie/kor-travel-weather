"""기존 SDK 관측망 수집 전의 canonical roster와 승인 계획만 준비한다."""

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import select

from .export_artifacts import ExportArtifactError
from .export_heads import ExportHeads
from .export_inventory_selection import SDK_NETWORKS, catalog_source_columns
from .export_sdk_collector import SdkExportCollector, sdk_descriptor
from .export_source_cohorts import declare_source_cohort

logger = logging.getLogger(__name__)


@dataclass
class SdkCollectionContext:
    collector: SdkExportCollector
    plans: dict = field(default_factory=dict)
    ignored_ids: set = field(default_factory=set)
    complete: bool = True

    def invalidate(self, reason="invalidated"):
        """원천 완결 미확인은 capture만 막고 기존 native 이력 수집은 유지한다."""
        if self.complete:
            logger.warning("sdk_collection_coverage_unconfirmed reason=%s", reason)
        self.complete = False
        self.finish(reason)

    def finish(self, reason="not_in_response"):
        """반환되지 않은 source 계획을 영구 preparing queue로 남기지 않는다.

        ``reason``은 정해진 짧은 분류만 받는다. 거절 사유로 attempt에 함께 남긴다.
        """
        for prepared in self.plans.values():
            if prepared is not None:
                self.collector._reject(
                    prepared.identity, "SOURCE_COVERAGE_UNCONFIRMED", reason=reason
                )
        self.plans.clear()

    def observe_krex_page(self, page):
        from .export_sdk_pages import SdkPageReceipt

        try:
            raw = page.raw["list"]
            if not isinstance(raw, list) or len(raw) != len(page.items):
                self.invalidate("partial_parse")
                return
            receipt = SdkPageReceipt()
            if page.page_no is None and page.num_of_rows is None:
                # restWeatherList는 페이지 없이 count와 전체 list만 준다. 그 전체 개수는
                # HTTP 전에 고정한 roster(활성+비활성 catalog) 크기와도 같아야 한다.
                receipt.add_snapshot(page, raw_count=len(raw))
                if receipt.total != len(self.plans) + len(self.ignored_ids):
                    self.invalidate("roster_mismatch")
                    return
            else:
                receipt.add(page, raw_count=len(raw))
            if not receipt.complete:
                self.invalidate("page_incomplete")
        except (ValueError, TypeError, KeyError, AttributeError, ExportArtifactError):
            self.invalidate("page_receipt")


def prepare_sdk_collection(repository, *, run_id, provider, dataset_key, artifact_root):
    heads = ExportHeads(repository)
    context = SdkCollectionContext(SdkExportCollector(
        repository, run_id=run_id, artifact_root=artifact_root,
    ))
    try:
        catalog = heads.metadata.tables["weather_locations"]
        # arbitrary metadata를 가져오지 않는다. 각 projected 필드에 byte/길이 상한이 있다.
        with heads._transaction() as s:
            rows = s.execute(select(
                catalog.c.location_id, catalog.c.latitude, catalog.c.longitude, catalog.c.enabled,
                *catalog_source_columns(catalog),
            ).where(
                catalog.c.metadata["measurement_point"]["provider"].as_string() == provider,
                catalog.c.metadata["measurement_point"]["network"].as_string()
                == SDK_NETWORKS[dataset_key],
            ).order_by(catalog.c.location_id).limit(5001)).mappings().all()
        if not 1 <= len(rows) <= 5000:
            raise ValueError("SDK 최초 catalog bootstrap 또는 상한 확인이 필요합니다.")
        context.ignored_ids = {row["location_id"] for row in rows if row["enabled"] is False}
        rows = [row for row in rows if row["enabled"]]
        locations = [SimpleNamespace(
            location_id=row["location_id"], latitude=row["latitude"], longitude=row["longitude"],
            metadata={"measurement_point": {
                key.removeprefix("point_"): value for key, value in row.items()
                if key.startswith("point_")
            }},
        ) for row in rows]
        declare_source_cohort(
            repository, run_id=run_id, provider=provider, dataset_key=dataset_key,
            descriptors=[sdk_descriptor(location, provider=provider, dataset_key=dataset_key)
                         for location in locations],
        )
        context.plans = {location.location_id: context.collector.prepare(
            location=location, provider=provider, dataset_key=dataset_key,
        ) for location in locations}
    except Exception as exc:
        from .export_collector import failure_reason

        logger.warning(
            "sdk_collection_prepare_deferred run_id=%s reason=%s", run_id, failure_reason(exc)
        )
        context.invalidate("prepare")
    return context
