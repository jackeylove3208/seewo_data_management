from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.ai.analysis_service import AnalysisService
from app.ai.providers.base import LLMResponse, ModelUsage
from app.api.routes import execution_batches as execution_batch_routes
from app.core.config import Settings
from app.core.security import OperatorContext
from app.main import create_app
from app.models.executions import GovernancePlanExplanationRecord, TargetVersionRecord
from app.repositories.executions import ExecutionRepository
from app.schemas.differences import DifferenceType
from app.schemas.governance import RecommendedAction
from tests.integration.ai.test_analysis_service import AgentSpy, seed_difference
from tests.integration.api.test_proposals import seeded


@pytest.fixture
def execution_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'execution-preview.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
        tokenization_secret=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _confirmed_ai_proposal(client: TestClient) -> tuple[str, dict[str, object]]:
    task_id, difference_id, _target_hash = seeded(client)
    analysis = client.get(f"/api/differences/{difference_id}/analysis").json()
    request = {
        "analysis_id": analysis["id"],
        "option_id": analysis["output"]["options"][0]["option_id"],
        "expected_difference_version": 1,
    }
    response = client.post(
        f"/api/differences/{difference_id}/proposals/from-analysis",
        json=request,
    )
    assert response.status_code == 201, response.text
    return str(task_id), response.json()


def _preview(client: TestClient) -> dict[str, object]:
    task_id, proposal = _confirmed_ai_proposal(client)
    response = client.post(
        "/api/execution-batches/preview",
        json={
            "task_id": task_id,
            "proposals": [
                {
                    "proposal_id": proposal["id"],
                    "proposal_version": proposal["proposal_version"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_binds_exact_current_proposal_and_confirmation_identity(
    execution_client: TestClient,
) -> None:
    task_id, proposal = _confirmed_ai_proposal(execution_client)
    preview = execution_client.post(
        "/api/execution-batches/preview",
        json={
            "task_id": task_id,
            "proposals": [
                {
                    "proposal_id": proposal["id"],
                    "proposal_version": proposal["proposal_version"],
                }
            ],
        },
    )

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["counts"] == {
        "create": 1,
        "update": 0,
        "move": 0,
        "disable": 0,
        "skip": 0,
    }
    assert body["proposal_sources"] == {"ai": 1, "operator": 0}
    assert body["operations"][0]["proposal"]["proposal_id"] == proposal["id"]
    assert body["operations"][0]["after"]["source_id"] == "t-a"

    spoofed = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "preview-confirm-1"},
        json={
            "plan_id": body["plan_id"],
            "plan_version": body["plan_version"],
            "high_risk_acknowledged": False,
            "operator_id": "spoofed-operator",
        },
    )
    assert spoofed.status_code == 422

    confirmed = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "preview-confirm-1"},
        json={
            "plan_id": body["plan_id"],
            "plan_version": body["plan_version"],
            "high_risk_acknowledged": False,
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["confirmed_by"] == "demo-operator"
    replay = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "preview-confirm-1"},
        json={
            "plan_id": body["plan_id"],
            "plan_version": body["plan_version"],
            "high_risk_acknowledged": False,
        },
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == confirmed.json()["id"]


def test_confirmation_rejects_a_superseded_proposal_version(
    execution_client: TestClient,
) -> None:
    task_id, proposal = _confirmed_ai_proposal(execution_client)
    preview = execution_client.post(
        "/api/execution-batches/preview",
        json={
            "task_id": task_id,
            "proposals": [
                {
                    "proposal_id": proposal["id"],
                    "proposal_version": proposal["proposal_version"],
                }
            ],
        },
    ).json()
    revised = execution_client.post(
        f"/api/differences/{proposal['difference_id']}/proposals/manual",
        json={
            "expected_difference_version": 1,
            "operation_type": "create",
            "target_entity_id": None,
            "changes": {"phone": "13700000000"},
            "rationale": "The operator verified this phone through the school directory",
        },
    )
    assert revised.status_code == 201, revised.text

    response = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "stale-proposal-confirm"},
        json={
            "plan_id": preview["plan_id"],
            "plan_version": preview["plan_version"],
        },
    )

    assert response.status_code == 409
    conflicts = response.json()["detail"]["conflicts"]
    assert conflicts[0]["code"] == "proposal_version_drift"


def test_explanation_failure_does_not_block_confirmation(
    execution_client: TestClient,
) -> None:
    preview = _preview(execution_client)

    explanation = execution_client.post(f"/api/governance-plans/{preview['plan_id']}/explanation")

    assert explanation.status_code == 503
    assert explanation.json()["state"] == "unavailable"
    confirmed = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "explanation-unavailable"},
        json={
            "plan_id": preview["plan_id"],
            "plan_version": preview["plan_version"],
        },
    )
    assert confirmed.status_code == 202, confirmed.text


