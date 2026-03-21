"""
Juridisch Assistent — Flask Backend
Lokale server op port 5001. Alle data blijft op de computer.
Patroon: meldpunt-ambtenaren/backend/app.py
"""
import os
import re as _re
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from config import Config, BUNDLE_DIR
from models import db, maak_id, Zaak, Document, Analyse, AnonymisatieRegel, DocumentPII, Email, Instelling
from document_processor import extract_text, extract_metadata, scan_folder, detect_type, detect_file_metadata, check_capabilities
from anonymizer import Anonymizer
from ai_local import OllamaClient
from ai_cloud import CloudAIClient
from meldpunt_client import MeldpuntClient

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
TEMPLATE_DIR = os.path.join(BUNDLE_DIR, 'templates')
app = Flask(__name__, static_folder=None, template_folder=TEMPLATE_DIR)
app.config.from_object(Config)
db.init_app(app)

FRONTEND_DIR = TEMPLATE_DIR


# ---------------------------------------------------------------------------
# CORS — nodig voor scanner op GitHub Pages
# ---------------------------------------------------------------------------
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    # Sta GitHub Pages en lokaal toe
    if origin and (origin.endswith('.github.io') or 'localhost' in origin or '127.0.0.1' in origin or '192.168.' in origin or '10.' in origin):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


def _detect_pii_type(replacement_label):
    """Bepaal PII type uit het vervanging-label."""
    rl = replacement_label.lower()
    if 'persoon' in rl:
        return 'naam'
    elif 'adres' in rl:
        return 'adres'
    elif 'email' in rl:
        return 'email'
    elif 'telefoon' in rl:
        return 'telefoon'
    elif 'postcode' in rl:
        return 'postcode'
    elif 'bsn' in rl:
        return 'bsn'
    elif 'iban' in rl:
        return 'iban'
    elif 'plaats' in rl:
        return 'plaats'
    elif 'instantie' in rl:
        return 'organisatie'
    return 'overig'


def get_ollama():
    """Haal OllamaClient op met huidige instellingen."""
    url = Instelling.get('ollama_url', Config.OLLAMA_BASE_URL)
    model = Instelling.get('ollama_model', Config.OLLAMA_MODEL)
    return OllamaClient(base_url=url, model=model)


def get_cloud_ai():
    """Haal CloudAIClient op met huidige instellingen."""
    provider = Instelling.get('ai_provider', Config.CLOUD_AI_PROVIDER)
    model = Instelling.get('ai_model', Config.CLOUD_AI_MODEL)
    if provider == 'anthropic':
        key = Instelling.get('anthropic_api_key', Config.ANTHROPIC_API_KEY)
    else:
        key = Instelling.get('openai_api_key', Config.OPENAI_API_KEY)
    return CloudAIClient(provider=provider, api_key=key, model=model)


def get_meldpunt():
    """Haal MeldpuntClient op."""
    url = Instelling.get('meldpunt_url', Config.MELDPUNT_API_URL)
    return MeldpuntClient(base_url=url)


# ---------------------------------------------------------------------------
# SPA serving (zelfde patroon als meldpunt)
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/scanner')
def scanner_page():
    """Mobiele documentscanner pagina."""
    return send_from_directory(FRONTEND_DIR, 'scanner.html')


@app.route('/<path:path>')
def static_files(path):
    """Serve static files, fallback to index.html voor SPA routing."""
    fpath = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(fpath):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, 'index.html')


def _make_doc(zaak_id, filepath, name=None, ftype=None, size=None, meta=None):
    """Maak een Document object met automatisch gedetecteerde metadata."""
    if meta is None:
        meta = detect_file_metadata(filepath)

    doc = Document(
        id=maak_id(),
        zaak_id=zaak_id,
        bestandsnaam=name or os.path.basename(filepath),
        bestandspad=filepath,
        bestandstype=ftype or detect_type(filepath),
        bestandsgrootte=size if size is not None else (os.path.getsize(filepath) if os.path.isfile(filepath) else 0),
        doc_type=meta.get('doc_type', ''),
        doc_datum=meta.get('doc_datum', ''),
        doc_afzender=meta.get('doc_afzender', ''),
        doc_ontvanger=meta.get('doc_ontvanger', ''),
        doc_kenmerk=meta.get('doc_kenmerk', ''),
        doc_onderwerp=meta.get('doc_onderwerp', ''),
        doc_richting=meta.get('doc_richting', ''),
        file_modified=meta.get('file_modified', ''),
    )
    return doc


# ===================================================================
# BESTANDSSYSTEEM BROWSER
# ===================================================================
@app.route('/api/browse', methods=['GET'])
def browse_folder():
    """Browse het bestandssysteem — mappen en bestanden tonen."""
    path = request.args.get('path', '')

    # Startpunt: common document locations
    if not path:
        home = os.path.expanduser('~')
        locations = []
        for name, subdir in [
            ('Bureaublad', 'Desktop'),
            ('Documenten', 'Documents'),
            ('Downloads', 'Downloads'),
            ('Gebruikersmap', ''),
        ]:
            full = os.path.join(home, subdir) if subdir else home
            if os.path.isdir(full):
                locations.append({'name': name, 'path': full, 'type': 'shortcut'})

        # OneDrive mappen (kunnen meerdere zijn: persoonlijk, werk, etc.)
        import glob
        for od in sorted(glob.glob(os.path.join(home, 'OneDrive*'))):
            if os.path.isdir(od):
                od_name = os.path.basename(od)
                label = 'OneDrive' if od_name == 'OneDrive' else od_name
                locations.append({'name': label, 'path': od, 'type': 'shortcut'})

        # Drives (Windows)
        if os.name == 'nt':
            import string
            for letter in string.ascii_uppercase:
                drive = f'{letter}:\\'
                if os.path.isdir(drive):
                    locations.append({'name': f'{letter}:', 'path': drive, 'type': 'drive'})

        return jsonify({'path': '', 'parent': '', 'items': locations, 'shortcuts': True})

    # Normalize path
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({'error': 'Map niet gevonden'}), 404

    parent = os.path.dirname(path) if path != os.path.dirname(path) else ''

    items = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            try:
                if entry.name.startswith('.') or entry.name.startswith('$'):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    # Tel bestanden in submap
                    try:
                        count = sum(1 for _ in os.scandir(entry.path))
                    except PermissionError:
                        count = 0
                    items.append({
                        'name': entry.name,
                        'path': entry.path,
                        'type': 'folder',
                        'items_count': count,
                    })
                elif entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    size = entry.stat().st_size
                    items.append({
                        'name': entry.name,
                        'path': entry.path,
                        'type': 'file',
                        'ext': ext,
                        'size': size,
                        'supported': ext in Config.SUPPORTED_EXTENSIONS,
                    })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return jsonify({'error': 'Geen toegang tot deze map'}), 403

    return jsonify({'path': path, 'parent': parent, 'items': items, 'shortcuts': False})


@app.route('/api/browse/scan', methods=['POST'])
def browse_scan():
    """Preview: scan een map en toon gevonden documenten (zonder zaak aan te maken)."""
    data = request.get_json(silent=True) or {}
    folder = data.get('path', '')
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Map niet gevonden'}), 404

    files = scan_folder(folder)

    # Groepeer per submap
    by_subfolder = {}
    for f in files:
        rel = os.path.relpath(f['path'], folder)
        parts = rel.replace('\\', '/').split('/')
        subfolder = parts[0] if len(parts) > 1 else '(hoofdmap)'
        if subfolder not in by_subfolder:
            by_subfolder[subfolder] = []
        by_subfolder[subfolder].append(f)

    return jsonify({
        'path': folder,
        'total_files': len(files),
        'total_size': sum(f['size'] for f in files),
        'by_subfolder': {k: {'files': v, 'count': len(v)} for k, v in sorted(by_subfolder.items())},
        'file_types': {},
    })


@app.route('/api/browse/scan-root', methods=['POST'])
def browse_scan_root():
    """Scan een hoofdmap: elke directe submap wordt als aparte zaak getoond.
    Bestanden in de hoofdmap zelf worden als losse zaak gegroepeerd.
    """
    data = request.get_json(silent=True) or {}
    folder = data.get('path', '')
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Map niet gevonden'}), 404

    zaken_preview = []

    # Directe submappen = elk een zaak
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except PermissionError:
        return jsonify({'error': 'Geen toegang tot deze map'}), 403

    for entry in entries:
        if entry.name.startswith('.') or entry.name.startswith('$'):
            continue
        if entry.is_dir(follow_symlinks=False):
            files = scan_folder(entry.path)
            if files:  # Alleen tonen als er documenten in zitten
                zaken_preview.append({
                    'naam': entry.name,
                    'path': entry.path,
                    'files': files,
                    'file_count': len(files),
                    'total_size': sum(f['size'] for f in files),
                })

    # Losse bestanden in de hoofdmap zelf
    losse = []
    for entry in os.scandir(folder):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in Config.SUPPORTED_EXTENSIONS:
                losse.append({
                    'name': entry.name,
                    'path': entry.path,
                    'type': detect_type(entry.path),
                    'size': entry.stat().st_size,
                })
    if losse:
        zaken_preview.insert(0, {
            'naam': '(losse documenten in hoofdmap)',
            'path': folder,
            'files': losse,
            'file_count': len(losse),
            'total_size': sum(f['size'] for f in losse),
            'is_root_files': True,
        })

    total_files = sum(z['file_count'] for z in zaken_preview)
    total_size = sum(z['total_size'] for z in zaken_preview)

    return jsonify({
        'path': folder,
        'zaken_count': len(zaken_preview),
        'total_files': total_files,
        'total_size': total_size,
        'zaken': zaken_preview,
    })


@app.route('/api/import-root', methods=['POST'])
def import_root():
    """Importeer alle submappen als aparte zaken met hun documenten."""
    data = request.get_json(silent=True) or {}
    folder = data.get('path', '')
    selected = data.get('selected', [])  # lijst van submap-paden, of leeg = alles

    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Map niet gevonden'}), 404

    # Sla de hoofdmap op als instelling
    Instelling.set('hoofdmap', folder)

    results = []

    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except PermissionError:
        return jsonify({'error': 'Geen toegang'}), 403

    for entry in entries:
        if entry.name.startswith('.') or entry.name.startswith('$'):
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue

        # Als er een selectie is, alleen die importeren
        if selected and entry.path not in selected:
            continue

        files = scan_folder(entry.path)
        if not files:
            continue

        # Check of er al een zaak bestaat met dit pad
        existing = Zaak.query.filter_by(folder_path=entry.path).first()
        if existing:
            results.append({
                'naam': entry.name,
                'status': 'overgeslagen',
                'reden': 'Zaak bestaat al (id: ' + existing.id + ')',
                'docs': 0,
            })
            continue

        # Maak zaak aan
        zaak = Zaak(
            id=maak_id(),
            naam=entry.name,
            folder_path=entry.path,
        )

        doc_count = 0
        for f in files:
            doc = _make_doc(zaak.id, f['path'], f['name'], f['type'], f['size'], f.get('meta'))
            db.session.add(doc)
            doc_count += 1

        db.session.add(zaak)
        results.append({
            'naam': entry.name,
            'status': 'aangemaakt',
            'zaak_id': zaak.id,
            'docs': doc_count,
        })

    # Losse bestanden in hoofdmap
    if not selected or folder in selected:
        losse = []
        for entry_file in os.scandir(folder):
            if entry_file.is_file():
                ext = os.path.splitext(entry_file.name)[1].lower()
                if ext in Config.SUPPORTED_EXTENSIONS:
                    losse.append(entry_file)

        if losse:
            existing = Zaak.query.filter_by(folder_path=folder).first()
            if not existing:
                zaak = Zaak(
                    id=maak_id(),
                    naam=os.path.basename(folder) + ' (losse documenten)',
                    folder_path=folder,
                )
                for entry_file in losse:
                    doc = _make_doc(zaak.id, entry_file.path)
                    db.session.add(doc)
                db.session.add(zaak)
                results.append({'naam': '(losse documenten)', 'status': 'aangemaakt', 'zaak_id': zaak.id, 'docs': len(losse)})

    db.session.commit()

    created = sum(1 for r in results if r['status'] == 'aangemaakt')
    skipped = sum(1 for r in results if r['status'] == 'overgeslagen')

    return jsonify({
        'ok': True,
        'hoofdmap': folder,
        'created': created,
        'skipped': skipped,
        'total_docs': sum(r['docs'] for r in results),
        'results': results,
    })


# ===================================================================
# ENKELE SUBMAP IMPORTEREN (klik op mapkaart)
# ===================================================================
@app.route('/api/import-single', methods=['POST'])
def import_single():
    """Importeer één submap als zaak. Als de zaak al bestaat, return die."""
    data = request.get_json(silent=True) or {}
    folder = data.get('path', '')
    naam = data.get('naam', '')

    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Map niet gevonden'}), 404

    # Check of er al een zaak bestaat met dit pad
    existing = Zaak.query.filter_by(folder_path=folder).first()
    if existing:
        # Sync: scan map opnieuw voor evt. nieuwe documenten
        files = scan_folder(folder)
        existing_paths = {d.bestandspad for d in existing.documenten.all()}
        added = 0
        for f in files:
            if f['path'] not in existing_paths:
                doc = _make_doc(existing.id, f['path'], f['name'], f['type'], f['size'], f.get('meta'))
                db.session.add(doc)
                added += 1
        if added:
            db.session.commit()
        return jsonify({
            'ok': True,
            'zaak_id': existing.id,
            'created': False,
            'synced': added,
        })

    # Nieuwe zaak aanmaken
    files = scan_folder(folder)
    zaak = Zaak(
        id=maak_id(),
        naam=naam or os.path.basename(folder),
        folder_path=folder,
        status='nieuw',
    )
    db.session.add(zaak)

    for f in files:
        doc = _make_doc(zaak.id, f['path'], f['name'], f['type'], f['size'], f.get('meta'))
        db.session.add(doc)

    db.session.commit()
    return jsonify({
        'ok': True,
        'zaak_id': zaak.id,
        'created': True,
        'docs': len(files),
    }), 201


# ===================================================================
# ZAKEN CRUD
# ===================================================================
@app.route('/api/zaken', methods=['GET'])
def list_zaken():
    """Alle zaken ophalen."""
    zaken = Zaak.query.order_by(Zaak.updated_at.desc()).all()
    return jsonify([z.to_dict() for z in zaken])


