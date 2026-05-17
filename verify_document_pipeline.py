"""Compatibility wrapper for ``scripts.verify.verify_document_pipeline``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_document_pipeline", run_name="__main__")

