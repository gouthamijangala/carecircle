import os
import subprocess
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

log_path = os.path.join(BASE_DIR, "uvicorn.pythonw.log")
with open(log_path, "ab", buffering=0) as log_file:
    subprocess.Popen(
        [
            sys.executable.replace("pythonw.exe", "python.exe"),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "info",
        ],
        cwd=BASE_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