@app.route('/api/zaken/by-folders', methods=['POST'])
def zaken_by_folders():
    """Geeft per folder_path de zaak-id terug (als die bestaat).
    Input: { "paths": ["C:\\...", "C:\\..."] }
    Output: { "C:\\...": {"id": "...", "naam": "..."}, ... }
    """
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not paths:
        return jsonify({})
    result = {}
    for z in Zaak.query.filter(Zaak.folder_path.in_(paths)).all():
        result[z.folder_path] = {'id': z.id, 'naam': z.naam, 'status': z.status, 'doc_count': z.documenten.count()}
    return jsonify(result)


@app.route('/api/zaken', methods=['POST'])
def create_zaak():
    """Nieuwe zaak aanmaken."""
    data = request.get_json(silent=True) or {}

    naam = (data.get('naam') or '').strip()
    if not naam:
        return jsonify({'error': 'Naam is verplicht'}), 400

    zaak = Zaak(
        id=maak_id(),
        naam=naam,
        omschrijving=(data.get('omschrijving') or '').strip(),
        folder_path=(data.get('folder_path') or '').strip(),
        wederpartij=(data.get('wederpartij') or '').strip(),
        instantie=(data.get('instantie') or '').strip(),
        notities=(data.get('notities') or '').strip(),
    )

    # Als er een folder is, scan direct documenten
    if zaak.folder_path and os.path.isdir(zaak.folder_path):
        files = scan_folder(zaak.folder_path)
        for f in files:
            doc = _make_doc(zaak.id, f['path'], f['name'], f['type'], f['size'], f.get('meta'))
            db.session.add(doc)

    db.session.add(zaak)
    db.session.commit()
    return jsonify(zaak.to_dict(include_docs=True)), 201


@app.route('/api/zaken/<zaak_id>', methods=['GET'])
def get_zaak(zaak_id):
    """Zaak ophalen met documenten."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404
    return jsonify(zaak.to_dict(include_docs=True))


@app.route('/api/zaken/<zaak_id>', methods=['PUT'])
def update_zaak(zaak_id):
    """Zaak bijwerken."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    for field in ('naam', 'omschrijving', 'procedure_type', 'status', 'folder_path',
                  'wederpartij', 'instantie', 'notities'):
        if field in data:
            setattr(zaak, field, (data[field] or '').strip() if isinstance(data[field], str) else data[field])

    if 'deadline' in data:
        if data['deadline']:
            from datetime import datetime
            try:
                zaak.deadline = datetime.fromisoformat(data['deadline'])
            except (ValueError, TypeError):
                pass
        else:
            zaak.deadline = None

    db.session.commit()
    return jsonify(zaak.to_dict())


@app.route('/api/zaken/<zaak_id>', methods=['DELETE'])
def delete_zaak(zaak_id):
    """Zaak verwijderen (inclusief documenten en analyses)."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    db.session.delete(zaak)
    db.session.commit()
    return jsonify({'ok': True})


# ===================================================================
# DOCUMENTEN
# ===================================================================
@app.route('/api/zaken/<zaak_id>/documenten', methods=['GET'])
def list_documenten(zaak_id):
    """Documenten van een zaak."""
    docs = Document.query.filter_by(zaak_id=zaak_id).order_by(Document.created_at.desc()).all()
    return jsonify([d.to_dict() for d in docs])


@app.route('/api/zaken/<zaak_id>/documenten', methods=['POST'])
def add_documenten(zaak_id):
    """Documenten toevoegen aan zaak (pad-gebaseerd)."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])

    added = []
    for fpath in paths:
        if not os.path.isfile(fpath):
            continue
        # Check of al toegevoegd
        existing = Document.query.filter_by(zaak_id=zaak_id, bestandspad=fpath).first()
        if existing:
            continue

        doc = _make_doc(zaak_id, fpath)
        db.session.add(doc)
        added.append(doc.to_dict())

    db.session.commit()
    return jsonify({'ok': True, 'added': added, 'count': len(added)})


@app.route('/api/zaken/<zaak_id>/scan-folder', methods=['POST'])
def scan_zaak_folder(zaak_id):
    """Scan de zaakmap opnieuw voor nieuwe documenten."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404
    if not zaak.folder_path or not os.path.isdir(zaak.folder_path):
        return jsonify({'error': 'Zaakmap niet gevonden of niet ingesteld'}), 400

    files = scan_folder(zaak.folder_path)
    existing_paths = {d.bestandspad for d in zaak.documenten.all()}

    added = []
    for f in files:
        if f['path'] in existing_paths:
            continue
        doc = _make_doc(zaak_id, f['path'], f['name'], f['type'], f['size'], f.get('meta'))
        db.session.add(doc)
        added.append(f['name'])

    db.session.commit()
    return jsonify({'ok': True, 'added': added, 'count': len(added)})


@app.route('/api/documenten/<doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    """Document verwijderen uit zaak (bestand blijft op schijf)."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    db.session.delete(doc)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/documenten/<doc_id>/metadata', methods=['PUT'])
def update_doc_metadata(doc_id):
    """Metadata van een document bijwerken."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    meta_fields = ['doc_type', 'doc_datum', 'doc_afzender', 'doc_ontvanger',
                   'doc_kenmerk', 'doc_onderwerp', 'doc_richting', 'doc_notities']

    for field in meta_fields:
        if field in data:
            setattr(doc, field, (data[field] or '').strip())

    db.session.commit()
    return jsonify(doc.to_dict())


# ===================================================================
# DOCUMENT VERWERKING (extractie)
# ===================================================================
@app.route('/api/documenten/<doc_id>/extract', methods=['POST'])
def extract_document(doc_id):
    """Extraheer tekst uit document (PDF, image, docx, txt)."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    if not os.path.isfile(doc.bestandspad):
        return jsonify({'error': 'Bestand niet gevonden op schijf'}), 404

    result = extract_text(doc.bestandspad)

    if result['error']:
        return jsonify({'error': result['error']}), 500

    doc.extracted_text = result['text']
    doc.extraction_done = True

    # Extraheer metadata
    meta = extract_metadata(result['text'])
    doc.metadata_json = meta

    db.session.commit()

    return jsonify({
        'ok': True,
        'text_length': len(result['text']),
        'pages': result['pages'],
        'method': result['method'],
        'metadata': meta,
    })


@app.route('/api/documenten/<doc_id>/text', methods=['GET'])
def get_document_text(doc_id):
    """Ophalen van geextraheerde tekst."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    return jsonify({
        'id': doc.id,
        'bestandsnaam': doc.bestandsnaam,
        'extracted_text': doc.extracted_text or '',
        'extraction_done': doc.extraction_done,
        'metadata': doc.metadata_json or {},
    })


@app.route('/api/zaken/<zaak_id>/extract-all', methods=['POST'])
def extract_all_documents(zaak_id):
    """Extraheer tekst uit alle documenten van een zaak."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    results = []
    for doc in zaak.documenten.all():
        if doc.extraction_done and doc.extracted_text and doc.extracted_text.strip():
            results.append({'id': doc.id, 'status': 'already_done'})
            continue
        if not os.path.isfile(doc.bestandspad):
            results.append({'id': doc.id, 'status': 'file_not_found'})
            continue

        result = extract_text(doc.bestandspad)
        txt = (result.get('text') or '').strip()
        if txt:
            doc.extracted_text = txt
            doc.extraction_done = True
            doc.metadata_json = extract_metadata(txt)
            results.append({'id': doc.id, 'status': 'ok', 'text_length': len(txt)})
        else:
            results.append({'id': doc.id, 'status': 'error', 'error': result.get('error') or 'Geen tekst gevonden'})

    db.session.commit()
    return jsonify({'ok': True, 'results': results})


# ===================================================================
# DOCUMENT UPLOAD (voor mobiele scanner)
# ===================================================================
@app.route('/api/zaken/<zaak_id>/upload', methods=['POST'])
def upload_documents(zaak_id):
    """Upload bestanden (images/PDF) naar een zaak. Kan meerdere images samenvoegen als PDF."""
    from PIL import Image as PILImage

    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404
    if not zaak.folder_path:
        return jsonify({'error': 'Zaak heeft geen map ingesteld'}), 400

    # Zorg dat de map bestaat
    os.makedirs(zaak.folder_path, exist_ok=True)

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Geen bestanden ontvangen'}), 400

    combine_pdf = request.form.get('combine_pdf', 'false') == 'true'
    filename = request.form.get('filename', '').strip()

    if combine_pdf and len(files) >= 1:
        # Combineer alle images tot één PDF
        if not filename:
            filename = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        if not filename.lower().endswith('.pdf'):
            filename += '.pdf'

        filepath = os.path.join(zaak.folder_path, filename)

        # Vermijd overschrijven
        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            filename = os.path.basename(filepath)
            counter += 1

        # Open alle images en converteer naar RGB
        pil_images = []
        for f in files:
            try:
                img = PILImage.open(f.stream)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                pil_images.append(img)
            except Exception as e:
                return jsonify({'error': f'Kon afbeelding niet verwerken: {e}'}), 400

        if not pil_images:
            return jsonify({'error': 'Geen geldige afbeeldingen ontvangen'}), 400

        # Sla op als PDF
        if len(pil_images) == 1:
            pil_images[0].save(filepath, 'PDF', resolution=200.0)
        else:
            pil_images[0].save(filepath, 'PDF', resolution=200.0,
                               save_all=True, append_images=pil_images[1:])

        # Maak Document record
        doc = _make_doc(zaak_id, filepath, name=filename)
        db.session.add(doc)
        db.session.commit()

        return jsonify({
            'ok': True,
            'filename': filename,
            'filepath': filepath,
            'pages': len(pil_images),
            'size': os.path.getsize(filepath),
            'document': doc.to_dict(),
        })

    else:
        # Upload individuele bestanden
        results = []
        for f in files:
            fname = f.filename or f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            filepath = os.path.join(zaak.folder_path, fname)

            # Vermijd overschrijven
            base, ext = os.path.splitext(filepath)
            counter = 1
            while os.path.exists(filepath):
                filepath = f"{base}_{counter}{ext}"
                counter += 1

            f.save(filepath)
            doc = _make_doc(zaak_id, filepath, name=os.path.basename(filepath))
            db.session.add(doc)
            results.append(doc.to_dict())

        db.session.commit()
        return jsonify({'ok': True, 'documents': results})


# ===================================================================
# ANONIMISERING
# ===================================================================
@app.route('/api/documenten/<doc_id>/anonymize', methods=['POST'])
def anonymize_document(doc_id):
    """Anonimiseer een document (regex + contextpatronen + optioneel Ollama).

    Gevonden PII (namen, emails, adressen, telefoon) wordt automatisch
    opgeslagen als AnonymisatieRegel zodat het hergebruikt kan worden
    over alle zaken heen.
    """
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    if not doc.extracted_text:
        return jsonify({'error': 'Document heeft nog geen geextraheerde tekst. Eerst extractie uitvoeren.'}), 400

    data = request.get_json(silent=True) or {}
    use_ollama = data.get('use_ollama', True)

    # Haal bestaande mapping op (als er al eerder geanonimiseerd is)
    existing_map = doc.anonymization_map or {}

    # Haal ook vaste regels op
    vaste_regels = {r.origineel: r.vervanging for r in AnonymisatieRegel.query.all()}
    merged_map = {**vaste_regels, **existing_map}

    # Initialiseer anonymizer
    ollama = get_ollama() if use_ollama else None
    anon = Anonymizer(ollama_client=ollama)

    # Run pipeline
    if use_ollama and ollama and ollama.is_available():
        anonymized, mapping = anon.anonymize(doc.extracted_text, merged_map)
    else:
        anonymized, mapping = anon.regex_only(doc.extracted_text, merged_map)

    doc.anonymized_text = anonymized
    doc.anonymization_map = mapping
    doc.anonymization_reviewed = False  # Reset review status

    # --- Sla gevonden PII op PER DOCUMENT (volledige mapping) ---
    # Verwijder oude PII-items voor dit document (worden opnieuw aangemaakt)
    DocumentPII.query.filter_by(document_id=doc.id).delete()

    # Gebruik found_pii als basis, maar vul aan vanuit de volledige mapping
    # zodat ook items uit bestaande regels per document worden opgeslagen
    pii_lookup = {orig: (ptype, label) for orig, ptype, label in anon.found_pii}
    doc_pii_count = 0
    for orig, replacement in mapping.items():
        if len(orig) <= 2:
            continue
        # Bepaal type: uit found_pii of afleiden uit replacement label
        if orig in pii_lookup:
            ptype = pii_lookup[orig][0]
        else:
            ptype = _detect_pii_type(replacement)
        pii_item = DocumentPII(
            document_id=doc.id,
            origineel=orig,
            vervanging=replacement,
            type=ptype,
        )
        db.session.add(pii_item)
        doc_pii_count += 1

    # --- Sla ook op als herbruikbare anonimisatieregels (globaal) ---
    new_rules = 0
    bestaande_originals = {r.origineel for r in AnonymisatieRegel.query.all()}
    for orig, ptype, label in anon.found_pii:
        if orig not in bestaande_originals and len(orig) > 2:
            regel = AnonymisatieRegel(
                origineel=orig,
                vervanging=label,
                type=ptype,
            )
            db.session.add(regel)
            bestaande_originals.add(orig)
            new_rules += 1

    db.session.commit()

    stats = Anonymizer.mapping_stats(mapping)

    return jsonify({
        'ok': True,
        'anonymized_length': len(anonymized),
        'mapping_count': len(mapping),
        'stats': stats,
        'new_rules_saved': new_rules,
        'doc_pii_saved': doc_pii_count,
        'used_ollama': use_ollama and ollama is not None and ollama.is_available(),
    })


@app.route('/api/documenten/<doc_id>/anonymized', methods=['GET'])
def get_anonymized(doc_id):
    """Haal geanonimiseerde tekst + mapping op."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    pii_items = [p.to_dict() for p in DocumentPII.query.filter_by(document_id=doc.id).all()]
    return jsonify({
        'id': doc.id,
        'bestandsnaam': doc.bestandsnaam,
        'extracted_text': doc.extracted_text or '',
        'anonymized_text': doc.anonymized_text or '',
        'anonymization_map': doc.anonymization_map or {},
        'anonymization_reviewed': doc.anonymization_reviewed,
        'stats': Anonymizer.mapping_stats(doc.anonymization_map or {}),
        'pii_items': pii_items,
    })


@app.route('/api/documenten/<doc_id>/anonymized', methods=['PUT'])
def update_anonymized(doc_id):
    """Werk anonimisering bij (gebruiker past mapping aan)."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    data = request.get_json(silent=True) or {}

    if 'anonymization_map' in data:
        doc.anonymization_map = data['anonymization_map']
        # Pas de anonimisering opnieuw toe met de bijgewerkte mapping
        if doc.extracted_text:
            result = doc.extracted_text
            for orig, repl in sorted(data['anonymization_map'].items(), key=lambda x: -len(x[0])):
                result = result.replace(orig, repl)
            doc.anonymized_text = result

    if 'anonymization_reviewed' in data:
        doc.anonymization_reviewed = bool(data['anonymization_reviewed'])

    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/documenten/<doc_id>/approve-anon', methods=['POST'])
