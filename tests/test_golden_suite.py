from __future__ import annotations

from app.eval.golden_suite import GOLDEN_CASES, GoldenEvaluator, build_report, reference_files


def test_reference_solutions_pass_all_cases() -> None:
    report = GoldenEvaluator().evaluate_suite(reference_files())
    assert report.total == len(GOLDEN_CASES)
    assert report.passed == report.total
    assert report.gate == "PASS"


def test_missing_symbol_and_syntax_fail() -> None:
    evaluator = GoldenEvaluator()
    case = GOLDEN_CASES[0]
    result = evaluator.evaluate(
        case,
        [{"path": "main.py", "content": "def wrong_name():\n    pass\n", "language": "python"}],
    )
    assert result.passed is False
    assert result.checks["expected_symbols"] is False
    assert "missing symbol group" in result.reason

    bad_syntax = evaluator.evaluate(
        case,
        [{"path": "main.py", "content": "def broken(:\n", "language": "python"}],
    )
    assert bad_syntax.passed is False
    assert bad_syntax.checks["syntax"] is False


def test_build_report_gate() -> None:
    report = build_report(
        [
            GoldenEvaluator().evaluate(case, reference_files()[case.id])
            for case in GOLDEN_CASES
        ]
    )
    assert report.gate == "PASS"
    assert report.failed == 0
