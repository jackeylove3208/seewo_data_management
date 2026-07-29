from app.agent_reporting import service


def test_rollback_terminal_state_accepts_writes_and_already_restored_rows() -> None:
    facts = {
        "mutations": [
            {"id": "write", "status": "succeeded"},
            {"id": "no-write", "status": "already_restored"},
        ]
    }

    assert service.rollback_terminal_state(facts) == "completed"


def test_rollback_terminal_state_exposes_conflict_skips() -> None:
    facts = {
        "mutations": [
            {"id": "restored", "status": "already_restored"},
            {"id": "conflict", "status": "conflict_skipped"},
        ]
    }

    assert service.rollback_terminal_state(facts) == "completed_with_conflicts"
