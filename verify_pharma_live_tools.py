"""Compatibility wrapper for ``scripts.verify.verify_pharma_live_tools``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_pharma_live_tools", run_name="__main__")

