@echo off
echo ============================================
echo   Polygon Uploader - Starting...
echo ============================================
echo.
echo   Mo trinh duyet tai: http://localhost:8080
echo   Nhan Ctrl+C de dung server
echo.

:: Open browser after a short delay
start "" "http://localhost:8080"

:: Start server
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
