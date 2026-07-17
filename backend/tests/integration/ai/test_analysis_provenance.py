import pytest

from app.ai.agent import GovernanceAgent
from app.ai.analysis_service import AnalysisService
from app.ai.mcp.server import MCPToolGateway
from app.repositories.analyses import AnalysisRepository
from app.schemas.differences import DifferenceType
from tests.fixtures.model_outputs import model_response, valid_attribute_analysis
from tests.integration.ai.test_analysis_service import seed_difference


class SuccessfulModel:
    async def complete_json(self, _request):
        return model_response(valid_attribute_analysis())


@pytest.mark.asyncio
async def test_historical_analysis_returns_persisted_model_provenance(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    service = AnalysisService(
        session,
        agent=GovernanceAgent(SuccessfulModel(), MCPToolGateway(session)),
    )

    generated = await service.analyze(difference.id)
    historical = await AnalysisRepository(session).get_for_difference(
        difference.id, difference.version
    )

    assert historical == generated
    assert historical is not None
    assert historical.provenance.provider == "stub-provider"
    assert historical.provenance.model == "stub-model-v1"
    assert historical.provenance.skill_version == "1.0.0"
    assert historical.provenance.prompt_version == "analysis-prompt-v1"
    assert historical.provenance.usage.input_tokens == 7
    assert historical.provenance.generated_at == generated.provenance.generated_at
