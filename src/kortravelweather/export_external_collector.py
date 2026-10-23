"""기존 외부 원천 ingest 앞뒤에 붙는 명시 opt-in 승인 adapter."""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from .export_collector import KmaNowcastExportCollector
from .export_external import RetrievalScopeDescriptor
from .export_receipts import ExportAttempt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedExternalExport:
    identity: ExportAttempt
    descriptor: RetrievalScopeDescriptor


class ExternalExportCollector(KmaNowcastExportCollector):
    def prepare(self, *, location, provider, dataset_key):
        identity = None
        try:
            descriptor = RetrievalScopeDescriptor(
                provider=provider,
                dataset_key=dataset_key,
                forecast_style="short" if dataset_key.endswith("forecast") else "observed",
                entity_id=location.location_id,
                location_ids=(location.location_id,),
                latitude=location.latitude,
                longitude=location.longitude,
                normalizer_revision=f"{provider}-v1",
            )
            runs = self.heads.metadata.tables["weather_sync_runs"]
            with self.heads._transaction() as session:
                orchestrator = session.scalar(
                    select(runs.c.orchestrator_run_id).where(runs.c.run_id == self.run_id)
                )
            identity = self.repository.begin_export_attempt(
                run_id=self.run_id,
                scope_key=descriptor.namespace,
                planned_sources=[descriptor.source_scope],
                execution_mode="dagster" if orchestrator else "native",
            )
            self.heads.lifecycle.claim(identity)
            self.heads.declare(identity, descriptor)
            return PreparedExternalExport(identity, descriptor)
        except Exception:
            logger.warning("external_export_prepare_failed run_id=%s", self.run_id)
            if identity is not None:
                self._reject(identity, "PREPARE_FAILED")
            return None

    def _bind_source(self, prepared, record):
        self.heads.bind_external_response(prepared.identity, record)
