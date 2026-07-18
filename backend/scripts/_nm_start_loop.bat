@echo off
cd /d D:\ADLINK\Myproject\novel-mind\backend
set PYTHONPATH=D:\ADLINK\Myproject\novel-mind\backend
set PYTHONUNBUFFERED=1
".venv\Scripts\python.exe" -u scripts\_nm_resume_loop.py 2 91 1 8 200 >> "D:\ADLINK\Myproject\novel-mind\.planning\phases\20-structure-workspace-multilayer-presentation\20-NM-BUILD-LOOP.out.log" 2>> "D:\ADLINK\Myproject\novel-mind\.planning\phases\20-structure-workspace-multilayer-presentation\20-NM-BUILD-LOOP.err.log"
