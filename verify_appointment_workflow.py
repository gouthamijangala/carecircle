"""Compatibility wrapper for ``scripts.verify.verify_appointment_workflow``."""

from runpy import run_module


if __name__ == "__main__":
    run_module("scripts.verify.verify_appointment_workflow", run_name="__main__")

