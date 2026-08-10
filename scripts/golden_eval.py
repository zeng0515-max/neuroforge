"""Run the golden evaluation suite offline or against the live pipeline."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.golden_suite import (
    GOLDEN_CASES,
    GoldenEvaluator,
    GoldenReport,
    reference_files,
    write_report,
)


def _files_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    pkg = state.get("code_package")
    files = getattr(pkg, "files", []) if pkg else []
    return [
        {
            "path": f.path,
            "content": f.content,
            "language": f.language,
        }
        for f in files
    ]


def run_live() -> Dict[str, List[Dict[str, Any]]]:
    from app.graph.unified_engine import stream_engine

    generated: Dict[str, List[Dict[str, Any]]] = {}
    for case in GOLDEN_CASES:
        _, state = stream_engine(
            task_id=f"golden-{case.id}",
            user_request=case.task,
            extra_args={"golden_case": case.id},
        )
        generated[case.id] = _files_from_state(state)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(description="NeuroForge golden evaluation")
    parser.add_argument("--live", action="store_true", help="run against live pipeline (requires LLM)")
    parser.add_argument("--output-dir", default="benchmark-results", help="output directory")
    args = parser.parse_args()

    files = run_live() if args.live else reference_files()
    report = GoldenEvaluator().evaluate_suite(files)
    report.generated_at = datetime.now(timezone.utc).isoformat()
    report.mode = "live" if args.live else "offline"
    report.command = " ".join(sys.argv)
    if args.live:
        try:
            from app.core.llm_adapter import llm

            report.model = llm.current_model
        except Exception:
            report.model = "unknown"
    report_path = write_report(report, Path(args.output_dir))

    print(f"Golden evaluation: {report.passed}/{report.total} passed, gate={report.gate}")
    print(f"Report: {report_path.resolve()}")
    return 0 if report.gate in ("PASS", "WARN") else 1


if __name__ == "__main__":
    sys.exit(main())
