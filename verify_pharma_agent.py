"""Compatibility wrapper for ``scripts.verify.verify_pharma_agent``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_pharma_agent", run_name="__main__")

