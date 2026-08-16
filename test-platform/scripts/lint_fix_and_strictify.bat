@echo off
REM ============================================================
REM  Test Platform · O-0.1 · lint strict 化一键脚本
REM  作用: pip install ruff/mypy → ruff --fix → ruff format → 最终校验
REM  前置: 网络通畅(能访问 files.pythonhosted.org)
REM  执行完此脚本, 若显示 "=== lint strict 化完成 ===":
REM      请手动去 .github/workflows/ci.yml lint Job 里把 3 个
REM      "continue-on-error: true" 删除或改成 false
REM ============================================================
setlocal enabledelayedexpansion
set "PYTHON=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [1/5] 安装 ruff + mypy (可能超时, 重试一两次)...
"%PYTHON%" -m pip install --timeout=120 "ruff==0.5.0" "mypy==1.4.1"
if errorlevel 1 (
    echo [!] pip install 超时或失败, 请重试 1-2 次
    exit /b 1
)

echo [2/5] ruff check --fix 自动修 E/W 类可修复问题...
cd /d "%~dp0..\.."
"%PYTHON%" -m ruff check backend\app backend\tests --fix
set "RUFF_FIX=%ERRORLEVEL%"
echo     ruff --fix exit=%RUFF_FIX% (非零说明有需要手动修的 F 类错误)

echo [3/5] ruff format 统一代码风格...
"%PYTHON%" -m ruff format backend\app backend\tests

echo [4/5] 最终 ruff check (严格, 不自动修)
"%PYTHON%" -m ruff check backend\app backend\tests
set "RUFF_FINAL=%ERRORLEVEL%"

echo [5/5] 最终 mypy (渐进式白名单: 整体 ignore_errors, 每个 Sprint 解一条 module)
"%PYTHON%" -m mypy backend\app --ignore-missing-imports
set "MYPY_FINAL=%ERRORLEVEL%"

echo.
echo ================================================
echo  最终结果:
echo    ruff check 退出码 = %RUFF_FINAL%  (期望 0)
echo    mypy       退出码 = %MYPY_FINAL%  (期望 0)
echo ================================================
if %RUFF_FINAL%==0 if %MYPY_FINAL%==0 (
    echo.
    echo === lint strict 化完成 ===
    echo  下一步: 去 .github/workflows/ci.yml 的 lint Job
    echo          删除 3 处 continue-on-error: true
    echo          (ruff check / ruff format / mypy 三条)
    echo          保存 → commit → push → CI 就会严格拦问题
    exit /b 0
) else (
    echo.
    echo === 还有错误需要手动修 ===
    if not %RUFF_FINAL%==0 (
        echo   - ruff: 运行 "ruff check backend\app backend\tests" 看报错清单
        echo     F 类错误 (未定义变量 / 类型错) 需要手动修
    )
    if not %MYPY_FINAL%==0 (
        echo   - mypy: 在 pyproject.toml 的 [[tool.mypy.overrides]] 把对应模块从
        echo     ignore_errors=true 的白名单里挑一个, 在 app.* override 之前
        echo     插入一条 ignore_errors=false 开始严格化
    )
    exit /b 1
)
