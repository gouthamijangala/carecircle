@echo off
cd /d C:\Users\DELL\Documents\100x-Project
if exist ".\venv\Scripts\python.exe" (
  ".\venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info >> uvicorn.task.log 2>&1
) else (
  python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info >> uvicorn.task.log 2>&1
)
