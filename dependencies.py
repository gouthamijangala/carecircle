import importlib
import subprocess
import sys


REQUIRED_PACKAGES = ("sentence_transformers", "numpy", "torch")
INSTALL_COMMAND = [
    sys.executable,
    "-m",
    "pip",
    "install",
    "-U",
    "sentence-transformers",
    "numpy",
    "torch",
    "--index-url",
    "https://download.pytorch.org/whl/cpu",
]
FALLBACK_INSTALL_COMMAND = [
    sys.executable,
    "-m",
    "pip",
    "install",
    "-U",
    "sentence-transformers",
    "numpy",
    "torch",
    "--extra-index-url",
    "https://download.pytorch.org/whl/cpu",
]


def _missing_packages() -> list[str]:
    missing = []
    for package_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(package_name)
        except Exception:
            missing.append(package_name)
    return missing


def verify_and_install_dependencies(auto_install: bool = False) -> bool:
    """
    Verify embedding dependencies and optionally install them if missing.
    Never raises; returns True only when all imports succeed.

    Runtime server startup should pass auto_install=False. Installing packages
    inside uvicorn --reload modifies venv files, which causes reload loops.
    """
    try:
        missing = _missing_packages()
        if not missing:
            print("Embedding dependencies available: sentence_transformers, numpy, torch")
            return True

        print(f"Missing embedding dependencies: {', '.join(missing)}")
        if not auto_install:
            print(
                "Embedding dependency auto-install disabled. "
                "Run dependency installation once outside the server process."
            )
            return False

        print("Installing embedding dependencies for CPU runtime...")
        result = subprocess.run(
            INSTALL_COMMAND,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Embedding dependency install failed.")
            if result.stderr:
                print(result.stderr[-2000:])
        else:
            print("Embedding dependency install completed.")

        remaining = _missing_packages()
        if remaining:
            print("Retrying embedding dependency install with PyPI fallback...")
            fallback_result = subprocess.run(
                FALLBACK_INSTALL_COMMAND,
                check=False,
                capture_output=True,
                text=True,
            )
            if fallback_result.returncode != 0:
                print("Fallback embedding dependency install failed.")
                if fallback_result.stderr:
                    print(fallback_result.stderr[-2000:])
            else:
                print("Fallback embedding dependency install completed.")
            remaining = _missing_packages()

        if remaining:
            print(f"Embedding dependencies still missing: {', '.join(remaining)}")
            return False

        print("Embedding dependencies verified after install.")
        return True
    except Exception as error:
        print(f"Embedding dependency verification failed: {error}")
        return False