def approve_anonymization(doc_id):
    """Markeer anonimisering als gereviewed/goedgekeurd."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404
    doc.anonymization_reviewed = True
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/zaken/<zaak_id>/anonymize-all', methods=['POST'])
def anonymize_all_documents(zaak_id):
    """Anonimiseer alle documenten van een zaak."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    use_ollama = data.get('use_ollama', True)

    vaste_regels = {r.origineel: r.vervanging for r in AnonymisatieRegel.query.all()}
    ollama = None
    if use_ollama:
        ollama = get_ollama()
        if not ollama.is_available():
            return jsonify({'error': 'Ollama is niet bereikbaar. Start Ollama of kies "Alleen regex".'}), 400
        models = ollama.list_models()
        if not models:
            return jsonify({'error': 'Geen Ollama model gevonden. Installeer een model met: ollama pull llama3.1'}), 400

    bestaande_originals = {r.origineel for r in AnonymisatieRegel.query.all()}
    total_new_rules = 0

    results = []
    for doc in zaak.documenten.all():
        # Automatisch tekst extraheren als dat nog niet is gebeurd
        if not doc.extracted_text:
            if doc.bestandspad and os.path.isfile(doc.bestandspad):
                ext_result = extract_text(doc.bestandspad)
                if not ext_result['error'] and ext_result['text']:
                    doc.extracted_text = ext_result['text']
                    doc.extraction_done = True
                    doc.metadata_json = extract_metadata(ext_result['text'])
                else:
                    err_msg = ext_result.get('error') or 'Geen tekst gevonden in bestand'
                    results.append({'id': doc.id, 'bestandsnaam': doc.bestandsnaam, 'status': 'no_text',
                                    'error': err_msg})
                    continue
            else:
                results.append({'id': doc.id, 'bestandsnaam': doc.bestandsnaam, 'status': 'no_text',
                                'error': 'Bestand niet gevonden op schijf'})
                continue

        anon = Anonymizer(ollama_client=ollama)
        existing_map = {**vaste_regels, **(doc.anonymization_map or {})}

        if use_ollama and ollama and ollama.is_available():
            anonymized, mapping = anon.anonymize(doc.extracted_text, existing_map)
        else:
            anonymized, mapping = anon.regex_only(doc.extracted_text, existing_map)

        doc.anonymized_text = anonymized
        doc.anonymization_map = mapping
        doc.anonymization_reviewed = False

        # Sla gevonden PII op PER DOCUMENT (volledige mapping)
        DocumentPII.query.filter_by(document_id=doc.id).delete()
        pii_lookup = {orig: (ptype, label) for orig, ptype, label in anon.found_pii}
        doc_pii_count = 0
        for orig, replacement in mapping.items():
            if len(orig) <= 2:
                continue
            ptype = pii_lookup[orig][0] if orig in pii_lookup else _detect_pii_type(replacement)
            pii_item = DocumentPII(
                document_id=doc.id,
                origineel=orig,
                vervanging=replacement,
                type=ptype,
            )
            db.session.add(pii_item)
            doc_pii_count += 1

        # Sla ook op als herbruikbare regels (globaal)
        for orig, ptype, label in anon.found_pii:
            if orig not in bestaande_originals and len(orig) > 2:
                regel = AnonymisatieRegel(origineel=orig, vervanging=label, type=ptype)
                db.session.add(regel)
                bestaande_originals.add(orig)
                total_new_rules += 1

        results.append({'id': doc.id, 'bestandsnaam': doc.bestandsnaam, 'status': 'ok', 'mapping_count': len(mapping), 'pii_count': doc_pii_count})

    db.session.commit()
    return jsonify({'ok': True, 'results': results, 'new_rules_saved': total_new_rules})


# ===================================================================
# PERSOONSGEGEVENS PER DOCUMENT
# ===================================================================
@app.route('/api/documenten/<doc_id>/pii', methods=['GET'])
def get_document_pii(doc_id):
    """Haal alle gevonden persoonsgegevens op voor een specifiek document."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    pii_items = DocumentPII.query.filter_by(document_id=doc_id).all()

    # Groepeer per type
    by_type = {}
    for p in pii_items:
        t = p.type or 'overig'
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(p.to_dict())

    return jsonify({
        'document_id': doc_id,
        'bestandsnaam': doc.bestandsnaam,
        'total': len(pii_items),
        'by_type': by_type,
        'items': [p.to_dict() for p in pii_items],
    })


@app.route('/api/documenten/<doc_id>/pii/<int:pii_id>', methods=['DELETE'])
def delete_document_pii(doc_id, pii_id):
    """Verwijder een PII-item van een document."""
    pii = DocumentPII.query.get(pii_id)
    if not pii or pii.document_id != doc_id:
        return jsonify({'error': 'PII-item niet gevonden'}), 404
    db.session.delete(pii)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/documenten/<doc_id>/pii', methods=['POST'])
def add_document_pii(doc_id):
    """Voeg handmatig een PII-item toe aan een document."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    origineel = data.get('origineel', '').strip()
    vervanging = data.get('vervanging', '').strip()
    ptype = data.get('type', 'naam')

    if not origineel or not vervanging:
        return jsonify({'error': 'origineel en vervanging zijn verplicht'}), 400

    pii = DocumentPII(
        document_id=doc_id,
        origineel=origineel,
        vervanging=vervanging,
        type=ptype,
    )
    db.session.add(pii)
    db.session.commit()
    return jsonify({'ok': True, 'pii': pii.to_dict()})


@app.route('/api/zaken/<zaak_id>/pii', methods=['GET'])
def get_zaak_pii(zaak_id):
    """Haal alle gevonden persoonsgegevens op voor alle documenten van een zaak."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    docs = zaak.documenten.all()
    all_pii = []
    per_document = {}

    for doc in docs:
        pii_items = DocumentPII.query.filter_by(document_id=doc.id).all()
        doc_pii = [p.to_dict() for p in pii_items]
        if doc_pii:
            per_document[doc.id] = {
                'bestandsnaam': doc.bestandsnaam,
                'pii_count': len(doc_pii),
                'items': doc_pii,
            }
        all_pii.extend(doc_pii)

    # Unieke persoonsgegevens over alle documenten
    unique_pii = {}
    for p in all_pii:
        key = p['origineel']
        if key not in unique_pii:
            unique_pii[key] = {'origineel': p['origineel'], 'vervanging': p['vervanging'], 'type': p['type'], 'documenten': []}
        unique_pii[key]['documenten'].append(p['document_id'])

    return jsonify({
        'zaak_id': zaak_id,
        'total_items': len(all_pii),
        'unique_items': len(unique_pii),
        'per_document': per_document,
        'unique': list(unique_pii.values()),
    })


# ===================================================================
# SAMENVATTING (Ollama lokaal, cloud als fallback)
# ===================================================================
@app.route('/api/zaken/<zaak_id>/samenvatting', methods=['POST'])
def generate_samenvatting(zaak_id):
    """Genereer of hergenereer een samenvatting van alle geanonimiseerde documenten."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    ollama = get_ollama()
    cloud = get_cloud_ai()
    use_cloud = data.get('force_cloud', False)

    if use_cloud and cloud.is_configured():
        ai = cloud
    elif ollama.is_available() and ollama.list_models():
        ai = ollama
    elif cloud.is_configured():
        ai = cloud
    else:
        return jsonify({'error': 'Geen AI beschikbaar. Installeer een Ollama model (ollama pull llama3.1) of stel een cloud API key in via Instellingen.'}), 400

    # Verzamel documenten met geanonimiseerde tekst
    docs = zaak.documenten.filter_by(anonymization_reviewed=True).all()
    if not docs:
        docs = [d for d in zaak.documenten.all() if d.anonymized_text and d.anonymized_text.strip()]
    if not docs:
        return jsonify({'error': 'Geen geanonimiseerde documenten gevonden. Anonimiseer eerst de documenten.'}), 400

    combined_text = '\n\n---\n\n'.join([
        f"Document: {d.bestandsnaam}\n{d.anonymized_text}"
        for d in docs if d.anonymized_text and d.anonymized_text.strip()
    ])

    if not combined_text.strip():
        return jsonify({'error': 'Geen tekst om samen te vatten.'}), 400

    try:
        result_text = ai.generate_summary(combined_text)
    except Exception as e:
        return jsonify({'error': f'AI fout: {e}'}), 500

    if not result_text or not result_text.strip():
        return jsonify({'error': 'AI gaf een leeg antwoord. Controleer of Ollama draait en een model geladen is (ollama pull llama3.1).'}), 500

    zaak.samenvatting = result_text
    zaak.samenvatting_updated_at = datetime.now()
    zaak.samenvatting_doc_count = len(docs)
    db.session.commit()

    return jsonify({
        'samenvatting': zaak.samenvatting,
        'samenvatting_updated_at': zaak.samenvatting_updated_at.isoformat(),
        'samenvatting_doc_count': zaak.samenvatting_doc_count,
    })


@app.route('/api/zaken/<zaak_id>/samenvatting', methods=['PUT'])
def update_samenvatting(zaak_id):
    """Samenvatting handmatig aanpassen."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    tekst = data.get('samenvatting', '').strip()
    if not tekst:
        return jsonify({'error': 'Samenvatting mag niet leeg zijn.'}), 400

    zaak.samenvatting = tekst
    zaak.samenvatting_updated_at = datetime.now()
    db.session.commit()

    return jsonify({
        'samenvatting': zaak.samenvatting,
        'samenvatting_updated_at': zaak.samenvatting_updated_at.isoformat(),
        'samenvatting_doc_count': zaak.samenvatting_doc_count,
    })


# ===================================================================
# PER-DOCUMENT SAMENVATTING
# ===================================================================
@app.route('/api/documenten/<doc_id>/samenvatting', methods=['POST'])
def generate_doc_samenvatting(doc_id):
    """Volledige pipeline: extraheer → anonimiseer → samenvat.

    Retourneert stap-voor-stap status zodat de UI kan tonen wat er gedaan is.
    """
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    steps = []  # Houdt bij welke stappen zijn uitgevoerd

    # ── STAP 1: Tekst extraheren ──
    if not doc.extracted_text or not doc.extracted_text.strip():
        if not os.path.isfile(doc.bestandspad):
            return jsonify({'error': 'Bestand niet gevonden op schijf: ' + doc.bestandspad, 'steps': steps}), 400
        try:
            result = extract_text(doc.bestandspad)
            txt = (result.get('text') or '').strip()
            if not txt:
                err = result.get('error') or 'Geen tekst gevonden'
                return jsonify({'error': f"Extractie: {err}", 'steps': steps}), 400
            # Tekst gevonden — sla op (ook als er een warning mee kwam)
            doc.extracted_text = txt
            doc.extraction_done = True
            doc.metadata_json = extract_metadata(txt)
            db.session.commit()
            detail = f"{len(txt)} tekens ({result['method']})"
            if result.get('error'):
                detail += f" — waarschuwing: {result['error']}"
            steps.append({'step': 'extractie', 'status': 'ok', 'detail': detail})
        except Exception as e:
            return jsonify({'error': f'Extractie fout: {e}', 'steps': steps}), 500
    else:
        steps.append({'step': 'extractie', 'status': 'al_gedaan', 'detail': f"{len(doc.extracted_text)} tekens"})

    # ── STAP 2: Anonimiseren ──
    if not doc.anonymized_text or not doc.anonymized_text.strip():
        try:
            vaste_regels = {r.origineel: r.vervanging for r in AnonymisatieRegel.query.all()}
            existing_map = {**vaste_regels, **(doc.anonymization_map or {})}
            anon = Anonymizer(ollama_client=None)  # Alleen regex voor snelheid
            anonymized, mapping = anon.regex_only(doc.extracted_text, existing_map)

            doc.anonymized_text = anonymized
            doc.anonymization_map = mapping
            doc.anonymization_reviewed = False

            # Sla PII op per document
            DocumentPII.query.filter_by(document_id=doc.id).delete()
            pii_lookup = {orig: (ptype, label) for orig, ptype, label in anon.found_pii}
            for orig, replacement in mapping.items():
                if len(orig) <= 2:
                    continue
                ptype = pii_lookup[orig][0] if orig in pii_lookup else _detect_pii_type(replacement)
                db.session.add(DocumentPII(document_id=doc.id, origineel=orig, vervanging=replacement, type=ptype))

            # Sla nieuwe regels op globaal
            bestaande_originals = {r.origineel for r in AnonymisatieRegel.query.all()}
            for orig, ptype, label in anon.found_pii:
                if orig not in bestaande_originals and len(orig) > 2:
                    db.session.add(AnonymisatieRegel(origineel=orig, vervanging=label, type=ptype))
                    bestaande_originals.add(orig)

            db.session.commit()
            steps.append({'step': 'anonimisering', 'status': 'ok', 'detail': f"{len(mapping)} items geanonimiseerd"})
        except Exception as e:
            # Anonimisering gefaald — ga door met originele tekst
            steps.append({'step': 'anonimisering', 'status': 'overgeslagen', 'detail': str(e)})
    else:
        steps.append({'step': 'anonimisering', 'status': 'al_gedaan', 'detail': f"{len(doc.anonymization_map or {})} items"})

    # ── STAP 3: Samenvatten met AI ──
    tekst = (doc.anonymized_text or doc.extracted_text or '').strip()
    if not tekst:
        return jsonify({'error': 'Geen tekst beschikbaar na verwerking.'}), 400

    data = request.get_json(silent=True) or {}
    ollama = get_ollama()
    cloud = get_cloud_ai()
    use_cloud = data.get('force_cloud', False)

    if use_cloud and cloud.is_configured():
        ai = cloud
    elif ollama.is_available() and ollama.list_models():
        ai = ollama
    elif cloud.is_configured():
        ai = cloud
    else:
        return jsonify({'error': 'Geen AI beschikbaar. Installeer Ollama (ollama pull llama3.1) of stel een cloud API key in.'}), 400

    is_anon = bool(doc.anonymized_text)
    prompt = f"""Maak een beknopte samenvatting van het volgende {'geanonimiseerde ' if is_anon else ''}juridische document.

