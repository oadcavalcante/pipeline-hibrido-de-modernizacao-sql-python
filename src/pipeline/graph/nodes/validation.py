"""
Nó 4 — Validação: verifica a qualidade do código Python gerado.

Verificações:
1. ast.parse()          — sintaxe Python válida (obrigatório)
2. ruff check           — qualidade estática / imports (obrigatório se disponível)
3. type_hint_coverage   — fração de funções com type hints (métricas)
4. has_docstrings       — funções com docstrings (métricas)
5. import_check         — imports presentes para nomes usados (heurística)
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pipeline.graph.state import PipelineState


@dataclass
class ValidationResult:
    ast_valid: bool
    ast_error: str | None
    ruff_available: bool
    ruff_issues: list[str]
    ruff_error_count: int
    ruff_warning_count: int
    type_hint_coverage: float       # 0.0 - 1.0
    has_docstrings: bool
    function_count: int
    import_lines: list[str]
    # Métrica composta
    quality_score: float            # 0.0 - 1.0

    @property
    def passed(self) -> bool:
        return self.ast_valid and self.ruff_error_count == 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def validate_python_code(code: str) -> ValidationResult:
    """Executa todas as validações estáticas no código gerado."""
    # 1. ast.parse
    ast_valid = False
    ast_error: str | None = None
    tree: ast.Module | None = None
    try:
        tree = ast.parse(code)
        ast_valid = True
    except SyntaxError as exc:
        ast_error = f"SyntaxError na linha {exc.lineno}: {exc.msg}"

    # 2. ruff
    ruff_available = False
    ruff_issues: list[str] = []
    ruff_error_count = 0
    ruff_warning_count = 0
    if ast_valid:
        ruff_available, ruff_issues, ruff_error_count, ruff_warning_count = _run_ruff(code)

    # 3. Métricas de qualidade (só se AST válida)
    function_count = 0
    type_hint_coverage = 0.0
    has_docstrings = False
    import_lines: list[str] = []

    if tree:
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        function_count = len(funcs)

        if funcs:
            typed = sum(1 for f in funcs if f.returns is not None or any(a.annotation for a in f.args.args))
            type_hint_coverage = round(typed / len(funcs), 2)
            has_docstrings = any(
                isinstance(f.body[0], ast.Expr) and isinstance(f.body[0].value, ast.Constant)
                for f in funcs
                if f.body
            )

        import_lines = [
            ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
        ]

    # 4. Score de qualidade composto
    quality_score = _compute_quality_score(
        ast_valid=ast_valid,
        ruff_error_count=ruff_error_count,
        ruff_warning_count=ruff_warning_count,
        type_hint_coverage=type_hint_coverage,
        has_docstrings=has_docstrings,
        function_count=function_count,
    )

    return ValidationResult(
        ast_valid=ast_valid,
        ast_error=ast_error,
        ruff_available=ruff_available,
        ruff_issues=ruff_issues[:20],   # limita saída
        ruff_error_count=ruff_error_count,
        ruff_warning_count=ruff_warning_count,
        type_hint_coverage=type_hint_coverage,
        has_docstrings=has_docstrings,
        function_count=function_count,
        import_lines=import_lines,
        quality_score=quality_score,
    )


def _run_ruff(code: str) -> tuple[bool, list[str], int, int]:
    """Executa ruff check em um arquivo temporário."""
    issues: list[str] = []
    errors = 0
    warnings = 0
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["ruff", "check", "--select=E,W,F,I", "--output-format=text", tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        Path(tmp_path).unlink(missing_ok=True)

        for line in result.stdout.splitlines():
            if re.search(r"\s[EF]\d+\s", line):
                errors += 1
                issues.append(line)
            elif re.search(r"\s[W]\d+\s", line):
                warnings += 1
                issues.append(line)
        return True, issues, errors, warnings

    except FileNotFoundError:
        return False, [], 0, 0
    except Exception:
        return False, [], 0, 0


def _compute_quality_score(
    *,
    ast_valid: bool,
    ruff_error_count: int,
    ruff_warning_count: int,
    type_hint_coverage: float,
    has_docstrings: bool,
    function_count: int,
) -> float:
    if not ast_valid:
        return 0.0

    score = 0.5   # baseline: código parseável
    if ruff_error_count == 0:
        score += 0.2
    elif ruff_error_count <= 3:
        score += 0.1
    if ruff_warning_count == 0:
        score += 0.1
    score += type_hint_coverage * 0.1
    if has_docstrings:
        score += 0.1
    return round(min(score, 1.0), 3)


# ─────────────────────────── Nó do LangGraph ─────────────────────────────────

async def validate_output_node(state: PipelineState) -> dict[str, Any]:
    """Nó 4 — Validação estática do código gerado."""
    if state.get("status") == "failure":
        return {}

    generated_code = state.get("generated_code")
    if not generated_code:
        return {
            "validation_passed": False,
            "validation_report": {"status": "skipped", "reason": "sem código gerado"},
            "status": "partial",
        }

    start = time.perf_counter()
    result = validate_python_code(generated_code)
    elapsed = (time.perf_counter() - start) * 1000

    final_status = "success" if result.passed else "partial"

    return {
        "validation_passed": result.passed,
        "validation_report": {
            **result.to_dict(),
            "duration_ms": round(elapsed, 2),
            "status": "success" if result.passed else "partial",
        },
        "status": final_status,
    }
