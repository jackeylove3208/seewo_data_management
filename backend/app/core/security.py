from pydantic import BaseModel, ConfigDict, Field


class OperatorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=128)
