@echo off
cd /d "%~dp0"

:loop
echo.
set /p instruction=Robot instruction:

if /I "%instruction%"=="exit" goto end
if "%instruction%"=="" goto loop

py -3.13 llm_robot_client.py "%instruction%"
goto loop

:end
echo Robot client closed.
pause
