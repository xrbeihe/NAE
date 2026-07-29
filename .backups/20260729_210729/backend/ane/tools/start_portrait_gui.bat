@echo off
title ANE-人物外貌收割工具
cd /d "%~dp0..\..\.."
set _PY="%~dp0..\..\..\.venv\Scripts\python.exe"
if not exist %_PY% set _PY=python
%_PY% -m ane.tools.portrait_gui
pause