Structureer als volgt:
1. TYPE: Wat voor document is dit? (brief, beschikking, bezwaar, etc.)
2. KERN: Waar gaat dit document over? (2-3 zinnen)
3. BELANGRIJKE PUNTEN: De kernpunten als korte bullets
4. TERMIJNEN: Eventuele deadlines of termijnen
5. ACTIES: Wat moet er eventueel gedaan worden?

Schrijf in helder, zakelijk Nederlands. Gebruik alleen informatie uit het document.

{'GEANONIMISEERD ' if is_anon else ''}DOCUMENT ({doc.bestandsnaam}):
{tekst[:8000]}"""

    try:
        result_text = ai.generate(prompt, system="Je bent een juridisch assistent die documenten samenvat. Wees accuraat en beknopt.")
    except Exception as e:
        return jsonify({'error': f'AI fout: {e}', 'steps': steps}), 500

    if not result_text or not result_text.strip():
        return jsonify({'error': 'AI gaf een leeg antwoord.', 'steps': steps}), 500

    doc.samenvatting = result_text.strip()
    doc.samenvatting_at = datetime.now()
    db.session.commit()
    steps.append({'step': 'samenvatting', 'status': 'ok', 'detail': f"{len(doc.samenvatting)} tekens"})

    return jsonify({
        'id': doc.id,
        'samenvatting': doc.samenvatting,
        'samenvatting_at': doc.samenvatting_at.isoformat(),
        'has_samenvatting': True,
        'has_anonymized': bool(doc.anonymized_text),
        'extraction_done': doc.extraction_done,
        'steps': steps,
    })


@app.route('/api/documenten/<doc_id>/samenvatting', methods=['PUT'])
def update_doc_samenvatting(doc_id):
    """Per-document samenvatting handmatig bewerken."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    tekst = data.get('samenvatting', '').strip()
    if not tekst:
        return jsonify({'error': 'Samenvatting mag niet leeg zijn.'}), 400

    doc.samenvatting = tekst
    doc.samenvatting_at = datetime.now()
    db.session.commit()

    return jsonify({
        'id': doc.id,
        'samenvatting': doc.samenvatting,
        'samenvatting_at': doc.samenvatting_at.isoformat(),
        'has_samenvatting': True,
    })


@app.route('/api/documenten/<doc_id>/samenvatting', methods=['DELETE'])
def delete_doc_samenvatting(doc_id):
    """Verwijder per-document samenvatting."""
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({'error': 'Document niet gevonden'}), 404

    doc.samenvatting = ''
    doc.samenvatting_at = None
    db.session.commit()

    return jsonify({'id': doc.id, 'has_samenvatting': False})


