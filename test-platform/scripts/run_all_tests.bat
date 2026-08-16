@echo off
REM ============================================================
REM  Test Platform - 一键全量测试脚本（Windows）
REM  作用：激活 venv -> 跑 unit/integration/api/phases -> 生成覆盖率报告
REM  用法：双击本文件，或在 test-platform\scripts\ 下执行 run_all_tests.bat
REM ============================================================

setlocal enabledelayedexpansion
set "ROOT=%~dp0..\.."
set "BACKEND=%ROOT%\backend"

echo [1/4] 进入 backend 目录...
cd /d "%BACKEND%"

echo [2/4] 检查并激活虚拟环境...
if exist "%ROOT%\venv\Scripts\activate.bat" (
    call "%ROOT%\venv\Scripts\activate.bat"
    echo     venv 已激活
) else (
    echo     [!] 未找到 venv，使用全局 Python（建议先 python -m venv venv）
)

echo [3/4] 运行 pytest（排除 load/slow，生成 coverage + JUnit + HTML）...
python -m pytest tests/unit tests/integration tests/api tests/phases ^
    -m "not load and not slow" ^
    --maxfail=5 ^
    --tb=short ^
    -q
set "PYTEST_EXIT=%ERRORLEVEL%"

echo [4/4] 打开覆盖率报告...
if exist "%BACKEND%\htmlcov\index.html" (
    start "" "%BACKEND%\htmlcov\index.html"
) else (
    echo     [!] htmlcov 未生成，请检查 pytest 输出
)

echo.
echo ================================
echo  pytest 退出码: %PYTEST_EXIT%
echo  报告位置:
echo   - JUnit:   backend\reports\test-results.xml
echo   - Coverage XML: backend\reports\coverage.xml
echo   - HTML:    backend\htmlcov\index.html
echo ================================
exit /b %PYTEST_EXIT%
