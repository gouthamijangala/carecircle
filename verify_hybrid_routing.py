"""Compatibility wrapper for ``scripts.verify.verify_hybrid_routing``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_hybrid_routing", run_name="__main__")

