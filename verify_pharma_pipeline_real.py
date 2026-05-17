"""Compatibility wrapper for ``scripts.verify.verify_pharma_pipeline_real``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_pharma_pipeline_real", run_name="__main__")

