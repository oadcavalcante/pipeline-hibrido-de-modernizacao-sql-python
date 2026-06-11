"""Testes do nó de validação."""

from __future__ import annotations

from pipeline.graph.nodes.validation import validate_python_code


class TestValidationValid:
    def test_ast_valid(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.ast_valid is True

    def test_no_ast_error(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.ast_error is None

    def test_function_count(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.function_count >= 1

    def test_has_imports(self, valid_python):
        result = validate_python_code(valid_python)
        assert len(result.import_lines) >= 1

    def test_type_hint_coverage(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.type_hint_coverage > 0.0

    def test_quality_score_positive(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.quality_score > 0.0

    def test_passed_true(self, valid_python):
        result = validate_python_code(valid_python)
        assert result.passed is True


class TestValidationInvalid:
    def test_ast_invalid(self, invalid_python):
        result = validate_python_code(invalid_python)
        assert result.ast_valid is False

    def test_ast_error_set(self, invalid_python):
        result = validate_python_code(invalid_python)
        assert result.ast_error is not None

    def test_quality_score_zero(self, invalid_python):
        result = validate_python_code(invalid_python)
        assert result.quality_score == 0.0

    def test_passed_false(self, invalid_python):
        result = validate_python_code(invalid_python)
        assert result.passed is False


class TestValidationEdgeCases:
    def test_empty_string(self):
        result = validate_python_code("")
        assert result.ast_valid is True  # módulo vazio é válido
        assert result.function_count == 0

    def test_hello_world(self):
        code = 'print("hello")\n'
        result = validate_python_code(code)
        assert result.ast_valid is True

    def test_to_dict_complete(self, valid_python):
        result = validate_python_code(valid_python)
        d = result.to_dict()
        assert "ast_valid" in d
        assert "quality_score" in d
        assert "passed" in d
