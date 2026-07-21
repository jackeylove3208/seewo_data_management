from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMProvider, ModelProviderError
from app.core.security import OperatorContext
from app.models.reporting import GovernanceReportRecord
from app.reports.facts import ExecutionFactCollector, facts_hash
from app.reports.narrative import ReportNarrativeGenerator, deterministic_report
from app.reports.renderer import HtmlReportRenderer
from app.repositories.reporting import ReportingRepository
from app.schemas.reporting import ExecutionFactBundle


class ReportService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        provider: LLMProvider,
        tokenization_secret: str | None,
    ) -> None:
        self.operator = operator
        self.repository = ReportingRepository(session)
        self.collector = ExecutionFactCollector(session, operator=operator)
        self.narrative = ReportNarrativeGenerator(
            provider,
            tokenization_secret=tokenization_secret,
        )
        self.renderer = HtmlReportRenderer()

    async def generate(
        self,
        execution_id: UUID,
        *,
        idempotency_key: str,
    ) -> GovernanceReportRecord:
        facts = await self.collector.collect(execution_id)
        digest = facts_hash(facts)
        job = await self.repository.start_report(
            execution_id=execution_id,
            tenant_id=self.operator.tenant_id,
            idempotency_key=idempotency_key,
            requested_by=self.operator.operator_id,
            facts=facts.model_dump(mode="json"),
            facts_hash=digest,
        )
        existing = await self.repository.get_report_for_job(job.id)
        if existing is not None:
            return existing
        fixed_facts = ExecutionFactBundle.model_validate(job.facts)
        try:
            content, provenance = await self.narrative.generate(
                fixed_facts,
                tenant_id=self.operator.tenant_id,
            )
        except (ModelProviderError, ValidationError, ValueError):
            content = deterministic_report(fixed_facts)
            provenance = {"mode": "deterministic_fallback"}
        html, html_hash = self.renderer.render(fixed_facts, content, job.version)
        return await self.repository.finish_report(
            job,
            facts=fixed_facts.model_dump(mode="json"),
            facts_hash=job.facts_hash,
            content=content.model_dump(mode="json"),
            html_content=html,
            html_hash=html_hash,
            provenance=provenance,
            generated_by=self.operator.operator_id,
        )
