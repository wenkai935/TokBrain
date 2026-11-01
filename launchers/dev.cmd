@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo Starting TokBrain BACKEND on 127.0.0.1:8001 ...
start "TokBrain Backend :8001" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001"

echo Starting TokBrain FRONTEND (hot reload) on 127.0.0.1:3001 ...
cd /d "%~dp0..\frontend"
start "TokBrain Frontend dev :3001" cmd /k "npm run dev"

echo.
echo Two windows were opened: backend :8001 and frontend dev :3001.
echo Keep both windows open. Frontend supports hot reload; editing .tsx saves and refreshes.
echo Close the windows to stop the services.
timeout /t 4 >nul
endlocal
