#!/bin/bash
echo ""
echo "  ================================================"
echo "   Juridisch Assistent - Lokale Legal Tool"
echo "   Alle data blijft op uw computer"
echo "  ================================================"
echo ""

cd "$(dirname "$0")"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[FOUT] Python3 niet gevonden. Installeer Python 3.10+"
    exit 1
fi

# Install dependencies
echo "[1/3] Dependencies controleren..."
pip3 install -q -r requirements.txt

# Initialize database
echo "[2/3] Database initialiseren..."
python3 -c "from models import db; from app import app; ctx=app.app_context(); ctx.push(); db.create_all(); print('  Database OK')"

# Check Ollama
echo "[3/3] Ollama controleren..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Ollama OK"
else
    echo ""
    echo "  [WAARSCHUWING] Ollama niet gevonden op localhost:11434"
    echo "  Voor anonimisering met AI: installeer Ollama van https://ollama.ai"
    echo "  Daarna: ollama pull llama3.1"
    echo ""
fi

# Start server
echo ""
echo "  Server starten op http://127.0.0.1:5001/"
echo "  Druk Ctrl+C om te stoppen"
echo ""
python3 app.py
