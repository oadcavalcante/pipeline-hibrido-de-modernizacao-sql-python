"""
Nó 1 — Parsing: converte PL/pgSQL em representação estruturada.

Estratégia:
- sqlglot para parsear as instruções SQL embutidas no corpo da procedure
- Regex customizado para estrutura procedural (DECLARE, BEGIN, EXCEPTION, END)
- Extração de: assinatura, parâmetros, variáveis, cursores, construções de risco
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import sqlglot

from pipeline.graph.state import PipelineState

# ─────────────────────────── Modelos de dados ─────────────────────────────────

@dataclass
class Parameter:
    name: str
    type: str
    mode: Literal["IN", "OUT", "INOUT"] = "IN"
    default: str | None = None


@dataclass
class Variable:
    name: str
    type: str
    default: str | None = None


@dataclass
class CursorDef:
    name: str
    query: str


@dataclass
class ParsedProcedure:
    name: str
    kind: Literal["function", "procedure"]
    parameters: list[Parameter] = field(default_factory=list)
    return_type: str | None = None
    returns_set: bool = False           # SETOF / RETURNS TABLE
    returns_table_columns: list[str] = field(default_factory=list)
    declare_vars: list[Variable] = field(default_factory=list)
    cursors: list[CursorDef] = field(default_factory=list)
    has_exception_block: bool = False
    has_transactions: bool = False      # BEGIN/COMMIT/ROLLBACK explícito
    body: str = ""
    declare_section: str = ""
    exception_section: str = ""
    sql_statements: list[str] = field(default_factory=list)
    called_functions: list[str] = field(default_factory=list)
    raise_statements: list[str] = field(default_factory=list)
    raw_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────── Parser principal ─────────────────────────────────

class PGSQLParser:
    """Faz parsing de stored procedures PL/pgSQL para estrutura intermediária."""

    # Padrão para o cabeçalho CREATE [OR REPLACE] FUNCTION|PROCEDURE
    _HEADER_RE = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>FUNCTION|PROCEDURE)\s+"
        r"(?:[\w]+\.)?(?P<name>\w+)\s*\(",
        re.IGNORECASE,
    )

    # Corpo entre $$ ... $$
    _BODY_RE = re.compile(r"\$\$\s*(.*?)\s*\$\$", re.DOTALL)

    # RETURNS [SETOF] <tipo> ou RETURNS TABLE(...)
    _RETURNS_RE = re.compile(
        r"RETURNS\s+(?P<setof>SETOF\s+)?(?P<type>TABLE\s*\([^)]+\)|[\w\(\)\s,]+?)"
        r"(?=\s+LANGUAGE|\s+AS\b)",
        re.IGNORECASE | re.DOTALL,
    )

    # Seção DECLARE
    _DECLARE_RE = re.compile(
        r"^\s*DECLARE\b(.*?)(?=^\s*BEGIN\b)",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )

    # Seção EXCEPTION
    _EXCEPTION_RE = re.compile(
        r"^\s*EXCEPTION\b(.*?)(?=^\s*END\b)",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )

    # RAISE EXCEPTION|WARNING|NOTICE
    _RAISE_RE = re.compile(
        r"\bRAISE\b\s+(EXCEPTION|WARNING|NOTICE|INFO|DEBUG)\b[^;]*;",
        re.IGNORECASE | re.DOTALL,
    )

    # Cursor explícito
    _CURSOR_RE = re.compile(
        r"(\w+)\s+(?:REFCURSOR|CURSOR\b(?:\s+\([^)]+\))?(?:\s+FOR\s+(.+?))?);",
        re.IGNORECASE | re.DOTALL,
    )

    # Chamadas a outras funções/procedures
    _FUNC_CALL_RE = re.compile(
        r"\b(fn_\w+|sp_\w+|proc_\w+)\s*\(",
        re.IGNORECASE,
    )

    # PERFORM / SELECT chamada de função
    _PERFORM_RE = re.compile(r"\bPERFORM\b\s+(\w+)\s*\(", re.IGNORECASE)

    def parse(self, source: str) -> ParsedProcedure:
        source = source.strip()

        header_m = self._HEADER_RE.search(source)
        if not header_m:
            raise ValueError("Não foi possível identificar CREATE FUNCTION/PROCEDURE no código.")

        kind: Literal["function", "procedure"] = (
            "function" if header_m.group("kind").upper() == "FUNCTION" else "procedure"
        )
        name = header_m.group("name")

        # Extrai parâmetros (texto entre parênteses após o nome)
        param_start = header_m.end()
        param_end = self._find_closing_paren(source, param_start - 1)
        params_str = source[param_start:param_end]
        parameters = self._parse_parameters(params_str)

        # RETURNS
        return_type: str | None = None
        returns_set = False
        returns_table_columns: list[str] = []
        returns_m = self._RETURNS_RE.search(source)
        if returns_m:
            returns_set = bool(returns_m.group("setof"))
            rtype = returns_m.group("type").strip()
            if rtype.upper().startswith("TABLE"):
                returns_set = True
                cols_match = re.search(r"TABLE\s*\((.+)\)", rtype, re.IGNORECASE | re.DOTALL)
                if cols_match:
                    returns_table_columns = [
                        c.strip() for c in cols_match.group(1).split(",")
                    ]
                return_type = "TABLE"
            else:
                return_type = rtype.strip()

        # Corpo entre $$
        body_m = self._BODY_RE.search(source)
        if not body_m:
            raise ValueError("Corpo da procedure (entre $$) não encontrado.")
        full_body = body_m.group(1)

        # Seção DECLARE
        declare_section = ""
        declare_vars: list[Variable] = []
        cursors: list[CursorDef] = []
        declare_m = self._DECLARE_RE.search(full_body)
        if declare_m:
            declare_section = declare_m.group(1)
            declare_vars, cursors = self._parse_declare_section(declare_section)

        # Seção EXCEPTION
        exception_section = ""
        has_exception = False
        exc_m = self._EXCEPTION_RE.search(full_body)
        if exc_m:
            has_exception = True
            exception_section = exc_m.group(1)

        # Corpo principal (entre BEGIN e EXCEPTION|END)
        body = self._extract_main_body(full_body)

        # Instruções SQL dentro do corpo
        sql_statements = self._extract_sql_statements(body)

        # RAISE statements
        raise_stmts = [m.group(0) for m in self._RAISE_RE.finditer(full_body)]

        # Chamadas a outras funções
        called = list({m.group(1) for m in self._FUNC_CALL_RE.finditer(full_body)})
        called += list({m.group(1) for m in self._PERFORM_RE.finditer(full_body)})
        called = list(set(called))

        return ParsedProcedure(
            name=name,
            kind=kind,
            parameters=parameters,
            return_type=return_type,
            returns_set=returns_set,
            returns_table_columns=returns_table_columns,
            declare_vars=declare_vars,
            cursors=cursors,
            has_exception_block=has_exception,
            has_transactions=self._has_explicit_transactions(full_body),
            body=body,
            declare_section=declare_section.strip(),
            exception_section=exception_section.strip(),
            sql_statements=sql_statements,
            called_functions=called,
            raise_statements=raise_stmts,
            raw_source=source,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _find_closing_paren(self, text: str, open_pos: int) -> int:
        """Encontra a posição do ')' que fecha o '(' em open_pos."""
        depth = 0
        for i in range(open_pos, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        return len(text)

    def _split_by_comma_top_level(self, text: str) -> list[str]:
        """Divide texto por vírgula respeitando parênteses aninhados."""
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in text:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append("".join(current).strip())
        return parts

    def _parse_parameters(self, params_str: str) -> list[Parameter]:
        if not params_str.strip():
            return []
        params: list[Parameter] = []
        for raw in self._split_by_comma_top_level(params_str):
            raw = raw.strip()
            if not raw:
                continue

            mode: Literal["IN", "OUT", "INOUT"] = "IN"
            mode_m = re.match(r"^(INOUT|IN|OUT)\s+", raw, re.IGNORECASE)
            if mode_m:
                mode = mode_m.group(1).upper()  # type: ignore[assignment]
                raw = raw[mode_m.end():]

            # nome tipo [DEFAULT ...]
            default: str | None = None
            default_m = re.search(r"\s+(?:DEFAULT|:=)\s+(.+)$", raw, re.IGNORECASE)
            if default_m:
                default = default_m.group(1).strip()
                raw = raw[: default_m.start()].strip()

            parts = raw.split(None, 1)
            if len(parts) == 2:
                params.append(Parameter(name=parts[0], type=parts[1].strip(), mode=mode, default=default))
            elif len(parts) == 1:
                params.append(Parameter(name=parts[0], type="unknown", mode=mode))
        return params

    def _parse_declare_section(
        self, declare_str: str
    ) -> tuple[list[Variable], list[CursorDef]]:
        variables: list[Variable] = []
        cursors: list[CursorDef] = []

        for line in re.split(r";", declare_str):
            line = line.strip()
            if not line:
                continue

            # Cursor: nome CURSOR [FOR query]
            cur_m = re.match(
                r"(\w+)\s+(?:CURSOR\b(?:\s+FOR\s+(.+))?|REFCURSOR\b)",
                line,
                re.IGNORECASE | re.DOTALL,
            )
            if cur_m:
                cursors.append(CursorDef(name=cur_m.group(1), query=(cur_m.group(2) or "").strip()))
                continue

            # Variável: nome TIPO [:= valor]
            var_m = re.match(r"(\w+)\s+(.+?)(?:\s*:=\s*(.+))?$", line, re.DOTALL)
            if var_m:
                name = var_m.group(1)
                type_str = var_m.group(2).strip()
                default = var_m.group(3)
                # DEFAULT dentro do tipo
                inner_def = re.search(r"\s*:=\s*(.+)$", type_str)
                if inner_def:
                    default = inner_def.group(1).strip()
                    type_str = type_str[: inner_def.start()].strip()
                variables.append(Variable(name=name, type=type_str, default=default))
        return variables, cursors

    def _extract_main_body(self, full_body: str) -> str:
        """Extrai o bloco principal entre o primeiro BEGIN e EXCEPTION|END."""
        begin_m = re.search(r"^\s*BEGIN\b", full_body, re.IGNORECASE | re.MULTILINE)
        if not begin_m:
            return full_body

        body_start = begin_m.end()
        # Tenta encontrar EXCEPTION ou END no mesmo nível
        exc_m = re.search(r"^\s*EXCEPTION\b", full_body[body_start:], re.IGNORECASE | re.MULTILINE)
        end_m = re.search(r"^\s*END\b\s*;?\s*$", full_body[body_start:], re.IGNORECASE | re.MULTILINE)

        if exc_m:
            return full_body[body_start : body_start + exc_m.start()].strip()
        if end_m:
            return full_body[body_start : body_start + end_m.start()].strip()
        return full_body[body_start:].strip()

    def _extract_sql_statements(self, body: str) -> list[str]:
        """Extrai e tenta parsear as instruções SQL dentro do corpo PL/pgSQL."""
        stmts: list[str] = []
        sql_pattern = re.compile(
            r"\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b.+?;",
            re.IGNORECASE | re.DOTALL,
        )
        for m in sql_pattern.finditer(body):
            raw_sql = m.group(0).strip()
            # Remove variáveis PL/pgSQL (INTO var) para tentar parsear
            clean = re.sub(r"\bINTO\s+[\w\s,]+", "", raw_sql, flags=re.IGNORECASE)
            try:
                sqlglot.parse_one(clean, dialect="postgres")
                stmts.append(raw_sql)
            except Exception:
                stmts.append(raw_sql)  # mantém mesmo sem parsear
        return stmts

    def _has_explicit_transactions(self, body: str) -> bool:
        return bool(re.search(r"\b(BEGIN\s+TRANSACTION|COMMIT|ROLLBACK)\b", body, re.IGNORECASE))


# ─────────────────────────── Nó do LangGraph ─────────────────────────────────

async def parse_procedure_node(state: PipelineState) -> dict[str, Any]:
    """Nó 1 — Parsing: PL/pgSQL → estrutura intermediária."""
    start = time.perf_counter()
    parser = PGSQLParser()
    try:
        parsed = parser.parse(state["source_code"])
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "parsed_procedure": parsed.to_dict(),
            "parsing_errors": [],
            "parsing_report": {
                "status": "success",
                "duration_ms": round(elapsed, 2),
                "name": parsed.name,
                "kind": parsed.kind,
                "parameters_count": len(parsed.parameters),
                "variables_count": len(parsed.declare_vars),
                "cursors_count": len(parsed.cursors),
                "has_exception_block": parsed.has_exception_block,
                "sql_statements_count": len(parsed.sql_statements),
            },
            "status": "running",
        }
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "parsed_procedure": None,
            "parsing_errors": [str(exc)],
            "parsing_report": {
                "status": "failure",
                "duration_ms": round(elapsed, 2),
                "error": str(exc),
            },
            "status": "failure",
            "error": f"Parsing falhou: {exc}",
        }
