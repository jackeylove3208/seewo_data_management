from typing import Any

from app.schemas.differences import DifferenceItem


def read_difference_context(difference: DifferenceItem) -> dict[str, Any]:
    return difference.model_dump(mode="json")
