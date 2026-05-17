"""Run the baseline verification suite used during restructuring."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

COMMANDS = [
    ("structure_imports", [sys.executable, "scripts/verify/verify_structure_imports.py"]),
    ("compile_core", [sys.executable, "-m", "py_compile", "main.py", "db.py", "ingestion.py", "handlers.py"]),
    ("appointment_workflow", [sys.executable, "verify_appointment_workflow.py"]),
    ("layers_4_6", [sys.executable, "verify_layers_4_6.py"]),
    ("document_pipeline", [sys.executable, "verify_document_pipeline.py"]),
    ("pharma_agent", [sys.executable, "verify_pharma_agent.py"]),
]


def main() -> int:
    failures: list[str] = []
    for name, command in COMMANDS:
        print(f"===== START {name} =====", flush=True)
        completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        print(f"===== END {name} exit={completed.returncode} =====", flush=True)
        if completed.returncode:
            failures.append(name)

    if failures:
        print("BASELINE_VERIFICATION_FAILED:", ", ".join(failures))
        return 1

    print("BASELINE_VERIFICATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

