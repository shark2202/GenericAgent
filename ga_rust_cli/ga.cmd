@echo off
REM §9.4 Launch script: ga.cmd (Windows)
REM
REM Convenience wrapper for the ga binary on Windows.
REM Searches for ga.exe in: target\debug, target\release, PATH.

setlocal

set "SCRIPT_DIR=%~dp0"

REM 1. target\debug\ga.exe
if exist "%SCRIPT_DIR%target\debug\ga.exe" (
    "%SCRIPT_DIR%target\debug\ga.exe" %*
    goto :eof
)

REM 2. target\release\ga.exe
if exist "%SCRIPT_DIR%target\release\ga.exe" (
    "%SCRIPT_DIR%target\release\ga.exe" %*
    goto :eof
)

REM 3. Same directory as script
if exist "%SCRIPT_DIR%ga.exe" (
    "%SCRIPT_DIR%ga.exe" %*
    goto :eof
)

REM 4. In PATH
where ga.exe >nul 2>&1
if %ERRORLEVEL% equ 0 (
    ga.exe %*
    goto :eof
)

REM 5. Try to build from source
if exist "%SCRIPT_DIR%Cargo.toml" (
    echo ga.exe not found. Building from source...
    cargo build --manifest-path "%SCRIPT_DIR%Cargo.toml" --release
    if exist "%SCRIPT_DIR%target\release\ga.exe" (
        "%SCRIPT_DIR%target\release\ga.exe" %*
        goto :eof
    )
)

echo ERROR: Cannot find or build ga. Please install manually.
exit /b 1
