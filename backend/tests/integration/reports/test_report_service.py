from uuid import uuid4

import pytest

from app.ai.providers.base import LLMResponse, ModelProviderError, ModelUsage
from app.core.security import OperatorContext
from app.reports.service import ReportService
from app.repositories.executions import ExecutionRepository
from tests.integration.repositories.test_reporting import _execution


class NarrativeProvider:
    async def complete_json(self, request):
        assert request.response_schema
        return LLMResponse(
            output={
                "result": {
                    "summary": "已完成整体治理",
                    "causes": ["联系方式不一致"],
                    "actions": ["更新教师电话"],
                    "outcomes": ["目标数据已验证"],
                    "failures": [],
                    "restore_state": "not_restored",
                }
            },
            provider="test-provider",
            model="analysis-model",
            usage=ModelUsage(input_tokens=12, output_tokens=8),
            request_id="report-request-1",
        )


class FailingProvider:
    async def complete_json(self, request):
        raise ModelProviderError("gateway unavailable")


async def _succeeded_execution(session):
    pair, batch, _root, output = await _execution(session)
    repository = ExecutionRepository(session)
    operation = (await repository.list_operations(batch.id))[0]
    await repository.append_attempt(
        operation.id,
        status="succeeded",
        actual_after={"phone": "<script>alert(1)</script>"},
        verification={"valid": True},
        target_version_id=output.id,
    )
    return pair, batch, output


@pytest.mark.asyncio
async def test_report_reuses_analysis_model_and_persists_provenance(session) -> None:
    pair, batch, _output = await _succeeded_execution(session)
    service = ReportService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id=pair.tenant_id),
        provider=NarrativeProvider(),
        tokenization_secret="report-token-secret-123",
    )

    report = await service.generate(batch.id, idempotency_key="report-1")
    repeated = await service.generate(batch.id, idempotency_key="report-1")

    assert repeated.id == report.id
    assert report.version == 1
    assert report.content["summary"] == "已完成整体治理"
    assert report.provenance["model"] == "analysis-model"
    assert report.provenance["skill_name"] == "generate-governance-report"
    assert report.facts["source_snapshot_id"]
    assert report.facts["target_snapshot_id"]
    assert report.facts["input_target_version_id"]
    assert report.facts["output_target_version_ids"]
    assert report.facts["difference_statistics"]
    assert report.facts["analyses"]
    assert "&lt;script&gt;" in report.html_content
    assert "<script>" not in report.html_content


@pytest.mark.asyncio
async def test_report_falls_back_deterministically_when_model_is_unavailable(session) -> None:
    pair, batch, _output = await _succeeded_execution(session)
    service = ReportService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id=pair.tenant_id),
        provider=FailingProvider(),
        tokenization_secret="report-token-secret-123",
    )

    report = await service.generate(batch.id, idempotency_key=f"report-{uuid4()}")

    assert report.provenance == {"mode": "deterministic_fallback"}
    assert report.content["summary"].startswith("执行批次")


class InvalidRestoreStateProvider(NarrativeProvider):
    async def complete_json(self, request):
        response = await super().complete_json(request)
        response.output["result"]["restore_state"] = "history-was-deleted"
        return response


@pytest.mark.asyncio
async def test_report_rejects_model_invented_restore_state(session) -> None:
    pair, batch, _output = await _succeeded_execution(session)
    service = ReportService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id=pair.tenant_id),
        provider=InvalidRestoreStateProvider(),
        tokenization_secret="report-token-secret-123",
    )

    report = await service.generate(batch.id, idempotency_key=f"report-{uuid4()}")

    assert report.provenance == {"mode": "deterministic_fallback"}
    assert report.content["restore_state"] == "not_restored"


@pytest.mark.asyncio
async def test_confirmed_execution_is_not_reportable(session) -> None:
    pair, batch, _root, _output = await _execution(session)
    service = ReportService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id=pair.tenant_id),
        provider=NarrativeProvider(),
        tokenization_secret="report-token-secret-123",
    )

    with pytest.raises(ValueError, match="not reportable"):
        await service.generate(batch.id, idempotency_key="report-confirmed")
