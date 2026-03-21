"""
Juridisch Assistent — Configuratie
Alle instellingen met veilige defaults. Gevoelige waarden via environment variables.
"""
import os
import sys
import secrets

# Als we als PyInstaller .exe draaien, is de base dir waar de .exe staat.
# De bundled bestanden (templates/) staan in sys._MEIPASS.
if getattr(sys, 'frozen', False):
    # Running as .exe
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    # Running as script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR


class Config:
    # Flask
    SECRET_KEY = os.environ.get('JA_SECRET_KEY') or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'juridisch.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 MB (documenten kunnen groot zijn)

    # Ollama (lokale AI — anonimisering)
    OLLAMA_BASE_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
    OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.1')

    # Cloud AI (alleen geanonimiseerde tekst)
    CLOUD_AI_PROVIDER = os.environ.get('AI_PROVIDER', 'openai')   # 'openai' of 'anthropic'
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
    CLOUD_AI_MODEL = os.environ.get('AI_MODEL', 'gpt-4o')

    # Meldpunt Ambtenaren koppeling
    MELDPUNT_API_URL = os.environ.get('MELDPUNT_URL', 'http://localhost:5000/api')

    # Paden
    DEFAULT_DATA_DIR = os.path.join(BASE_DIR, 'data')

    # Server
    PORT = int(os.environ.get('PORT', os.environ.get('JA_PORT', 5001)))
    HOST = os.environ.get('JA_HOST', '0.0.0.0')

    # Ondersteunde bestandstypen
    SUPPORTED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif',
                            '.bmp', '.gif', '.txt', '.docx', '.doc', '.rtf'}
