@echo off
title Juridisch Assistent - Build EXE
echo.
echo  ================================================
echo   Juridisch Assistent - Build naar .exe
echo  ================================================
echo.

cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FOUT] Python niet gevonden. Installeer Python 3.10+
    pause
    exit /b 1
)

:: Check/install PyInstaller
echo [1/4] PyInstaller controleren...
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo       PyInstaller installeren...
    pip install pyinstaller
)

:: Install dependencies
echo [2/4] Dependencies installeren...
pip install -q -r requirements.txt

:: Clean previous builds
echo [3/4] Vorige builds opruimen...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

:: Build
echo [4/4] EXE bouwen... (dit duurt 2-5 minuten)
echo.
pyinstaller ^
    --onefile ^
    --name "JuridischAssistent" ^
    --icon "NONE" ^
    --add-data "templates;templates" ^
    --hidden-import "flask" ^
    --hidden-import "flask_sqlalchemy" ^
    --hidden-import "sqlalchemy" ^
    --hidden-import "sqlalchemy.dialects.sqlite" ^
    --hidden-import "jinja2" ^
    --hidden-import "json" ^
    --hidden-import "email.mime.text" ^
    --collect-submodules "sqlalchemy" ^
    --noconfirm ^
    --clean ^
    app.py

if errorlevel 1 (
    echo.
    echo [FOUT] Build mislukt!
    pause
    exit /b 1
)

echo.
echo  ================================================
echo   Build geslaagd!
echo   EXE: dist\JuridischAssistent.exe
echo.
echo   Gebruik:
echo     1. Kopieer dist\JuridischAssistent.exe naar een map
echo     2. Dubbelklik om te starten
echo     3. Browser opent automatisch op http://127.0.0.1:5001/
echo.
echo   De database (juridisch.db) wordt aangemaakt in
echo   dezelfde map als de .exe
echo  ================================================
echo.
pause