def test_successful_explanation_is_tokenized_and_persisted_separately(
    execution_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderSpy:
        def __init__(self) -> None:
            self.messages: tuple[str, ...] = ()

        async def complete_json(self, request):
            self.messages = tuple(message.content for message in request.messages)
            return LLMResponse(
                output={
                    "result": {
                        "summary": "One reviewed create operation is ready",
                        "risk_explanation": "The deterministic policy assigned medium risk",
                        "attention_points": ["Review the derived target version"],
                    }
                },
                provider="provider-spy",
                model="explanation-model",
                usage=ModelUsage(input_tokens=11, output_tokens=7),
                request_id="explanation-request-1",
            )

    provider = ProviderSpy()
    execution_client.app.state.settings.tokenization_secret = SecretStr(
        "explanation-tokenization-secret"
    )
    monkeypatch.setattr(
        execution_batch_routes,
        "HttpLLMProvider",
        lambda **_values: provider,
    )
    preview = _preview(execution_client)

    response = execution_client.post(f"/api/governance-plans/{preview['plan_id']}/explanation")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "available"
    assert response.json()["provider"] == "provider-spy"
    assert all("张三" not in message for message in provider.messages)
    assert all("13700000000" not in message for message in provider.messages)

    async def persisted_count() -> int:
        async with execution_client.app.state.database.session_factory() as session:
            records = tuple(
                await session.scalars(
                    select(GovernancePlanExplanationRecord).where(
                        GovernancePlanExplanationRecord.plan_id == UUID(str(preview["plan_id"]))
                    )
                )
            )
            assert records[0].request_id == "explanation-request-1"
            assert records[0].usage == {"input_tokens": 11, "output_tokens": 7}
            return len(records)

    assert execution_client.portal is not None
    assert execution_client.portal.call(persisted_count) == 1


def test_high_risk_plan_requires_explicit_acknowledgement(
    execution_client: TestClient,
) -> None:
    async def seed_move() -> tuple[str, str, str]:
        async with execution_client.app.state.database.session_factory() as session:
            difference = await seed_difference(session, DifferenceType.STRUCTURE_CONFLICT)
            analysis = await AnalysisService(
                session,
                agent=AgentSpy(action=RecommendedAction.MOVE),
                operator=OperatorContext(
                    operator_id="demo-operator",
                    tenant_id="school-1",
                ),
            ).analyze(difference.id)
            await session.commit()
            assert analysis.output is not None
            return str(difference.task_id), str(difference.id), str(analysis.id)

    assert execution_client.portal is not None
    task_id, difference_id, analysis_id = execution_client.portal.call(seed_move)
    proposal = execution_client.post(
        f"/api/differences/{difference_id}/proposals/from-analysis",
        json={
            "analysis_id": analysis_id,
            "option_id": "option-1",
            "expected_difference_version": 1,
        },
    )
    assert proposal.status_code == 201, proposal.text
    preview = execution_client.post(
        "/api/execution-batches/preview",
        json={
            "task_id": task_id,
            "proposals": [
                {
                    "proposal_id": proposal.json()["id"],
                    "proposal_version": 1,
                }
            ],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["high_risk"] is True

    rejected = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "high-risk-rejected"},
        json={
            "plan_id": preview.json()["plan_id"],
            "plan_version": 1,
        },
    )
    assert rejected.status_code == 409
    accepted = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "high-risk-accepted"},
        json={
            "plan_id": preview.json()["plan_id"],
            "plan_version": 1,
            "high_risk_acknowledged": True,
        },
    )
    assert accepted.status_code == 202, accepted.text


def test_confirmation_rejects_target_version_drift(
    execution_client: TestClient,
) -> None:
    preview = _preview(execution_client)

    async def add_new_target_version() -> None:
        async with execution_client.app.state.database.session_factory() as session:
            current = await session.scalar(
                select(TargetVersionRecord).where(
                    TargetVersionRecord.id == UUID(str(preview["input_target_version_id"]))
                )
            )
            assert current is not None
            await ExecutionRepository(session).create_target_version(
                task_id=current.task_id,
                tenant_id=current.tenant_id,
                source_snapshot_id=current.source_snapshot_id,
                parent_version_id=current.id,
                batch_id=None,
                file_sha256="d" * 64,
                content_hash="e" * 64,
                storage_path=Path(f"/tmp/{uuid4()}.csv"),
            )
            await session.commit()

    assert execution_client.portal is not None
    execution_client.portal.call(add_new_target_version)
    response = execution_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "target-drift"},
        json={
            "plan_id": preview["plan_id"],
            "plan_version": preview["plan_version"],
        },
    )

    assert response.status_code == 409
    codes = {item["code"] for item in response.json()["detail"]["conflicts"]}
    assert "target_version_drift" in codes


def test_confirmation_rehashes_the_current_target_file(
    execution_client: TestClient,
) -> None:
    preview = _preview(execution_client)

    async def target_path() -> Path:
        async with execution_client.app.state.database.session_factory() as session:
            version = await session.get(
                TargetVersionRecord,
                UUID(str(preview["input_target_version_id"])),
            )
            assert version is not None
            return Path(version.storage_path)

    assert execution_client.portal is not None
    path = execution_client.portal.call(target_path)
    path.write_text("id,name\nchanged,Changed\n", encoding="utf-8")
    try:
        response = execution_client.post(
            "/api/execution-batches",
            headers={"Idempotency-Key": "physical-target-drift"},
            json={
                "plan_id": preview["plan_id"],
                "plan_version": preview["plan_version"],
            },
        )
    finally:
        path.unlink(missing_ok=True)

    assert response.status_code == 409
    codes = {item["code"] for item in response.json()["detail"]["conflicts"]}
    assert "target_version_drift" in codes
