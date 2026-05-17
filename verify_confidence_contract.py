"""Compatibility wrapper for ``scripts.verify.verify_confidence_contract``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_confidence_contract", run_name="__main__")

