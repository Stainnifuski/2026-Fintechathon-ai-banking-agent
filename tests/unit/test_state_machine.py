"""状态机领域规则测试。"""

import pytest

from backend.app.domain.state_machine import (
    InvalidTransition,
    OperationStatus,
    TaskPhase,
    ensure_operation_transition,
    project_task_phase,
    yuan_to_fen,
)


def test_money_uses_integer_fen() -> None:
    assert yuan_to_fen(100.01) == 10001
    with pytest.raises(ValueError):
        yuan_to_fen(1.001)


def test_terminal_operation_cannot_reopen() -> None:
    with pytest.raises(InvalidTransition):
        ensure_operation_transition(OperationStatus.SUCCEEDED, OperationStatus.PROCESSING)


def test_unknown_has_reconciliation_priority() -> None:
    assert (
        project_task_phase(
            [OperationStatus.UNKNOWN],
            control_mode="AUTO",
            ready=True,
        )
        == TaskPhase.RECONCILING
    )


def test_settled_task_is_finished() -> None:
    assert (
        project_task_phase(
            [OperationStatus.SUCCEEDED],
            control_mode="AUTO",
            settled=True,
        )
        == TaskPhase.FINISHED
    )
