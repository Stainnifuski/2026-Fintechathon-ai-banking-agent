"""防止5号任务编排再次越层导入模拟银行账本实现。"""

import ast
from pathlib import Path


def test_orchestration_does_not_import_bank_orm_or_implementation() -> None:
    root = Path(__file__).resolve().parents[2]
    protected_files = [
        root / "backend/app/services/orchestration/task_service.py",
        root / "backend/app/routers/tasks.py",
    ]
    forbidden_modules = {
        "backend.app.models.account",
        "backend.app.models.transaction",
        "backend.app.models.user",
        "backend.app.services.bank",
    }
    forbidden_names = {"AccountORM", "TransactionORM", "UserORM", "BankOperationORM"}
    forbidden_import_fragments = (
        "models.account",
        "models.transaction",
        "models.user",
        "services.bank",
    )

    for path in protected_files:
        source = path.read_text(encoding="utf-8")
        assert not any(fragment in source for fragment in forbidden_import_fragments), path
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported_modules.add(module)
                imported_names.update(alias.name for alias in node.names)

        assert forbidden_modules.isdisjoint(imported_modules), path
        assert forbidden_names.isdisjoint(imported_names), path


def test_only_bank_package_imports_bank_owned_orms() -> None:
    """所有生产代码中，只有4号实现包可以直接接触银行 ORM。"""
    root = Path(__file__).resolve().parents[2]
    app_root = root / "backend/app"
    bank_root = app_root / "services/bank"
    models_root = app_root / "models"
    forbidden_names = {
        "AccountORM", "BankOperationORM", "ContactORM", "PaymentRequestORM",
        "PaymentRequestPaymentORM", "SubscriptionORM",
        "TransactionORM", "UserORM",
    }

    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        if path.is_relative_to(bank_root) or path.is_relative_to(models_root):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                leaked = {alias.name for alias in node.names} & forbidden_names
                if leaked:
                    violations.append(f"{path.relative_to(root)} imports {sorted(leaked)}")
            elif isinstance(node, ast.Name) and node.id in forbidden_names:
                violations.append(f"{path.relative_to(root)} references {node.id}")

    assert not violations, "银行 ORM 越过4号边界：\n" + "\n".join(sorted(set(violations)))
