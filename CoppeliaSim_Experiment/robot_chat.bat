@echo off
cd /d "%~dp0"

py -3.13 llm_robot_client.py --menu
echo Robot client closed.
pause