# ===================================================================
# AI ANALYSE (Ollama lokaal, cloud als fallback — alleen geanonimiseerde tekst)
# ===================================================================
@app.route('/api/zaken/<zaak_id>/analyze', methods=['POST'])
def analyze_zaak(zaak_id):
    """Analyseer een zaak met AI (Ollama lokaal, of cloud als fallback)."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    analysis_type = data.get('type', 'procedure_advies')

    # Probeer eerst Ollama (lokaal), anders cloud AI
    ollama = get_ollama()
    cloud = get_cloud_ai()
    use_cloud = data.get('force_cloud', False)

    if use_cloud and cloud.is_configured():
        ai = cloud
    elif ollama.is_available() and ollama.list_models():
        ai = ollama
    elif cloud.is_configured():
        ai = cloud
    else:
        return jsonify({'error': 'Geen AI beschikbaar. Installeer een Ollama model (ollama pull llama3.1) of stel een cloud API key in via Instellingen.'}), 400

    # Verzamel geanonimiseerde tekst
    docs = zaak.documenten.filter_by(anonymization_reviewed=True).all()
    if not docs:
        # Sta ook niet-gereviewed toe als force=True
        if data.get('force'):
            docs = zaak.documenten.filter(Document.anonymized_text != '').all()
        if not docs:
            return jsonify({'error': 'Geen geanonimiseerde documenten gevonden. Anonimiseer eerst en keur goed.'}), 400

    combined_text = '\n\n---\n\n'.join([
        f"Document: {d.bestandsnaam}\n{d.anonymized_text}"
        for d in docs if d.anonymized_text
    ])

    if not combined_text.strip():
        return jsonify({'error': 'Geen tekst om te analyseren.'}), 400

    # Roep juiste analyse-functie aan
    if analysis_type == 'procedure_advies':
        result_text = ai.analyze_procedure(combined_text)
    elif analysis_type == 'deadline_check':
        known_dates = []
        for d in docs:
            if d.metadata_json and d.metadata_json.get('datums'):
                known_dates.extend(d.metadata_json['datums'])
        result_text = ai.check_deadlines(combined_text, known_dates)
    elif analysis_type == 'uitleg':
        result_text = ai.explain_document(combined_text)
    elif analysis_type == 'concept':
        template = data.get('template', '')
        result_text = ai.generate_draft(
            zaak.procedure_type or 'bezwaarschrift',
            combined_text,
            template
        )
    else:
        return jsonify({'error': f'Onbekend analyse-type: {analysis_type}'}), 400

    # Bepaal provider/model info
    if isinstance(ai, OllamaClient):
        ai_provider = 'ollama'
        ai_model = ai.model
    else:
        ai_provider = ai.provider
        ai_model = ai.model

    # Sla analyse op
    analyse = Analyse(
        id=maak_id(),
        zaak_id=zaak_id,
        type=analysis_type,
        input_text=combined_text[:2000] + ('...' if len(combined_text) > 2000 else ''),
        result_text=result_text,
        ai_provider=ai_provider,
        ai_model=ai_model,
    )
    db.session.add(analyse)
    zaak.status = 'analyse'
    db.session.commit()

    return jsonify(analyse.to_dict())


@app.route('/api/zaken/<zaak_id>/analyses', methods=['GET'])
def list_analyses(zaak_id):
    """Alle analyses van een zaak."""
    analyses = Analyse.query.filter_by(zaak_id=zaak_id).order_by(Analyse.created_at.desc()).all()
    return jsonify([a.to_dict() for a in analyses])


# ===================================================================
# PROCEDURES (templates & info)
# ===================================================================
PROCEDURES = [
    # ========== BESTUURSRECHT ==========
    {
        'type': 'klacht',
        'naam': 'Klacht',
        'categorie': 'bestuursrecht',
        'icon': 'record_voice_over',
        'kleur': '#eab308',
        'wettelijke_basis': 'Hoofdstuk 9 Awb (art. 9:1 t/m 9:36)',
        'termijn': 'Geen wettelijke termijn (binnen 1 jaar aanbevolen)',
        'beschrijving': 'Klacht over het gedrag of handelen van een ambtenaar of bestuursorgaan.',
        'uitleg': (
            'Een klacht gaat niet over een besluit, maar over hoe een ambtenaar of bestuursorgaan '
            'zich heeft gedragen. Denk aan: onbeschoft gedrag, niet terugbellen, verkeerde informatie geven, '
            'trage afhandeling, of discriminatie.\n\n'
            'De Algemene wet bestuursrecht (Awb) regelt in Hoofdstuk 9 het klachtrecht. '
            'Iedereen heeft het recht om een klacht in te dienen over de wijze waarop een bestuursorgaan '
            'zich jegens hem of een ander heeft gedragen (art. 9:1 Awb).\n\n'
            'Het bestuursorgaan is verplicht de klacht te behandelen. Er moet een hoorzitting worden '
            'aangeboden (art. 9:10 Awb) en de klacht moet binnen 6 weken worden afgehandeld '
            '(art. 9:11 Awb), met mogelijke verlenging van 4 weken.\n\n'
            'Als u ontevreden bent over de afhandeling kunt u naar de Nationale ombudsman '
            '(art. 9:17 Awb). Die doet een onafhankelijk onderzoek.'
        ),
        'wanneer': (
            'Gebruik een klacht als u vindt dat een ambtenaar of overheidsinstantie zich niet behoorlijk '
            'heeft gedragen. Het gaat om gedrag, niet om de inhoud van een besluit. '
            'Voorbeelden: lange wachttijden, onbereikbaarheid, onjuiste informatieverstrekking, '
            'onbeschofte bejegening, schending van privacy.'
        ),
        'vereisten': [
            'Naam en adres van de klager',
            'Dagtekening (datum)',
            'Omschrijving van de gedraging waartegen de klacht is gericht',
            'Handtekening (art. 9:4 Awb)',
        ],
        'kosten': 'Geen kosten. Een klachtprocedure is gratis.',
        'instantie': 'Het bestuursorgaan zelf, daarna eventueel Nationale ombudsman',
        'stappen': [
            'Klacht schriftelijk opstellen met feiten en data',
            'Indienen bij het bestuursorgaan (per brief of online)',
            'Bestuursorgaan biedt hoorzitting aan (art. 9:10 Awb)',
            'Bestuursorgaan stuurt schriftelijk oordeel binnen 6 weken',
            'Bij ontevredenheid: klacht bij Nationale ombudsman',
        ],
        'vervolg': 'Nationale ombudsman (art. 9:17 Awb)',
        'wetsartikelen': [
            {'artikel': 'Art. 9:1 Awb', 'inhoud': 'Recht om een klacht in te dienen'},
            {'artikel': 'Art. 9:4 Awb', 'inhoud': 'Vormvereisten van de klacht'},
            {'artikel': 'Art. 9:10 Awb', 'inhoud': 'Hoorplicht bij klachtbehandeling'},
            {'artikel': 'Art. 9:11 Awb', 'inhoud': 'Afhandelingstermijn: 6 weken'},
            {'artikel': 'Art. 9:12 Awb', 'inhoud': 'Schriftelijke kennisgeving oordeel'},
            {'artikel': 'Art. 9:17 Awb', 'inhoud': 'Verwijzing naar Nationale ombudsman'},
        ],
        'velden': ['klager', 'verweerder', 'instantie', 'feiten', 'klacht_omschrijving'],
        'tips': [
            'Wees concreet: noem data, namen, tijdstippen',
            'Beschrijf feitelijk wat er is gebeurd, zonder oordeel',
            'Bewaar kopieën van alle correspondentie',
            'Een klacht heeft geen schorsende werking op een besluit',
        ],
    },
    {
        'type': 'bezwaar',
        'naam': 'Bezwaarschrift',
        'categorie': 'bestuursrecht',
        'icon': 'edit_document',
        'kleur': '#3b82f6',
        'wettelijke_basis': 'Art. 6:4 t/m 7:28 Awb',
        'termijn': '6 weken na bekendmaking besluit',
        'beschrijving': 'Bezwaar maken tegen een besluit (beschikking) van een bestuursorgaan.',
        'uitleg': (
            'Een bezwaarschrift is de eerste stap als u het niet eens bent met een besluit van de overheid. '
            'Het gaat altijd om een schriftelijk besluit (beschikking) gericht aan u persoonlijk, '
            'bijvoorbeeld: een afwijzing van een uitkering, een boete, een vergunningsweigering, '
            'of een terugvordering.\n\n'
            'De Awb schrijft voor dat u eerst bezwaar moet maken bij hetzelfde bestuursorgaan dat het '
            'besluit heeft genomen, voordat u naar de rechter kunt (art. 7:1 Awb). Het bestuursorgaan '
            'heroverweegt dan het besluit volledig opnieuw (art. 7:11 Awb).\n\n'
            'BELANGRIJK: De termijn is 6 weken na de dag van bekendmaking van het besluit (art. 6:7 Awb). '
            'Deze termijn is FATAAL — als u te laat bent, wordt uw bezwaar niet-ontvankelijk verklaard. '
            'Tip: dien desnoods een pro forma bezwaar in (kort bezwaar zonder gronden) en vul de gronden '
            'later aan.\n\n'
            'Het bestuursorgaan moet u in de gelegenheid stellen om te worden gehoord (art. 7:2 Awb). '
            'De beslissing op bezwaar moet binnen 6 weken (of 12 weken bij adviescommissie) worden genomen.'
        ),
        'wanneer': (
            'Gebruik een bezwaarschrift als u een schriftelijk besluit (beschikking) heeft ontvangen '
            'waar u het niet mee eens bent. Voorbeelden: afwijzing aanvraag, boetebesluit, '
            'terugvordering, WOZ-beschikking, verkeersboete (WAHV), weigering vergunning.'
        ),
        'vereisten': [
            'Naam en adres van de indiener',
            'Dagtekening (datum)',
            'Omschrijving van het besluit waartegen bezwaar wordt gemaakt',
            'De gronden van het bezwaar (waarom u het niet eens bent)',
            'Handtekening (art. 6:5 Awb)',
        ],
        'kosten': 'Bezwaar is gratis. Geen kosten verbonden aan de procedure.',
        'instantie': 'Het bestuursorgaan dat het besluit heeft genomen',
        'stappen': [
            'Bezwaarschrift opstellen (of pro forma als termijn dreigt)',
            'Indienen binnen 6 weken na bekendmaking besluit',
            'Eventueel gronden aanvullen (na pro forma)',
            'Hoorzitting bijwonen (art. 7:2 Awb)',
            'Beslissing op bezwaar ontvangen (6-12 weken)',
            'Bij afwijzing: beroep bij de bestuursrechter',
        ],
        'vervolg': 'Beroep bij de bestuursrechter (art. 8:1 Awb)',
        'wetsartikelen': [
            {'artikel': 'Art. 6:5 Awb', 'inhoud': 'Vormvereisten bezwaarschrift'},
            {'artikel': 'Art. 6:7 Awb', 'inhoud': 'Bezwaartermijn: 6 weken'},
            {'artikel': 'Art. 6:9 Awb', 'inhoud': 'Tijdigheid: poststempel of ontvangst'},
            {'artikel': 'Art. 6:11 Awb', 'inhoud': 'Verschoonbare termijnoverschrijding'},
            {'artikel': 'Art. 7:1 Awb', 'inhoud': 'Bezwaar verplicht vóór beroep'},
            {'artikel': 'Art. 7:2 Awb', 'inhoud': 'Recht om gehoord te worden'},
            {'artikel': 'Art. 7:10 Awb', 'inhoud': 'Beslistermijn: 6 of 12 weken'},
            {'artikel': 'Art. 7:11 Awb', 'inhoud': 'Volledige heroverweging'},
        ],
        'velden': ['bezwaarmaker', 'bestuursorgaan', 'besluit_datum', 'besluit_kenmerk', 'bezwaar_gronden'],
        'tips': [
            'Dien altijd op tijd in — de 6-wekentermijn is strikt',
            'Stuur een pro forma bezwaar als u meer tijd nodig heeft',
            'Vraag om een voorlopige voorziening als het besluit ernstige gevolgen heeft',
            'Neem het kenmerk van het besluit over in uw bezwaarschrift',
            'U kunt zich laten bijstaan door een gemachtigde',
        ],
    },
    {
        'type': 'beroep',
        'naam': 'Beroepschrift',
        'categorie': 'bestuursrecht',
        'icon': 'account_balance',
        'kleur': '#8b5cf6',
        'wettelijke_basis': 'Art. 8:1 t/m 8:113 Awb',
        'termijn': '6 weken na bekendmaking beslissing op bezwaar',
        'beschrijving': 'Beroep bij de bestuursrechter tegen een beslissing op bezwaar.',
        'uitleg': (
            'Als het bestuursorgaan uw bezwaar heeft afgewezen (of u bent het niet eens met de '
            'beslissing op bezwaar), kunt u in beroep bij de bestuursrechter. De rechter toetst of het '
            'bestuursorgaan het besluit rechtmatig heeft genomen.\n\n'
            'Beroep wordt ingesteld bij de rechtbank, sector bestuursrecht (art. 8:1 Awb). '
            'De termijn is weer 6 weken na bekendmaking van de beslissing op bezwaar. '
            'U betaalt griffierecht.\n\n'
            'De bestuursrechter toetst het besluit aan de wet en aan de algemene beginselen van behoorlijk '
            'bestuur (zoals het zorgvuldigheidsbeginsel, motiveringsbeginsel, evenredigheidsbeginsel). '
            'De rechter kan het besluit vernietigen en het bestuursorgaan opdragen een nieuw besluit te nemen.\n\n'
            'Bij een voorlopige voorziening (art. 8:81 Awb) kunt u de rechter vragen om het besluit te '
            'schorsen totdat er een uitspraak is.'
        ),
        'wanneer': (
            'Gebruik beroep als uw bezwaar is afgewezen of als u het niet eens bent met de beslissing '
            'op bezwaar. Ook bij fictieve weigering (als het bestuursorgaan niet tijdig beslist). '
            'Er zijn uitzonderingen waar u direct in beroep kunt (zonder bezwaar), zoals bij bepaalde '
            'belastingzaken.'
        ),
        'vereisten': [
            'Naam en adres',
            'Dagtekening',
            'Het bestreden besluit (kopie meesturen)',
            'De gronden van het beroep',
            'Handtekening',
            'Griffierecht betalen (na ontvangst nota)',
        ],
        'kosten': 'Griffierecht: ca. \u20ac50 (natuurlijke personen) of \u20ac365 (rechtspersonen). Exacte bedragen worden jaarlijks vastgesteld.',
        'instantie': 'Rechtbank, sector bestuursrecht',
        'stappen': [
            'Beroepschrift opstellen met gronden',
            'Indienen bij de rechtbank binnen 6 weken',
            'Griffierecht betalen na ontvangst nota',
            'Eventueel voorlopige voorziening aanvragen',
            'Verweer van het bestuursorgaan ontvangen',
            'Zitting bijwonen bij de bestuursrechter',
            'Uitspraak ontvangen (meestal binnen 6 weken na zitting)',
        ],
        'vervolg': 'Hoger beroep bij ABRvS, CRvB of CBb',
        'wetsartikelen': [
            {'artikel': 'Art. 8:1 Awb', 'inhoud': 'Recht op beroep bij de bestuursrechter'},
            {'artikel': 'Art. 8:41 Awb', 'inhoud': 'Griffierecht verschuldigd'},
            {'artikel': 'Art. 8:42 Awb', 'inhoud': 'Bestuursorgaan stuurt verweerschrift'},
            {'artikel': 'Art. 8:56 Awb', 'inhoud': 'Oproep voor zitting'},
            {'artikel': 'Art. 8:69 Awb', 'inhoud': 'Rechter oordeelt op grondslag beroepschrift'},
            {'artikel': 'Art. 8:72 Awb', 'inhoud': 'Rechter kan besluit vernietigen'},
            {'artikel': 'Art. 8:81 Awb', 'inhoud': 'Voorlopige voorziening (spoedprocedure)'},
        ],
        'velden': ['appellant', 'verweerder', 'bezwaarbesluit_datum', 'bezwaarbesluit_kenmerk', 'beroepsgronden'],
        'tips': [
            'Stuur altijd een kopie van het bestreden besluit mee',
            'Betaal het griffierecht op tijd — anders wordt uw beroep niet behandeld',
            'Overweeg een voorlopige voorziening bij spoedeisend belang',
            'U kunt ter zitting nog aanvullende argumenten aanvoeren',
            'Bij winst kunt u proceskostenvergoeding vragen (Bpb)',
        ],
    },
    {
        'type': 'hoger_beroep',
        'naam': 'Hoger beroepschrift',
        'categorie': 'bestuursrecht',
        'icon': 'domain',
        'kleur': '#ec4899',
        'wettelijke_basis': 'Diverse (Wet op de Raad van State, Beroepswet, Wet bestuursrechtspraak bedrijfsorganisatie)',
        'termijn': '6 weken na verzending uitspraak',
        'beschrijving': 'Hoger beroep bij de Afdeling bestuursrechtspraak of Centrale Raad van Beroep.',
        'uitleg': (
            'Als de rechtbank uw beroep heeft afgewezen, kunt u in hoger beroep. In het bestuursrecht '
            'zijn er drie hoger-beroepsinstanties:\n\n'
            '1. Afdeling bestuursrechtspraak van de Raad van State (ABRvS) — vreemdelingenrecht, '
            'ruimtelijke ordening, milieu, en andere zaken\n'
            '2. Centrale Raad van Beroep (CRvB) — sociale zekerheid en ambtenarenzaken\n'
            '3. College van Beroep voor het bedrijfsleven (CBb) — economisch bestuursrecht\n\n'
            'In hoger beroep wordt de zaak volledig opnieuw beoordeeld. U kunt nieuwe argumenten en '
            'bewijs aanvoeren. De hoger-beroepsrechter kan de uitspraak van de rechtbank bevestigen, '
            'vernietigen, of zelf in de zaak voorzien.'
        ),
        'wanneer': (
            'Gebruik hoger beroep als de rechtbank uw beroep heeft afgewezen en u vindt dat de '
            'rechtbank het recht verkeerd heeft toegepast of de feiten verkeerd heeft beoordeeld.'
        ),
        'vereisten': [
            'Naam en adres',
            'Dagtekening',
            'De bestreden uitspraak (kopie)',
            'Hoger beroepsgronden',
            'Handtekening',
            'Griffierecht',
        ],
        'kosten': 'Griffierecht hoger beroep: ca. \u20ac136 (natuurlijke personen) of \u20ac548 (rechtspersonen).',
        'instantie': 'ABRvS, CRvB, of CBb (afhankelijk van het rechtsgebied)',
        'stappen': [
            'Hoger beroepschrift opstellen met grieven',
            'Indienen bij de juiste hoger-beroepsinstantie',
            'Griffierecht betalen',
            'Verweer ontvangen',
            'Zitting bijwonen',
            'Uitspraak ontvangen',
        ],
        'vervolg': 'Geen regulier rechtsmiddel meer (eventueel herziening art. 8:119 Awb of EHRM)',
        'wetsartikelen': [
            {'artikel': 'Art. 37 Wet RvS', 'inhoud': 'Hoger beroep bij de Afdeling bestuursrechtspraak'},
            {'artikel': 'Art. 18 Beroepswet', 'inhoud': 'Hoger beroep bij de Centrale Raad van Beroep'},
            {'artikel': 'Art. 20 Wet bestuursrechtspraak bedrijfsorganisatie', 'inhoud': 'Hoger beroep bij het CBb'},
            {'artikel': 'Art. 8:119 Awb', 'inhoud': 'Herziening van een onherroepelijke uitspraak'},
        ],
        'velden': ['appellant', 'verweerder', 'uitspraak_datum', 'uitspraak_kenmerk', 'hoger_beroepsgronden'],
        'tips': [
            'Hoger beroep is de laatste nationale bestuursrechtelijke mogelijkheid',
            'Na hoger beroep staat eventueel het EHRM open (Europees Hof voor de Rechten van de Mens)',
            'Weet bij welke instantie u hoger beroep moet instellen — dit verschilt per rechtsgebied',
        ],
    },
    {
        'type': 'voorlopige_voorziening',
        'naam': 'Voorlopige voorziening',
        'categorie': 'bestuursrecht',
        'icon': 'speed',
        'kleur': '#f97316',
        'wettelijke_basis': 'Art. 8:81 t/m 8:87 Awb',
        'termijn': 'Geen termijn (zolang bezwaar/beroep loopt)',
        'beschrijving': 'Spoedprocedure om een besluit te schorsen in afwachting van bezwaar of beroep.',
        'uitleg': (
            'Een voorlopige voorziening is een spoedprocedure bij de bestuursrechter. U vraagt de '
            'voorzieningenrechter om het bestreden besluit te schorsen totdat er een definitief oordeel '
            'is in de bezwaar- of beroepsprocedure.\n\n'
            'Dit is essentieel als het besluit onherstelbare gevolgen heeft. Bijvoorbeeld: een sloop '
            'die dreigt, een uitzetting, stopzetting van een uitkering, of een last onder dwangsom.\n\n'
            'De voorzieningenrechter maakt een belangenafweging: weegt uw belang zwaarder dan het '
            'belang van het bestuursorgaan bij onmiddellijke uitvoering? Er moet sprake zijn van '
            'onverwijlde spoed (art. 8:81 Awb).\n\n'
            'De rechter kan ook een kortsluiting toepassen (art. 8:86 Awb): direct uitspraak doen '
            'in de hoofdzaak als de zaak zich daarvoor leent.'
        ),
        'wanneer': (
            'Gebruik een voorlopige voorziening als een besluit onherstelbare gevolgen dreigt te hebben '
            'en u niet kunt wachten op de bezwaar- of beroepsprocedure. Er moet sprake zijn van '
            'spoedeisend belang.'
        ),
        'vereisten': [
            'Er moet een bezwaar- of beroepsprocedure lopen (of gelijktijdig worden ingediend)',
            'Er moet sprake zijn van onverwijlde spoed',
            'Naam, adres, dagtekening',
            'Omschrijving van het spoedeisend belang',
            'Griffierecht',
        ],
        'kosten': 'Griffierecht: ca. \u20ac50 (natuurlijke personen).',
        'instantie': 'Voorzieningenrechter van de rechtbank',
        'stappen': [
            'Verzoek om voorlopige voorziening opstellen',
            'Gelijktijdig bezwaar of beroep indienen (als dat nog niet is gebeurd)',
            'Indienen bij de rechtbank',
            'Griffierecht betalen',
            'Zitting (meestal binnen 2 weken)',
            'Uitspraak (vaak direct na zitting)',
        ],
        'vervolg': 'De hoofdzaak (bezwaar of beroep) loopt door',
        'wetsartikelen': [
            {'artikel': 'Art. 8:81 Awb', 'inhoud': 'Bevoegdheid voorzieningenrechter'},
            {'artikel': 'Art. 8:83 Awb', 'inhoud': 'Mondelinge behandeling'},
            {'artikel': 'Art. 8:84 Awb', 'inhoud': 'Uitspraak op verzoek'},
            {'artikel': 'Art. 8:85 Awb', 'inhoud': 'Duur van de voorlopige voorziening'},
            {'artikel': 'Art. 8:86 Awb', 'inhoud': 'Kortsluiting: direct uitspraak in hoofdzaak'},
        ],
        'velden': ['verzoeker', 'verweerder', 'besluit_kenmerk', 'spoedeisend_belang'],
        'tips': [
            'Onderbouw het spoedeisende karakter goed',
            'Zonder lopend bezwaar/beroep wordt uw verzoek afgewezen',
            'De zitting vindt meestal snel plaats (1-2 weken)',
        ],
    },
    {
        'type': 'woo',
        'naam': 'WOO-verzoek',
        'categorie': 'bestuursrecht',
        'icon': 'visibility',
        'kleur': '#06b6d4',
        'wettelijke_basis': 'Wet open overheid (Woo, voorheen Wob)',
        'termijn': 'Geen indieningstermijn (beslistermijn: 4 weken)',
        'beschrijving': 'Verzoek om openbaarmaking van overheidsinformatie.',
        'uitleg': (
            'De Wet open overheid (Woo), die sinds 1 mei 2022 de Wet openbaarheid van bestuur (Wob) '
            'heeft vervangen, geeft iedereen het recht om overheidsinformatie op te vragen.\n\n'
            'U hoeft geen belang aan te tonen: iedereen mag een WOO-verzoek doen (art. 4.1 Woo). '
            'Het bestuursorgaan moet binnen 4 weken beslissen, met mogelijke verlenging van 2 weken '
            '(art. 4.4 Woo).\n\n'
            'Het bestuursorgaan kan openbaarmaking weigeren op grond van uitzonderingsgronden in '
            'art. 5.1 en 5.2 Woo, zoals: persoonsgegevens, bedrijfsgeheimen, veiligheid van de staat, '
            'of het belang van opsporing en vervolging.\n\n'
            'Als het bestuursorgaan niet (volledig) openbaar maakt, kunt u bezwaar maken '
            'tegen dat besluit via de normale bezwaarprocedure.'
        ),
        'wanneer': (
            'Gebruik een WOO-verzoek als u overheidsdocumenten wilt inzien. Denk aan: '
            'interne memo\'s, e-mails, beleidsstukken, verslagen, adviezen, of andere documenten '
            'die betrekking hebben op uw zaak of een kwestie van publiek belang.'
        ),
        'vereisten': [
            'Zo precies mogelijk omschrijven welke informatie u zoekt',
            'Aangeven over welke bestuurlijke aangelegenheid het gaat',
            'Naam en adres (voor het antwoord)',
        ],
        'kosten': 'Gratis. Mogelijk kosten voor kopieën van documenten.',
        'instantie': 'Het bestuursorgaan dat over de informatie beschikt',
        'stappen': [
            'WOO-verzoek zo specifiek mogelijk formuleren',
            'Indienen bij het juiste bestuursorgaan',
            'Bestuursorgaan beslist binnen 4 weken (+2 weken verlenging)',
            'Bij weigering of gedeeltelijke openbaarmaking: bezwaar maken',
            'Eventueel beroep bij de bestuursrechter',
        ],
        'vervolg': 'Bezwaar (art. 7:1 Awb) tegen het WOO-besluit',
        'wetsartikelen': [
            {'artikel': 'Art. 4.1 Woo', 'inhoud': 'Recht om informatie te verzoeken'},
            {'artikel': 'Art. 4.4 Woo', 'inhoud': 'Beslistermijn: 4 weken (+2)'},
            {'artikel': 'Art. 5.1 Woo', 'inhoud': 'Uitzonderingsgronden (absoluut en relatief)'},
            {'artikel': 'Art. 5.2 Woo', 'inhoud': 'Uitzonderingsgronden m.b.t. personen'},
        ],
        'velden': ['verzoeker', 'bestuursorgaan', 'onderwerp', 'gevraagde_documenten'],
        'tips': [
            'Wees zo specifiek mogelijk over welke documenten u zoekt',
            'Noem een tijdsperiode om het verzoek af te bakenen',
            'U hoeft geen reden op te geven voor uw verzoek',
            'Bij te laat beslissen: ingebrekestelling + dwangsom (art. 4:17 Awb)',
        ],
    },
    # ========== CIVIEL RECHT ==========
    {
        'type': 'dagvaarding',
        'naam': 'Dagvaardingsprocedure',
        'categorie': 'civiel_recht',
        'icon': 'description',
        'kleur': '#10b981',
        'wettelijke_basis': 'Art. 45 t/m 82 Rv (Wetboek van Burgerlijke Rechtsvordering)',
        'termijn': 'Verjaringstermijn (meestal 5 jaar, art. 3:307 BW)',
        'beschrijving': 'Civiele procedure via een dagvaarding, uitgebracht door een deurwaarder.',
        'uitleg': (
            'Een dagvaarding is de meest gebruikelijke manier om een civiele procedure te starten. '
            'U (de eiser) laat via een deurwaarder een dagvaarding uitbrengen aan de gedaagde, '
            'waarmee u hem oproept om voor de rechter te verschijnen.\n\n'
            'De dagvaarding moet voldoen aan strenge wettelijke eisen (art. 45 en 111 Rv). '
            'Er moet in staan: wie u bent, wie de gedaagde is, wat u vordert, waarom, '
            'en op welke datum de zaak dient.\n\n'
            'Bij vorderingen tot \u20ac25.000 is de kantonrechter bevoegd (art. 93 Rv). '
            'Daar hoeft u geen advocaat te hebben. Bij hogere bedragen is de civiele rechter bevoegd '
            'en is een advocaat verplicht (art. 79 Rv).\n\n'
            'Na de dagvaarding volgt een schriftelijke ronde (conclusies), eventueel een comparitie '
            '(zitting), en ten slotte een vonnis.'
        ),
        'wanneer': (
            'Gebruik een dagvaarding als u een vordering heeft op een ander persoon of bedrijf. '
            'Voorbeelden: onbetaalde facturen, schadevergoeding, nakoming van een overeenkomst, '
            'onrechtmatige daad (art. 6:162 BW), burenrecht.'
        ),
        'vereisten': [
            'Identiteit eiser en gedaagde',
            'De eis (vordering) met juridische grondslag',
            'De feiten en bewijsmiddelen',
            'Uitbrenging via een gerechtsdeurwaarder (art. 45 Rv)',
            'Advocaat verplicht boven \u20ac25.000 (art. 79 Rv)',
        ],
        'kosten': 'Griffierecht (afhankelijk van vorderingshoogte), deurwaarderskosten, eventueel advocaatkosten.',
        'instantie': 'Kantonrechter (t/m \u20ac25.000) of civiele rechter (boven \u20ac25.000)',
        'stappen': [
            'Dagvaarding (laten) opstellen',
            'Deurwaarder inschakelen voor betekening',
            'Zaak wordt aangebracht bij de rechtbank',
            'Griffierecht betalen',
            'Schriftelijke ronde (conclusies van eis en antwoord)',
            'Comparitie (zitting) bij de rechter',
            'Vonnis',
        ],
        'vervolg': 'Hoger beroep bij het gerechtshof (art. 332 Rv)',
        'wetsartikelen': [
            {'artikel': 'Art. 45 Rv', 'inhoud': 'Vereisten voor een exploit (dagvaarding)'},
            {'artikel': 'Art. 79 Rv', 'inhoud': 'Verplichte procesvertegenwoordiging (advocaat)'},
            {'artikel': 'Art. 93 Rv', 'inhoud': 'Bevoegdheid kantonrechter (t/m \u20ac25.000)'},
            {'artikel': 'Art. 111 Rv', 'inhoud': 'Inhoud van de dagvaarding'},
            {'artikel': 'Art. 332 Rv', 'inhoud': 'Hoger beroep tegen vonnis'},
        ],
        'velden': ['eiser', 'gedaagde', 'vordering', 'grondslag', 'bewijs'],
        'tips': [
            'Bij de kantonrechter (t/m \u20ac25.000) mag u zelf procederen',
            'Stuur altijd een aanmaning/sommatiebrief vóór u dagvaardt',
            'Let op de verjaringstermijn van uw vordering',
            'De verliezer betaalt in principe de proceskosten (art. 237 Rv)',
        ],
    },
    {
        'type': 'verzoekschrift',
        'naam': 'Verzoekschriftprocedure',
        'categorie': 'civiel_recht',
        'icon': 'request_page',
        'kleur': '#14b8a6',
        'wettelijke_basis': 'Art. 261 t/m 291 Rv',
        'termijn': 'Zaaksafhankelijk',
        'beschrijving': 'Verzoekschriftprocedure voor familie-, arbeids- of insolventierechtelijke kwesties.',
        'uitleg': (
            'Bij een verzoekschriftprocedure dient u een verzoek in bij de rechter. Anders dan bij '
            'een dagvaarding hoeft er geen deurwaarder aan te pas te komen — u stuurt het verzoekschrift '
            'rechtstreeks naar de rechtbank.\n\n'
            'Verzoekschriftprocedures worden gebruikt bij: echtscheiding, gezag/omgang, '
            'onderbewindstelling, curatele, naamswijziging, ontbinding arbeidsovereenkomst, '
            'faillissement, schuldsanering (WSNP), en adoptie.\n\n'
            'De rechter beslist bij beschikking (niet bij vonnis). Belanghebbenden worden opgeroepen '
            'en kunnen een verweerschrift indienen.\n\n'
            'Bij veel verzoekschriftprocedures (zoals echtscheiding) is een advocaat verplicht. '
            'Bij de kantonrechter (arbeidsrecht, bewind) kunt u vaak zelf procederen.'
        ),
        'wanneer': (
            'Gebruik een verzoekschrift als de wet dat voorschrijft. In de praktijk: familierecht '
            '(echtscheiding, gezag, adoptie), arbeidsrecht (ontbinding), curatele/bewind, '
            'schuldsanering (WSNP), faillissement.'
        ),
        'vereisten': [
            'Verzoekschrift met gronden en verzoek',
            'Relevante bijlagen',
            'Advocaat vaak verplicht (familierecht)',
            'Griffierecht',
        ],
        'kosten': 'Griffierecht (afhankelijk van type zaak), eventueel advocaatkosten.',
        'instantie': 'Rechtbank (familierecht, arbeid) of kantonrechter (bewind, curatele)',
        'stappen': [
            'Verzoekschrift (laten) opstellen',
            'Indienen bij de rechtbank',
            'Griffierecht betalen',
            'Belanghebbenden kunnen verweerschrift indienen',
            'Mondelinge behandeling (zitting)',
            'Beschikking ontvangen',
        ],
        'vervolg': 'Hoger beroep bij het gerechtshof (art. 358 Rv)',
        'wetsartikelen': [
            {'artikel': 'Art. 261 Rv', 'inhoud': 'Begin van verzoekschriftprocedure'},
            {'artikel': 'Art. 278 Rv', 'inhoud': 'Inhoud van het verzoekschrift'},
            {'artikel': 'Art. 279 Rv', 'inhoud': 'Oproeping belanghebbenden'},
            {'artikel': 'Art. 287 Rv', 'inhoud': 'Beschikking door de rechter'},
            {'artikel': 'Art. 358 Rv', 'inhoud': 'Hoger beroep tegen beschikking'},
        ],
        'velden': ['verzoeker', 'belanghebbende', 'verzoek', 'gronden'],
        'tips': [
            'Controleer of uw type zaak een verzoekschrift of dagvaarding vereist',
            'Bij familierecht is een advocaat altijd verplicht',
            'Belanghebbenden hebben recht op een verweerschrift',
        ],
    },
    # ========== STRAFRECHT ==========
    {
        'type': 'aangifte',
        'naam': 'Aangifte',
        'categorie': 'strafrecht',
        'icon': 'report',
        'kleur': '#ef4444',
        'wettelijke_basis': 'Art. 161 t/m 166 Sv (Wetboek van Strafvordering)',
        'termijn': 'Verjaringstermijn (afhankelijk van het strafbare feit)',
        'beschrijving': 'Aangifte doen bij de politie van een strafbaar feit.',
        'uitleg': (
            'Aangifte is de mededeling aan de politie of het Openbaar Ministerie dat er een strafbaar '
            'feit is gepleegd. Iedereen die kennis draagt van een strafbaar feit is bevoegd daarvan '
            'aangifte te doen (art. 161 Sv).\n\n'
            'Aangifte kan schriftelijk of mondeling (art. 163 Sv). Bij mondelinge aangifte wordt een '
            'proces-verbaal opgemaakt. U kunt aangifte doen bij het politiebureau, online, '
            'of telefonisch (bij bepaalde delicten).\n\n'
            'BELANGRIJK: Aangifte is niet hetzelfde als een melding. Bij aangifte geeft u aan dat u '
            'wilt dat de dader wordt vervolgd. De politie is verplicht uw aangifte op te nemen.\n\n'
            'Het Openbaar Ministerie (OM) beslist over vervolging. Het OM kan seponeren '
            '(niet vervolgen). Als u het daarmee oneens bent, kunt u een art. 12 Sv-klacht indienen.'
        ),
        'wanneer': (
            'Doe aangifte als u slachtoffer bent van een strafbaar feit of als u getuige bent. '
            'Voorbeelden: mishandeling, bedreiging, oplichting, diefstal, fraude, vernieling, '
            'identiteitsfraude, computercriminaliteit, ambtsmisdrijven.'
        ),
        'vereisten': [
            'Beschrijving van het strafbare feit',
            'Wanneer en waar het plaatsvond',
            'Wie de verdachte is (als bekend)',
            'Eventueel bewijs (foto\'s, berichten, getuigen)',
            'Uw identiteitsbewijs (bij aangifte op het bureau)',
        ],
        'kosten': 'Gratis.',
        'instantie': 'Politie (opnemen aangifte), Openbaar Ministerie (beslissing vervolging)',
        'stappen': [
            'Aangifte voorbereiden: feiten, data, bewijs verzamelen',
            'Aangifte doen: bureau, online of telefonisch',
            'Proces-verbaal ondertekenen',
            'Wachten op bericht van politie/OM',
            'Bij sepot: eventueel art. 12 Sv-klacht',
        ],
        'vervolg': 'Art. 12 Sv-klacht bij het gerechtshof (als OM niet vervolgt)',
        'wetsartikelen': [
            {'artikel': 'Art. 161 Sv', 'inhoud': 'Bevoegdheid tot aangifte'},
            {'artikel': 'Art. 163 Sv', 'inhoud': 'Wijze van aangifte (schriftelijk of mondeling)'},
            {'artikel': 'Art. 164 Sv', 'inhoud': 'Klachtdelicten'},
            {'artikel': 'Art. 165 Sv', 'inhoud': 'Aangifteplicht voor ambtenaren'},
            {'artikel': 'Art. 167 Sv', 'inhoud': 'Opportuniteitsbeginsel (OM beslist over vervolging)'},
        ],
        'velden': ['aangever', 'verdachte', 'strafbaar_feit', 'datum_feit', 'bewijs'],
        'tips': [
            'De politie is verplicht uw aangifte op te nemen',
            'Vraag altijd om een kopie van het proces-verbaal',
            'Bewaar bewijs: screenshots, foto\'s, getuigenverklaringen',
            'Bij weigering aangifte op te nemen: klacht bij politie of Nationale ombudsman',
            'Voor ambtsmisdrijven: ook aangifte bij het OM mogelijk (art. 162 Sv)',
        ],
    },
    {
        'type': 'art12',
        'naam': 'Art. 12 Sv-klacht',
        'categorie': 'strafrecht',
        'icon': 'gavel',
        'kleur': '#be185d',
        'wettelijke_basis': 'Art. 12 t/m 13a Sv',
        'termijn': '3 maanden na kennisgeving van niet-vervolging',
        'beschrijving': 'Klacht bij het gerechtshof tegen de beslissing van het OM om niet te vervolgen.',
        'uitleg': (
            'Als het Openbaar Ministerie besluit om een strafbaar feit niet te vervolgen (sepot), '
            'kunt u als rechtstreeks belanghebbende een klaagschrift indienen bij het gerechtshof '
            '(art. 12 Sv).\n\n'
            'Het gerechtshof beoordeelt of het OM terecht heeft afgezien van vervolging. '
            'Het hof hoort u (de klager), eventueel de beklaagde, en het OM. De behandeling vindt '
            'plaats in raadkamer (niet-openbaar).\n\n'
            'Het gerechtshof kan het OM alsnog opdracht geven tot vervolging. Dit is een belangrijk '
            'correctiemechanisme op het opportuniteitsbeginsel van het OM.\n\n'
            'De termijn is 3 maanden na de kennisgeving van de beslissing tot niet-vervolging. '
            'Als u geen kennisgeving heeft ontvangen, kunt u informeren bij het OM of de zaak nog loopt.'
        ),
        'wanneer': (
            'Gebruik een art. 12-klacht als u slachtoffer bent van een strafbaar feit en het OM '
            'besluit om de verdachte niet te vervolgen (sepot). Ook als de politie weigert aangifte '
            'op te nemen of de zaak niet serieus oppakt.'
        ),
        'vereisten': [
            'U moet rechtstreeks belanghebbende zijn (slachtoffer)',
            'Het OM moet hebben besloten niet te vervolgen',
            'Klaagschrift met motivering',
            'Binnen 3 maanden na kennisgeving sepot',
        ],
        'kosten': 'Gratis.',
        'instantie': 'Gerechtshof (raadkamer)',
        'stappen': [
            'Klaagschrift opstellen met motivering',
            'Indienen bij het gerechtshof binnen 3 maanden',
            'Eventueel advocaat-generaal geeft schriftelijk advies',
            'Hoorzitting in raadkamer (u mag uw klacht toelichten)',
            'Beschikking: vervolging bevolen of klacht afgewezen',
        ],
        'vervolg': 'Als het hof vervolging beveelt: het OM moet de verdachte vervolgen',
        'wetsartikelen': [
            {'artikel': 'Art. 12 Sv', 'inhoud': 'Recht op beklag bij het gerechtshof'},
            {'artikel': 'Art. 12a Sv', 'inhoud': 'Termijn: 3 maanden na kennisgeving'},
            {'artikel': 'Art. 12d Sv', 'inhoud': 'Horen van klager, beklaagde en OM'},
            {'artikel': 'Art. 12i Sv', 'inhoud': 'Beschikking: afwijzing of bevel tot vervolging'},
            {'artikel': 'Art. 167 Sv', 'inhoud': 'Opportuniteitsbeginsel (context)'},
        ],
        'velden': ['klager', 'beklaagde', 'strafbaar_feit', 'sepot_datum', 'klacht_gronden'],
        'tips': [
            'U mag zelf procederen, maar een advocaat is aan te raden',
            'Beschrijf duidelijk waarom vervolging in het algemeen belang is',
            'Het gerechtshof behandelt de zaak in raadkamer (niet-openbaar)',
            'Een art. 12-klacht is een krachtig middel tegen passiviteit van het OM',
        ],
    },
]


@app.route('/api/procedures', methods=['GET'])
def list_procedures():
    """Lijst van alle procedure-types."""
    return jsonify(PROCEDURES)


@app.route('/api/procedures/<ptype>/template', methods=['GET'])
def get_procedure_template(ptype):
    """Template voor een specifiek procedure-type."""
    proc = next((p for p in PROCEDURES if p['type'] == ptype), None)
    if not proc:
        return jsonify({'error': 'Procedure-type niet gevonden'}), 404
    return jsonify(proc)


# ===================================================================
# MIJN RECHTSPRAAK (Playwright)
# ===================================================================
@app.route('/api/rechtspraak/start', methods=['POST'])
def rechtspraak_start():
    """Start browser en navigeer naar Mijn Rechtspraak."""
    from rechtspraak import get_automation
    auto = get_automation()
    result = auto.start()
    return jsonify(result)


@app.route('/api/rechtspraak/status', methods=['GET'])
def rechtspraak_status():
    """Check login-status."""
    from rechtspraak import get_automation
    auto = get_automation()
    return jsonify(auto.get_status())


@app.route('/api/rechtspraak/fill', methods=['POST'])
def rechtspraak_fill():
    """Vul formulier in (na login)."""
    from rechtspraak import get_automation
    auto = get_automation()
    data = request.get_json(silent=True) or {}
    result = auto.fill(data)
    return jsonify(result)


@app.route('/api/rechtspraak/stop', methods=['POST'])
def rechtspraak_stop():
    """Sluit browser."""
    from rechtspraak import get_automation
    auto = get_automation()
    result = auto.stop()
    return jsonify(result)


# ===================================================================
# MELDPUNT AMBTENAREN KOPPELING
# ===================================================================
@app.route('/api/meldpunt/check', methods=['GET'])
def meldpunt_check():
    """Check of Meldpunt bereikbaar is."""
    mp = get_meldpunt()
    return jsonify(mp.health())


@app.route('/api/meldpunt/prepare/<zaak_id>', methods=['GET'])
def meldpunt_prepare(zaak_id):
    """Bereid melding voor op basis van zaak-gegevens."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    mp = get_meldpunt()
    docs = zaak.documenten.all()
    melding = mp.build_melding_from_zaak(zaak, docs)
    return jsonify(melding)


