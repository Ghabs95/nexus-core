#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FileBudget:
    path: str
    max_loc: int


FILE_BUDGETS = [
    FileBudget("examples/telegram-bot/src/inbox_processor.py", 800),
    FileBudget("examples/telegram-bot/src/telegram_bot.py", 800),
    FileBudget("examples/telegram-bot/src/webhook_server.py", 800),
    FileBudget("nexus/core/workflow.py", 800),
    FileBudget("nexus/plugins/builtin/ai_runtime_plugin.py", 800),
]

FUNCTION_TARGETS = {
    "nexus/core/workflow.py::WorkflowEngine.complete_step": 100,
    "nexus/plugins/builtin/ai_runtime_plugin.py::AIOrchestrator.invoke_agent": 100,
    "examples/telegram-bot/src/inbox_processor.py::process_file": 150,
}


def count_loc(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def iter_functions(module: ast.Module) -> Iterable[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{item.name}", item


def function_lengths(path: Path) -> dict[str, int]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for name, node in iter_functions(module):
        if hasattr(node, "end_lineno") and node.end_lineno:
            out[name] = int(node.end_lineno) - int(node.lineno) + 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-blocking hotspot budget checker")
    parser.add_argument("--fail-on-targets", action="store_true", help="Exit non-zero on target violations")
    args = parser.parse_args()

    violations: list[str] = []
    print("Hotspot budgets")
    print("===============")

    for budget in FILE_BUDGETS:
        path = REPO_ROOT / budget.path
        if not path.exists():
            print(f"[SKIP] {budget.path} (missing)")
            continue
        loc = count_loc(path)
        status = "OK" if loc <= budget.max_loc else "WARN"
        line = f"[{status}] {budget.path}: {loc} LOC (target <={budget.max_loc})"
        print(line)
        if status == "WARN":
            violations.append(line)

    print("\nFunction targets")
    print("================")
    cache: dict[str, dict[str, int]] = {}
    for key, target in FUNCTION_TARGETS.items():
        file_path_str, func_name = key.split("::", 1)
        path = REPO_ROOT / file_path_str
        if not path.exists():
            print(f"[SKIP] {key} (missing file)")
            continue
        lengths = cache.setdefault(file_path_str, function_lengths(path))
        length = lengths.get(func_name)
        if length is None:
            print(f"[SKIP] {key} (function not found)")
            continue
        status = "OK" if length <= target else "WARN"
        line = f"[{status}] {key}: {length} lines (target <={target})"
        print(line)
        if status == "WARN":
            violations.append(line)

    print("\nResult")
    print("======")
    if violations:
        print(f"{len(violations)} warning(s).")
        if args.fail_on_targets:
            return 1
    else:
        print("No hotspot target warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

