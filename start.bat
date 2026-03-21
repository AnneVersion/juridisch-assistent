@echo off
title Juridisch Assistent
echo.
echo  ================================================
echo   Juridisch Assistent - Lokale Legal Tool
echo   Alle data blijft op uw computer
echo  ================================================
echo.

cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FOUT] Python niet gevonden. Installeer Python 3.10+ van https://python.org
    pause
    exit /b 1
)

:: Check/install dependencies
echo [1/3] Dependencies controleren...
pip install -q -r requirements.txt

:: Initialize database
echo [2/3] Database initialiseren...
python -c "from models import db; from app import app; ctx=app.app_context(); ctx.push(); db.create_all(); print('  Database OK')"

:: Check Ollama
echo [3/3] Ollama controleren...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WAARSCHUWING] Ollama niet gevonden op localhost:11434
    echo  Voor anonimisering met AI: installeer Ollama van https://ollama.ai
    echo  Daarna: ollama pull llama3.1
    echo.
) else (
    echo   Ollama OK
)

:: Start server
echo.
echo  Server starten op http://127.0.0.1:5001/
echo  Druk Ctrl+C om te stoppen
echo.
python app.py
pause