@app.route('/api/meldpunt/submit', methods=['POST'])
def meldpunt_submit():
    """Dien melding in bij Meldpunt Ambtenaren."""
    data = request.get_json(silent=True) or {}
    mp = get_meldpunt()
    result = mp.submit_melding(data)
    return jsonify(result)


# ===================================================================
# OUTLOOK E-MAIL KOPPELING (Microsoft Graph API)
# ===================================================================
_outlook_client = None
_auth_flow = None  # Tijdelijke opslag voor OAuth flow state


def get_outlook():
    """Haal OutlookClient op met opgeslagen tokens."""
    global _outlook_client
    from email_client import OutlookClient
    client_id = Instelling.get('ms_client_id', '')
    tokens_json = Instelling.get('ms_tokens', '')
    tokens = json.loads(tokens_json) if tokens_json else {}
    _outlook_client = OutlookClient(client_id=client_id, tokens=tokens)
    return _outlook_client


def _save_outlook_tokens(tokens):
    """Sla tokens op in de database."""
    # Sla geen interne flow objecten op
    safe = {k: v for k, v in tokens.items() if k in ('access_token', 'refresh_token', 'expires_at', 'id_token_claims')}
    Instelling.set('ms_tokens', json.dumps(safe))


@app.route('/api/outlook/status', methods=['GET'])
def outlook_status():
    """Check Outlook verbindingsstatus."""
    client_id = Instelling.get('ms_client_id', '')
    if not client_id:
        return jsonify({'connected': False, 'has_client_id': False, 'reason': 'no_client_id', 'message': 'Geen Microsoft Client ID ingesteld'})

    client = get_outlook()
    if client.is_connected():
        profile = client.get_profile()
        return jsonify({
            'connected': True,
            'has_client_id': True,
            'profile': {
                'email': profile.get('email', '') if profile else '',
                'name': profile.get('name', '') if profile else '',
            },
        })
    return jsonify({'connected': False, 'has_client_id': True, 'reason': 'not_authenticated', 'message': 'Niet ingelogd'})


