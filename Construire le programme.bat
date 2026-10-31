@echo off
chcp 65001 >nul
rem Builds distribution\Compteur pity Aniimo.exe (one file, no console) with PyInstaller.
rem Bundles only what the counter needs: the fingerprints of the Lucky / Super Lucky / capture sounds
rem (sons\empreintes.npz, not the sounds themselves) and the three image models.
cd /d "%~dp0"
set WORK=%TEMP%\compteur-pity-build
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Compteur pity Aniimo" ^
  --add-data "%~dp0sons\empreintes.npz;sons" ^
  --add-data "%~dp0images\niv.png;images" --add-data "%~dp0images\culbuton.png;images" ^
  --add-data "%~dp0images\floraison_titre.png;images" ^
  --distpath "%~dp0distribution" --workpath "%WORK%" --specpath "%WORK%" overlay.py
if errorlevel 1 (echo. & echo La construction a échoué. & pause & exit /b 1)
echo.
echo Fait : distribution\Compteur pity Aniimo.exe
pause
