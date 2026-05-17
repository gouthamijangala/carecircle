"""Compatibility wrapper for ``scripts.verify.verify_system_optimization``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_system_optimization", run_name="__main__")