@app.route('/api/outlook/auth/start', methods=['POST'])
def outlook_auth_start():
    """Start de OAuth device code flow."""
    global _auth_flow
    client = get_outlook()
    result = client.start_device_flow()
    if 'error' in result:
        return jsonify(result), 400

    _auth_flow = result.get('flow')
    return jsonify({
        'user_code': result['user_code'],
        'verification_uri': result['verification_uri'],
        'message': result['message'],
        'expires_in': result['expires_in'],
    })


@app.route('/api/outlook/auth/complete', methods=['POST'])
def outlook_auth_complete():
    """Wacht op voltooiing van device code flow.

    Dit is een BLOKKERENDE call — MSAL pollt intern tot de gebruiker
    inlogt of de flow verloopt (max ~15 min). De frontend maakt
    één request en wacht op het antwoord.
    """
    global _auth_flow
    if not _auth_flow:
        return jsonify({'error': 'Geen actieve login-flow. Start opnieuw.'}), 400

    client = get_outlook()
    flow = _auth_flow
    result = client.complete_device_flow(flow)
    _auth_flow = None  # Flow is geconsumeerd

    if 'error' in result:
        return jsonify(result), 400

    _save_outlook_tokens(result)
    profile = client.get_profile()
    return jsonify({
        'ok': True,
        'profile': {
            'email': profile.get('email', '') if profile else '',
            'name': profile.get('name', '') if profile else '',
        },
    })


@app.route('/api/outlook/auth/callback')
def outlook_auth_callback():
    """OAuth redirect callback (auth code flow alternatief)."""
    global _auth_flow
    if not _auth_flow:
        return 'Geen actieve login-flow', 400

    client = get_outlook()
    result = client.complete_auth_code(_auth_flow, dict(request.args))
    _auth_flow = None

    if 'error' in result:
        return f'<h2>Login mislukt</h2><p>{result["error"]}</p><a href="/">Terug</a>'

    _save_outlook_tokens(result)
    return '<h2>Ingelogd!</h2><p>U kunt dit venster sluiten en teruggaan naar Juridisch Assistent.</p><script>window.close()</script>'


@app.route('/api/outlook/disconnect', methods=['POST'])
def outlook_disconnect():
    """Verbreek Outlook-koppeling (verwijder tokens)."""
    Instelling.set('ms_tokens', '')
    return jsonify({'ok': True})


@app.route('/api/outlook/search', methods=['POST'])
def outlook_search():
    """Zoek e-mails."""
    client = get_outlook()
    if not client.is_connected():
        return jsonify({'error': 'Niet verbonden met Outlook. Log eerst in.'}), 401

    data = request.get_json(silent=True) or {}
    query = data.get('query', '')
    from_date = data.get('from_date', '')
    to_date = data.get('to_date', '')
    top = data.get('top', 50)

    if not query:
        return jsonify({'error': 'Geen zoekterm opgegeven'}), 400

    result = client.search_emails(query, top=top, from_date=from_date or None, to_date=to_date or None)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route('/api/outlook/search-zaak/<zaak_id>', methods=['POST'])
