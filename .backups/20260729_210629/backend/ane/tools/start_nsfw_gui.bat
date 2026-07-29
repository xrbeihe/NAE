@echo off
title ANE-NSFW-GUI
cd /d "%~dp0..\..\.."
set _PY="%~dp0..\..\..\.venv\Scripts\python.exe"
if not exist %_PY% set _PY=python
%_PY% -m ane.tools.nsfw_gui
pause
