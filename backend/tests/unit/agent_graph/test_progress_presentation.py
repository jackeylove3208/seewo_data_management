from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.routes.agent import _graph_action_label, _graph_human_gate_view


@pytest.mark.parametrize(
    ("node", "label"),
    [
        ("load_verified_mutations", "正在读取执行事实并比对当前目标数据"),
        ("assess_restore_impact", "正在判定可恢复、已恢复与冲突操作"),
        ("wait_restore_conflicts", "正在等待处理回滚数据冲突"),
        ("compile_restore_plan", "正在冻结回滚计划与数据比较哈希"),
        (
            "preflight_restore",
            "正在准备逐项回滚，每项写入前都会重新校验",
        ),
        ("verify_restore_operations", "正在进入回滚结果汇总"),
    ],
)
def test_every_rollback_graph_step_has_a_specific_operator_label(
    node: str,
    label: str,
) -> None:
    assert _graph_action_label(node) == label


@pytest.mark.parametrize(
    ("kind", "node", "summary", "reason"),
    [
        (
            "rollback_conflict",
            "wait_restore_conflicts",
            "检测到 2 条同步后数据已被修改",
            "这些操作涉及的当前数据已不再等于同步后的值",
        ),
        (
            "rollback_approval",
            "wait_rollback_approval",
            "确认执行 2 条回滚操作",
            "执行前仍会重新读取并校验当前目标数据",
        ),
    ],
)
def test_rollback_gates_explain_conflicts_and_final_confirmation(
    kind: str,
    node: str,
    summary: str,
    reason: str,
) -> None:
    graph = SimpleNamespace(cursor=4, current_node=node)
    run = SimpleNamespace(status="waiting_human")
    gate = SimpleNamespace(
        id=uuid4(),
        gate_kind=kind,
        status="pending",
        member_ids=(str(uuid4()), str(uuid4())),
        cursor=3,
        decision=None,
    )

    view = _graph_human_gate_view(  # type: ignore[arg-type]
        gate,
        graph=graph,  # type: ignore[arg-type]
        run=run,  # type: ignore[arg-type]
    )

    assert view.summary_zh == summary
    assert view.risk_reason_zh is not None
    assert reason in view.risk_reason_zh
