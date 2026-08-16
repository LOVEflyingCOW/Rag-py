@echo off
REM ============================================================
REM  Test Platform - 一键负载/并发测试脚本（Windows）
REM  作用：只跑 mark=load 的用例（默认 CI 跳过）
REM ============================================================

setlocal enabledelayedexpansion
set "ROOT=%~dp0..\.."
set "BACKEND=%ROOT%\backend"

cd /d "%BACKEND%"

if exist "%ROOT%\venv\Scripts\activate.bat" (
    call "%ROOT%\venv\Scripts\activate.bat"
)

echo [Load Test] 仅执行 mark=load 的并发用例...
python -m pytest tests/load -m load --tb=long -v
set "EXIT=%ERRORLEVEL%"

echo.
echo 负载测试完成，退出码=%EXIT%
exit /b %EXIT%
