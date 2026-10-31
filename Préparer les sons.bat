@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Extraction des sons de capture depuis les fichiers du jeu...
python tools\extract_sounds.py %*
pause
