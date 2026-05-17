"""Verify root compatibility imports and new package imports both work.

This script is intentionally lightweight so it can run before and after each
package migration batch.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    import context_manager
    import pharma_prompts
    import validators
    from carecircle.app.core import config as packaged_config
    from carecircle.app.ingestion.context_manager import MedicalContextManager
    from carecircle.app.ingestion.validators import validate_and_clean
    from carecircle.app.pharma.prompts import get_prompt_for_type

    assert context_manager.MedicalContextManager is MedicalContextManager
    assert validators.validate_and_clean is validate_and_clean
    assert pharma_prompts.get_prompt_for_type is get_prompt_for_type
    assert hasattr(packaged_config, "LLM_ENDPOINT")
    assert "/no_think" in get_prompt_for_type("prescription")
    print("STRUCTURE_IMPORT_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
