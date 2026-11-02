@echo off
setlocal
set "REPO=%WINDWAKER_TRANSLATION_REPO%"
if not defined REPO if exist "%~dp0workflow.py" set "REPO=%~dp0."
if not defined REPO set "REPO=D:\git\dev\ZeldaWW-Ukrainian-translation"
if not exist "%REPO%\workflow.py" (echo Translation workflow not found: "%REPO%\workflow.py" & pause & exit /b 1)
where python >nul 2>nul || (echo Python was not found on PATH. & pause & exit /b 1)
python "%REPO%\workflow.py" build gc
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