def outlook_search_zaak(zaak_id):
    """Zoek e-mails gerelateerd aan een specifieke zaak."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    client = get_outlook()
    if not client.is_connected():
        return jsonify({'error': 'Niet verbonden met Outlook. Log eerst in.'}), 401

    data = request.get_json(silent=True) or {}
    extra_terms = data.get('extra_terms', [])
    days_back = data.get('days_back', 365 * 3)

    # Bouw zoektermen vanuit zaak-gegevens
    terms = []
    if zaak.naam:
        terms.append(zaak.naam)
    if zaak.wederpartij:
        terms.append(zaak.wederpartij)
    if zaak.instantie:
        terms.append(zaak.instantie)
    terms.extend(extra_terms)

    # Haal ook kenmerken uit documenten
    for doc in zaak.documenten.all():
        if doc.doc_kenmerk:
            terms.append(doc.doc_kenmerk)

    # Dedup
    seen = set()
    unique_terms = []
    for t in terms:
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            unique_terms.append(t)

    result = client.search_emails_for_zaak(
        zaak_naam=zaak.naam,
        wederpartij=zaak.wederpartij,
        extra_terms=unique_terms[2:],  # Skip naam en wederpartij (al apart)
        days_back=days_back,
    )
    return jsonify(result)


@app.route('/api/outlook/email/<message_id>', methods=['GET'])
def outlook_get_email(message_id):
    """Haal een specifiek e-mailbericht op."""
    client = get_outlook()
    if not client.is_connected():
        return jsonify({'error': 'Niet verbonden met Outlook'}), 401
    result = client.get_email(message_id)
    if 'error' in result:
        return jsonify(result), 404
    return jsonify(result)


# ===================================================================
# E-MAIL DATABASE (opgeslagen e-mails)
# ===================================================================
@app.route('/api/emails/sync', methods=['POST'])
def sync_emails():
    """Synchroniseer alle e-mails vanuit Outlook naar de lokale database.

    Body: { "since_date": "YYYY-MM-DD" (optioneel), "max_emails": 5000 (optioneel) }
    """

    client = get_outlook()
    if not client.is_connected():
        return jsonify({'error': 'Niet verbonden met Outlook. Koppel eerst je account in Instellingen.'}), 401

    data = request.get_json(silent=True) or {}
    since_date = data.get('since_date', None)
    max_emails = data.get('max_emails', 5000)

    # Haal het e-mailadres van de gebruiker op (om richting te bepalen)
    user_email = client.get_user_email().lower()

    # Haal alle e-mails op
    result = client.fetch_all_emails(
        top_per_page=100,
        max_emails=max_emails,
        since_date=since_date,
    )

    if 'error' in result:
        return jsonify(result), 500

    # Bestaande graph_ids ophalen voor deduplicatie
    existing_ids = {e.graph_id for e in Email.query.with_entities(Email.graph_id).all() if e.graph_id}

    new_count = 0
    updated_count = 0

    for em in result.get('emails', []):
        gid = em.get('id', '')
        if not gid:
            continue

        # Bepaal richting: uitgaand als van_email == user_email
        from_email = em.get('from_email', '').lower()
        richting = 'uitgaand' if from_email == user_email else 'inkomend'

        # Bepaal datum
        received_str = em.get('received', '') or em.get('sent', '')
        datum = None
        datum_str = ''
        if received_str:
            try:
                # Parse ISO datetime
                datum_str = received_str[:10]
                datum = datetime.fromisoformat(received_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                datum_str = received_str[:10] if len(received_str) >= 10 else ''

        # Body tekst extraheren uit HTML
        body_html = em.get('body', '')
        body_text = ''
        if body_html:
            # Simpele HTML-naar-tekst conversie
            body_text = _re.sub(r'<[^>]+>', ' ', body_html)
            body_text = _re.sub(r'\s+', ' ', body_text).strip()

        if gid in existing_ids:
            # Update bestaande email
            existing = Email.query.filter_by(graph_id=gid).first()
            if existing:
                existing.body_text = body_text[:50000] if body_text else existing.body_text
                existing.body_html = body_html[:100000] if body_html else existing.body_html
                updated_count += 1
            continue

        email_obj = Email(
            graph_id=gid,
            internet_message_id=em.get('internet_message_id', ''),
            conversation_id=em.get('conversation_id', ''),
            onderwerp=em.get('subject', '')[:1000],
            van_naam=em.get('from_name', '')[:300],
            van_email=em.get('from_email', '')[:300],
            aan=em.get('to', []),
            cc=em.get('cc', []),
            datum=datum,
            datum_str=datum_str,
            body_preview=em.get('preview', '')[:300],
            body_text=body_text[:50000],
            body_html=body_html[:100000],
            heeft_bijlagen=em.get('has_attachments', False),
            is_gelezen=em.get('is_read', True),
            importance=em.get('importance', 'normal'),
            richting=richting,
            map=em.get('_folder', 'inbox'),
        )
        db.session.add(email_obj)
        existing_ids.add(gid)
        new_count += 1

    db.session.commit()

    total = Email.query.count()
    return jsonify({
        'ok': True,
        'fetched': result.get('count', 0),
        'new_saved': new_count,
        'updated': updated_count,
        'total_in_db': total,
        'folders_synced': result.get('folders_synced', []),
    })


@app.route('/api/emails', methods=['GET'])
def list_emails():
    """Lijst van alle opgeslagen e-mails met zoeken, filteren en paginering.

    Query params:
      q: zoektekst (in onderwerp, body, afzender)
      zaak_id: filter op zaak
      richting: inkomend/uitgaand
      van: filter op afzender email
      sort: datum_desc (default), datum_asc
      page: paginanummer (1-based)
      per_page: aantal per pagina (default 50)
      unlinked: 1 = alleen emails zonder zaak
    """

    q = request.args.get('q', '').strip()
    zaak_id = request.args.get('zaak_id', '')
    richting = request.args.get('richting', '')
    van = request.args.get('van', '').strip()
    sort = request.args.get('sort', 'datum_desc')
    page = int(request.args.get('page', 1))
    per_page = min(int(request.args.get('per_page', 50)), 200)
    unlinked = request.args.get('unlinked', '')

    query = Email.query

    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                Email.onderwerp.ilike(like),
                Email.body_text.ilike(like),
                Email.van_naam.ilike(like),
                Email.van_email.ilike(like),
                Email.body_preview.ilike(like),
            )
        )

    if zaak_id:
        query = query.filter_by(zaak_id=zaak_id)

    if richting:
        query = query.filter_by(richting=richting)

    if van:
        query = query.filter(Email.van_email.ilike(f'%{van}%'))

    if unlinked == '1':
        query = query.filter(Email.zaak_id.is_(None))

    # Sortering
    if sort == 'datum_asc':
        query = query.order_by(db.case((Email.datum.is_(None), 1), else_=0), Email.datum.asc())
    else:
        query = query.order_by(db.case((Email.datum.is_(None), 1), else_=0), Email.datum.desc())

    total = query.count()
    emails = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'emails': [e.to_dict() for e in emails],
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
    })


@app.route('/api/emails/stats', methods=['GET'])
def email_stats():
    """Statistieken over opgeslagen e-mails."""

    total = Email.query.count()
    linked = Email.query.filter(Email.zaak_id.isnot(None)).count()
    unlinked = total - linked
    inkomend = Email.query.filter_by(richting='inkomend').count()
    uitgaand = Email.query.filter_by(richting='uitgaand').count()

    # Unieke afzenders
    afzenders = db.session.query(
        Email.van_email, db.func.count(Email.id)
    ).group_by(Email.van_email).order_by(db.func.count(Email.id).desc()).limit(20).all()

    return jsonify({
        'total': total,
        'linked': linked,
        'unlinked': unlinked,
        'inkomend': inkomend,
        'uitgaand': uitgaand,
        'top_afzenders': [{'email': a[0], 'count': a[1]} for a in afzenders if a[0]],
    })


@app.route('/api/emails/<email_id>', methods=['GET'])
def get_email_detail(email_id):
    """Haal een specifieke opgeslagen e-mail op (volledige body)."""

    email = Email.query.get(email_id)
    if not email:
        return jsonify({'error': 'E-mail niet gevonden'}), 404
    return jsonify(email.to_dict_full())


@app.route('/api/emails/<email_id>/link', methods=['POST'])
def link_email_to_zaak(email_id):
    """Koppel een e-mail aan een zaak."""

    email = Email.query.get(email_id)
    if not email:
        return jsonify({'error': 'E-mail niet gevonden'}), 404

    data = request.get_json(silent=True) or {}
    zaak_id = data.get('zaak_id', '')

    if zaak_id:
        zaak = Zaak.query.get(zaak_id)
        if not zaak:
            return jsonify({'error': 'Zaak niet gevonden'}), 404
        email.zaak_id = zaak_id
    else:
        email.zaak_id = None  # Ontkoppel

    db.session.commit()
    return jsonify({'ok': True, 'email': email.to_dict()})


@app.route('/api/emails/link-bulk', methods=['POST'])
def link_emails_bulk():
    """Koppel meerdere e-mails aan een zaak."""

    data = request.get_json(silent=True) or {}
    email_ids = data.get('email_ids', [])
    zaak_id = data.get('zaak_id', '')

    if zaak_id:
        zaak = Zaak.query.get(zaak_id)
        if not zaak:
            return jsonify({'error': 'Zaak niet gevonden'}), 404

    count = 0
    for eid in email_ids:
        email = Email.query.get(eid)
        if email:
            email.zaak_id = zaak_id or None
            count += 1

    db.session.commit()
    return jsonify({'ok': True, 'linked': count})


@app.route('/api/emails/auto-link', methods=['POST'])
def auto_link_emails():
    """Automatisch e-mails koppelen aan zaken op basis van zoekopdrachten.

    Doorzoekt onderwerp en body van ongelinkte emails op zaak-namen en wederpartijen.
    """

    zaken = Zaak.query.all()
    total_linked = 0
    results = []

    # Haal alle ongelinkte emails op
    unlinked_emails = Email.query.filter(Email.zaak_id.is_(None)).all()

    for zaak in zaken:
        search_terms = []
        if zaak.naam:
            search_terms.append(zaak.naam.lower())
        if zaak.wederpartij:
            search_terms.append(zaak.wederpartij.lower())
        if zaak.instantie:
            search_terms.append(zaak.instantie.lower())

        if not search_terms:
            continue

        zaak_linked = 0
        for email in unlinked_emails:
            if email.zaak_id:  # Al gekoppeld door eerdere match in deze run
                continue

            # Doorzoek onderwerp en body
            text = f"{email.onderwerp} {email.body_preview} {email.body_text[:2000]}".lower()

            for term in search_terms:
                if len(term) >= 3 and term in text:
                    email.zaak_id = zaak.id
                    zaak_linked += 1
                    total_linked += 1
                    break

        if zaak_linked > 0:
            results.append({'zaak': zaak.naam, 'zaak_id': zaak.id, 'linked': zaak_linked})

    db.session.commit()

    return jsonify({
        'ok': True,
        'total_linked': total_linked,
        'results': results,
        'unlinked_remaining': Email.query.filter(Email.zaak_id.is_(None)).count(),
    })


@app.route('/api/zaken/<zaak_id>/timeline', methods=['GET'])
def zaak_timeline(zaak_id):
    """Gecombineerde tijdlijn: documenten + e-mails, chronologisch gesorteerd."""
    zaak = Zaak.query.get(zaak_id)
    if not zaak:
        return jsonify({'error': 'Zaak niet gevonden'}), 404

    items = []

    # 1. Documenten toevoegen aan tijdlijn
    for doc in zaak.documenten.all():
        datum = doc.doc_datum or doc.file_modified or ''
        if datum:
            # Normaliseer naar YYYY-MM-DD
            datum_sort = datum[:10]
        else:
            datum_sort = '9999-99-99'  # Zonder datum achteraan

        items.append({
            'type': 'document',
            'date': datum[:10] if datum else '',
            'date_sort': datum_sort,
            'title': doc.bestandsnaam,
            'subtitle': doc.doc_onderwerp or '',
            'doc_type': doc.doc_type or '',
            'richting': doc.doc_richting or '',
            'afzender': doc.doc_afzender or '',
            'ontvanger': doc.doc_ontvanger or '',
            'kenmerk': doc.doc_kenmerk or '',
            'doc_id': doc.id,
            'has_text': bool(doc.extracted_text),
        })

    # 2. E-mails toevoegen (uit lokale database — gekoppeld aan deze zaak)
    for em in zaak.emails.all():
        datum_str = em.datum_str or ''
        items.append({
            'type': 'email',
            'date': datum_str,
            'date_sort': datum_str or '9999-99-99',
            'title': em.onderwerp or '',
            'subtitle': em.body_preview or '',
            'from_name': em.van_naam or '',
            'from_email': em.van_email or '',
            'to': em.aan or [],
            'has_attachments': em.heeft_bijlagen,
            'email_id': em.id,
            'importance': em.importance or 'normal',
            'richting': em.richting or '',
        })

    # Sorteer op datum (nieuwste eerst)
    items.sort(key=lambda x: x['date_sort'], reverse=True)

    return jsonify({
        'zaak_id': zaak_id,
        'zaak_naam': zaak.naam,
        'items': items,
        'count': len(items),
    })


# ===================================================================
# ANONYMISATIE REGELS (persistent)
# ===================================================================
@app.route('/api/anonymisatie-regels', methods=['GET'])
def list_anon_regels():
    """Lijst van vaste anonimisatieregels."""
    regels = AnonymisatieRegel.query.all()
    return jsonify([r.to_dict() for r in regels])


@app.route('/api/anonymisatie-regels', methods=['POST'])
def add_anon_regel():
    """Voeg een vaste anonimisatieregel toe."""
    data = request.get_json(silent=True) or {}
    origineel = (data.get('origineel') or '').strip()
    vervanging = (data.get('vervanging') or '').strip()
    if not origineel or not vervanging:
        return jsonify({'error': 'Origineel en vervanging zijn verplicht'}), 400

    regel = AnonymisatieRegel(
        origineel=origineel,
        vervanging=vervanging,
        type=data.get('type', 'naam'),
    )
    db.session.add(regel)
    db.session.commit()
    return jsonify(regel.to_dict()), 201


@app.route('/api/anonymisatie-regels/<int:regel_id>', methods=['DELETE'])
def delete_anon_regel(regel_id):
    """Verwijder een anonimisatieregel."""
    regel = AnonymisatieRegel.query.get(regel_id)
    if not regel:
        return jsonify({'error': 'Regel niet gevonden'}), 404
    db.session.delete(regel)
    db.session.commit()
    return jsonify({'ok': True})


# ===================================================================
# INSTELLINGEN & HEALTH
# ===================================================================
@app.route('/api/health', methods=['GET'])
def health_check():
    """Systeemstatus: Ollama, Cloud AI, Meldpunt, document processing."""
    ollama = get_ollama()
    cloud = get_cloud_ai()
    mp = get_meldpunt()
    caps = check_capabilities()

    # Outlook status
    outlook_info = {'connected': False, 'reason': 'not_configured'}
    try:
        ms_id = Instelling.get('ms_client_id', '')
        if ms_id:
            oc = get_outlook()
            outlook_info = {'connected': oc.is_connected()}
    except Exception:
        pass

    return jsonify({
        'ollama': ollama.health(),
        'cloud_ai': cloud.health(),
        'meldpunt': mp.health(),
        'document_processing': caps,
        'outlook': outlook_info,
    })


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Huidige instellingen ophalen."""
    return jsonify({
        'ollama_url': Instelling.get('ollama_url', Config.OLLAMA_BASE_URL),
        'ollama_model': Instelling.get('ollama_model', Config.OLLAMA_MODEL),
        'ai_provider': Instelling.get('ai_provider', Config.CLOUD_AI_PROVIDER),
        'ai_model': Instelling.get('ai_model', Config.CLOUD_AI_MODEL),
        'openai_api_key': '***' if Instelling.get('openai_api_key', Config.OPENAI_API_KEY) else '',
        'anthropic_api_key': '***' if Instelling.get('anthropic_api_key', Config.ANTHROPIC_API_KEY) else '',
        'meldpunt_url': Instelling.get('meldpunt_url', Config.MELDPUNT_API_URL),
        'hoofdmap': Instelling.get('hoofdmap', ''),
        'ms_client_id': Instelling.get('ms_client_id', ''),
    })


@app.route('/api/settings', methods=['PUT'])
def update_settings():
    """Instellingen bijwerken."""
    data = request.get_json(silent=True) or {}

    allowed = {
        'ollama_url', 'ollama_model', 'ai_provider', 'ai_model',
        'openai_api_key', 'anthropic_api_key', 'meldpunt_url', 'hoofdmap',
        'ms_client_id'
    }

    for key, value in data.items():
        if key in allowed:
            # Sla API keys niet op als ze masked zijn
            if key.endswith('_api_key') and value == '***':
                continue
            Instelling.set(key, str(value).strip())

    return jsonify({'ok': True})


# ===================================================================
# DATABASE MIGRATIE (SQLite → MSSQL)
# ===================================================================
@app.route('/api/migrate/check', methods=['GET'])
def migrate_check():
    """Check of migratie-vereisten aanwezig zijn."""
    from db_migrate import check_requirements
    return jsonify(check_requirements())


@app.route('/api/migrate/test', methods=['POST'])
def migrate_test():
    """Test verbinding met MSSQL server."""
    from db_migrate import build_connection_string, test_connection
    data = request.get_json(silent=True) or {}

    server = data.get('server', 'localhost')
    database = data.get('database', 'JuridischAssistent')
    auth_type = data.get('auth_type', 'windows')
    username = data.get('username', '')
    password = data.get('password', '')
    driver = data.get('driver', '')

    conn_str = build_connection_string(server, database, auth_type, username, password, driver or None)
    result = test_connection(conn_str)
    return jsonify(result)


@app.route('/api/migrate/start', methods=['POST'])
def migrate_start():
    """Start de migratie van SQLite naar MSSQL."""
    from db_migrate import build_connection_string, migrate_to_mssql
    data = request.get_json(silent=True) or {}

    server = data.get('server', 'localhost')
    database = data.get('database', 'JuridischAssistent')
    auth_type = data.get('auth_type', 'windows')
    username = data.get('username', '')
    password = data.get('password', '')
    driver = data.get('driver', '')

    conn_str = build_connection_string(server, database, auth_type, username, password, driver or None)
    result = migrate_to_mssql(db, conn_str)
    return jsonify(result)


@app.route('/api/migrate/export', methods=['POST'])
def migrate_export():
    """Exporteer SQLite database als .sql dump."""
    from db_migrate import export_sqlite_dump
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'juridisch.db')
    try:
        output = export_sqlite_dump(db_path)
        return jsonify({'ok': True, 'file': output})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ===================================================================
# MAIN
# ===================================================================
if __name__ == '__main__':
    import sys

    # Check if running as frozen .exe (PyInstaller)
    is_frozen = getattr(sys, 'frozen', False)

    with app.app_context():
        db.create_all()

        # Auto-migratie: voeg nieuwe kolommen toe aan bestaande databases
        import sqlite3 as _sqlite3
        _db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        _conn = _sqlite3.connect(_db_path)
        _cur = _conn.cursor()
        _cur.execute("PRAGMA table_info(zaken)")
        _existing = {row[1] for row in _cur.fetchall()}
        for _col, _sql in [
            ('samenvatting', "ALTER TABLE zaken ADD COLUMN samenvatting TEXT DEFAULT ''"),
            ('samenvatting_updated_at', "ALTER TABLE zaken ADD COLUMN samenvatting_updated_at DATETIME"),
            ('samenvatting_doc_count', "ALTER TABLE zaken ADD COLUMN samenvatting_doc_count INTEGER DEFAULT 0"),
        ]:
            if _col not in _existing:
                _cur.execute(_sql)
        _conn.commit()

        # Auto-migratie: documenten tabel
        _cur.execute("PRAGMA table_info(documenten)")
        _existing_doc = {row[1] for row in _cur.fetchall()}
        for _col, _sql in [
            ('samenvatting', "ALTER TABLE documenten ADD COLUMN samenvatting TEXT DEFAULT ''"),
            ('samenvatting_at', "ALTER TABLE documenten ADD COLUMN samenvatting_at DATETIME"),
        ]:
            if _col not in _existing_doc:
                _cur.execute(_sql)
        _conn.commit()
        _conn.close()

        print(f"\n  Juridisch Assistent")
        print(f"  http://{Config.HOST}:{Config.PORT}/")
        print(f"  Database: {Config.SQLALCHEMY_DATABASE_URI}")
        print()

    # If running as .exe, auto-open browser
    if is_frozen:
        import webbrowser
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(f'http://{Config.HOST}:{Config.PORT}/')).start()

    app.run(
        debug=not is_frozen,
        host=Config.HOST,
        port=Config.PORT,
        use_reloader=not is_frozen,
    )
