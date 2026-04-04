#!/usr/bin/env python3
"""Checks lightweight architectural contracts after modular refactor."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_ast(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def class_method_names(tree: ast.AST, class_name: str) -> set[str]:
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    return set()


def assert_main_entrypoint() -> None:
    main_file = ROOT / "main.py"
    source = main_file.read_text(encoding="utf-8")
    required = [
        "from core.bot_app import run_entrypoint",
        'if __name__ == "__main__":',
        "run_entrypoint()",
    ]
    for token in required:
        if token not in source:
            raise AssertionError(f"main.py no contiene contrato requerido: {token}")


def assert_bot_app_contract() -> None:
    tree = load_ast(ROOT / "core" / "bot_app.py")
    methods = class_method_names(tree, "Bot")
    required = {
        "__init__",
        "connect",
        "sync_wallet",
        "monitor_open_trades",
        "handle_command",
    }
    missing = sorted(required - methods)
    if missing:
        raise AssertionError(f"Bot contract incompleto, faltan métodos: {missing}")



def assert_bot_facade_contract() -> None:
    tree = load_ast(ROOT / "core" / "bot_facade.py")
    methods = class_method_names(tree, "BotFacade")
    required = {
        "run",
        "execute_order",
        "close_trade",
        "update_radar",
        "get_audit_verdict",
    }
    missing = sorted(required - methods)
    if missing:
        raise AssertionError(f"BotFacade contract incompleto, faltan métodos: {missing}")


def main() -> int:
    assert_main_entrypoint()
    assert_bot_app_contract()
    assert_bot_facade_contract()
    print("[OK] Contratos arquitectónicos validados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
