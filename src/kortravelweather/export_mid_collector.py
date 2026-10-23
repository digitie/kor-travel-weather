"""중기 지역 원천 하나의 사전 승인 계획. native 저장 의미는 바꾸지 않는다."""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from .export_collector import KmaNowcastExportCollector
from .export_mid import MidScopeDescriptor
from .export_receipts import ExportAttempt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedMidExport:
    identity: ExportAttempt
    descriptor: MidScopeDescriptor


class MidExportCollector(KmaNowcastExportCollector):
    def prepare(self, *, descriptor: MidScopeDescriptor):
        identity = None
        try:
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
            return PreparedMidExport(identity, descriptor)
        except Exception:
            logger.warning("mid_export_prepare_deferred run_id=%s", self.run_id)
            if identity is not None:
                self._reject(identity, "PREPARE_FAILED")
            return None

    def _bind_source(self, prepared, record):
        self.heads.bind_mid_response(prepared.identity, record)
