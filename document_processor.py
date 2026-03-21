"""
Juridisch Assistent — Document Processor
Extraheert tekst uit PDF, afbeeldingen, DOCX en TXT bestanden.
Server-side equivalent van verwerkBrief() / extractPdfText() / extractImageText() uit meldpunt index.html.
"""
import os
import re
from datetime import datetime

# Optionele imports — graceful fallback als niet geinstalleerd
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import pytesseract
    from PIL import Image
    # Configureer Tesseract pad op Windows
    _tesseract_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for _tp in _tesseract_paths:
        if os.path.isfile(_tp):
            pytesseract.pytesseract.tesseract_cmd = _tp
            break
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


# ---------------------------------------------------------------------------
# Ondersteunde extensies
# ---------------------------------------------------------------------------
PDF_EXTS = {'.pdf'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp', '.gif'}
TXT_EXTS = {'.txt', '.rtf', '.csv'}
DOCX_EXTS = {'.docx', '.doc'}
ALL_EXTS = PDF_EXTS | IMAGE_EXTS | TXT_EXTS | DOCX_EXTS


def detect_type(filepath):
    """Bepaal bestandstype op basis van extensie."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in PDF_EXTS:
        return 'pdf'
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in DOCX_EXTS:
        return 'docx'
    if ext in TXT_EXTS:
        return 'txt'
    return 'unknown'


# ---------------------------------------------------------------------------
# Tekst extractie
# ---------------------------------------------------------------------------
def extract_text(filepath):
    """
    Extraheer tekst uit elk ondersteund bestandstype.
    Returns: {'text': str, 'pages': int, 'method': str, 'error': str|None}
    """
    ftype = detect_type(filepath)

    if ftype == 'pdf':
        return extract_pdf(filepath)
    elif ftype == 'image':
        return extract_image(filepath)
    elif ftype == 'docx':
        return extract_docx(filepath)
    elif ftype == 'txt':
        return extract_txt(filepath)
    else:
        return {'text': '', 'pages': 0, 'method': 'none', 'error': f'Onbekend bestandstype: {os.path.splitext(filepath)[1]}'}


def extract_pdf(filepath):
    """
    Extraheer tekst uit PDF. Probeert eerst pdfplumber (tekst-PDF),
    valt terug op OCR als er weinig tekst uitkomt (gescande PDF).
    """
    if not HAS_PDFPLUMBER:
        return {'text': '', 'pages': 0, 'method': 'none', 'error': 'pdfplumber niet geinstalleerd. Installeer met: pip install pdfplumber'}

    try:
        text_parts = []
        page_count = 0
        with pdfplumber.open(filepath) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text() or ''
                text_parts.append(page_text)

        full_text = '\n\n'.join(text_parts).strip()

        # Als er heel weinig tekst is, is het waarschijnlijk een scan → probeer OCR
        if len(full_text) < 50:
            if HAS_TESSERACT:
                ocr_result = _ocr_pdf(filepath)
                if ocr_result and len(ocr_result) > len(full_text):
                    return {'text': ocr_result, 'pages': page_count, 'method': 'pdf+ocr', 'error': None}
            # Geen tekst en geen OCR mogelijk
            if len(full_text) < 10:
                error = 'Gescande PDF — installeer Tesseract OCR voor tekstherkenning' if not HAS_TESSERACT else 'Geen tekst gevonden (ook niet via OCR)'
                return {'text': full_text, 'pages': page_count, 'method': 'pdfplumber', 'error': error}

        return {'text': full_text, 'pages': page_count, 'method': 'pdfplumber', 'error': None}

    except Exception as e:
        return {'text': '', 'pages': 0, 'method': 'pdfplumber', 'error': str(e)}


def _find_poppler():
    """Vind poppler binaries op Windows."""
    import glob
    search_paths = [
        r'C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\*oppler*\*\Library\bin',
        r'C:\Program Files\poppler*\bin',
        r'C:\Program Files (x86)\poppler*\bin',
        r'C:\poppler*\bin',
    ]
    for pattern in search_paths:
        matches = glob.glob(pattern)
        for m in matches:
            if os.path.isfile(os.path.join(m, 'pdfinfo.exe')):
                return m
    return None

_POPPLER_PATH = _find_poppler()


def _ocr_pdf(filepath):
    """OCR op PDF pagina's via conversie naar afbeeldingen."""
    try:
        from pdf2image import convert_from_path
        kwargs = {'dpi': 300}
        if _POPPLER_PATH:
            kwargs['poppler_path'] = _POPPLER_PATH
        images = convert_from_path(filepath, **kwargs)
        texts = []
        for img in images:
            try:
                text = pytesseract.image_to_string(img, lang='nld')
            except pytesseract.TesseractError:
                text = pytesseract.image_to_string(img, lang='eng')
            texts.append(text)
        return '\n\n'.join(texts).strip()
    except ImportError:
        return ''
    except Exception:
        return ''


def extract_image(filepath):
    """Extraheer tekst uit afbeelding via OCR (Tesseract met Nederlandse taal)."""
    if not HAS_TESSERACT:
        return {'text': '', 'pages': 1, 'method': 'none', 'error': 'pytesseract niet geinstalleerd. Installeer met: pip install pytesseract Pillow'}

    try:
        img = Image.open(filepath)
        # Probeer eerst Nederlands, fallback naar Engels
        try:
            text = pytesseract.image_to_string(img, lang='nld')
        except pytesseract.TesseractError:
            text = pytesseract.image_to_string(img, lang='eng')

        return {'text': text.strip(), 'pages': 1, 'method': 'tesseract', 'error': None}

    except Exception as e:
        return {'text': '', 'pages': 1, 'method': 'tesseract', 'error': str(e)}


def extract_docx(filepath):
    """Extraheer tekst uit DOCX bestand."""
    if not HAS_DOCX:
        return {'text': '', 'pages': 0, 'method': 'none', 'error': 'python-docx niet geinstalleerd. Installeer met: pip install python-docx'}

    try:
        doc = DocxDocument(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = '\n\n'.join(paragraphs)

        # Schat pagina's (ca. 3000 tekens per pagina)
        pages = max(1, len(text) // 3000)

        return {'text': text.strip(), 'pages': pages, 'method': 'python-docx', 'error': None}

    except Exception as e:
        return {'text': '', 'pages': 0, 'method': 'python-docx', 'error': str(e)}


def extract_txt(filepath):
    """Lees tekstbestand met encoding-detectie."""
    encodings = ['utf-8', 'latin-1', 'cp1252', 'ascii']

    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                text = f.read()
            return {'text': text.strip(), 'pages': max(1, len(text) // 3000), 'method': f'txt ({enc})', 'error': None}
        except (UnicodeDecodeError, UnicodeError):
            continue

    return {'text': '', 'pages': 0, 'method': 'txt', 'error': 'Kan bestand niet lezen (encoding-probleem)'}


# ---------------------------------------------------------------------------
# Metadata extractie uit Nederlandse juridische documenten
# ---------------------------------------------------------------------------
MAANDEN_NL = {
    'januari': '01', 'februari': '02', 'maart': '03', 'april': '04',
    'mei': '05', 'juni': '06', 'juli': '07', 'augustus': '08',
    'september': '09', 'oktober': '10', 'november': '11', 'december': '12'
}

def extract_metadata(text):
    """
    Extraheer gestructureerde metadata uit Nederlandse juridische tekst.
    Returns: dict met gevonden referenties, datums, BSN-indicatoren etc.
    """
    meta = {
        'kenmerk': [],
        'datums': [],
        'bsn_detected': False,
        'afzender': '',
        'onderwerp': '',
        'bezwaartermijn_mentioned': False,
        'wetsartikelen': [],
    }

    if not text:
        return meta

    # Kenmerk / referentienummer
    kenmerk_patterns = [
        r'[Kk]enmerk[:\s]+([A-Z0-9/\-\.]+)',
        r'[Rr]eferentie(?:nummer)?[:\s]+([A-Z0-9/\-\.]+)',
        r'[Oo]ns\s+kenmerk[:\s]+([A-Z0-9/\-\.]+)',
        r'[Zz]aaknummer[:\s]+([A-Z0-9/\-\.]+)',
    ]
    for pat in kenmerk_patterns:
        for m in re.finditer(pat, text):
            meta['kenmerk'].append(m.group(1))

    # Datums (DD maand JJJJ formaat)
    datum_pat = r'(\d{1,2})\s+(' + '|'.join(MAANDEN_NL.keys()) + r')\s+(\d{4})'
    for m in re.finditer(datum_pat, text, re.IGNORECASE):
        dag, maand, jaar = m.group(1), m.group(2).lower(), m.group(3)
        if maand in MAANDEN_NL:
            meta['datums'].append(f'{jaar}-{MAANDEN_NL[maand]}-{dag.zfill(2)}')

    # Datums (DD-MM-JJJJ formaat)
    for m in re.finditer(r'(\d{2})[-/](\d{2})[-/](\d{4})', text):
        meta['datums'].append(f'{m.group(3)}-{m.group(2)}-{m.group(1)}')

    # BSN detectie (9 cijfers die eruitzien als BSN)
    if re.search(r'\b\d{9}\b', text):
        meta['bsn_detected'] = True

    # Bezwaartermijn
    if re.search(r'bezwaar(?:termijn|schrift)?', text, re.IGNORECASE):
        meta['bezwaartermijn_mentioned'] = True

    # Wetsartikelen
    art_patterns = [
        r'[Aa]rt(?:ikel)?\.?\s*(\d+[a-z]?)(?:\s+(?:lid\s+\d+|Awb|Wob|Woo|BW|Rv|Sv|Sr|Gr\.?w\.?))?',
    ]
    for pat in art_patterns:
        for m in re.finditer(pat, text):
            meta['wetsartikelen'].append(m.group(0).strip())

    # Onderwerp (zoek "Betreft:" of "Onderwerp:")
    betreft = re.search(r'(?:Betreft|Onderwerp)[:\s]+(.+?)(?:\n|$)', text, re.IGNORECASE)
    if betreft:
        meta['onderwerp'] = betreft.group(1).strip()[:200]

    # Deduplicate
    meta['kenmerk'] = list(dict.fromkeys(meta['kenmerk']))
    meta['datums'] = sorted(list(dict.fromkeys(meta['datums'])))
    meta['wetsartikelen'] = list(dict.fromkeys(meta['wetsartikelen']))[:20]

    return meta


# ---------------------------------------------------------------------------
# Map scanner
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Metadata auto-detectie
# ---------------------------------------------------------------------------
# Patronen voor documenttype herkenning uit bestandsnaam
_DOC_TYPE_PATTERNS = [
    # (regex_pattern, doc_type)
    (r'(?i)(e-?mail|mail|inbox|verzonden|sent|outlook)', 'email'),
    (r'(?i)(brief|schrijven|correspondentie|aanschrijving)', 'brief'),
    (r'(?i)(beschikking|besluit|beslissing|toekenning|afwijzing)', 'beschikking'),
    (r'(?i)(bezwaar)', 'bezwaarschrift'),
    (r'(?i)(beroep)', 'beroepschrift'),
    (r'(?i)(klacht)', 'klacht'),
    (r'(?i)(dagvaarding|exploit)', 'dagvaarding'),
    (r'(?i)(vonnis|uitspraak)', 'uitspraak'),
    (r'(?i)(verzoek|aanvraag)', 'verzoek'),
    (r'(?i)(factuur|rekening|nota)', 'factuur'),
    (r'(?i)(rapport|verslag|notitie)', 'rapport'),
    (r'(?i)(formulier|form)', 'formulier'),
    (r'(?i)(contract|overeenkomst)', 'contract'),
    (r'(?i)(scan|kopie)', 'scan'),
    (r'(?i)(foto|screenshot|afbeelding|img|image|photo)', 'foto'),
    (r'(?i)(aangifte)', 'aangifte'),
    (r'(?i)(woo|wob|openbaarheid)', 'woo'),
    (r'(?i)(bewijs|bijlage)', 'bijlage'),
]

# Patronen voor richting (inkomend/uitgaand)
_RICHTING_PATTERNS = [
    (r'(?i)(ontvangen|inkomend|inbox|van\s)', 'inkomend'),
    (r'(?i)(verzonden|uitgaand|outbox|sent|aan\s)', 'uitgaand'),
]

# Patronen voor datum uit bestandsnaam
_DATUM_PATTERNS = [
    # 2024-01-15, 2024_01_15, 20240115
    r'(20\d{2})[_\-\.]?(0[1-9]|1[0-2])[_\-\.]?(0[1-9]|[12]\d|3[01])',
    # 15-01-2024, 15_01_2024
    r'(0[1-9]|[12]\d|3[01])[_\-\.]?(0[1-9]|1[0-2])[_\-\.]?(20\d{2})',
]


def detect_file_metadata(filepath):
    """
    Detecteer metadata automatisch uit bestandsnaam, extensie en bestandseigenschappen.

    Returns: dict met gevulde metadata velden
    """
    fname = os.path.basename(filepath)
    name_no_ext = os.path.splitext(fname)[0]
    ext = os.path.splitext(fname)[1].lower()
    meta = {}

    # 1. Document type uit extensie
    if ext in IMAGE_EXTS:
        meta['doc_type'] = 'scan'  # Default voor afbeeldingen
    elif ext == '.txt':
        meta['doc_type'] = 'tekst'
    elif ext == '.pdf':
        meta['doc_type'] = ''  # Kan alles zijn
    elif ext in DOCX_EXTS:
        meta['doc_type'] = ''

    # 2. Document type uit bestandsnaam (overschrijft default)
    for pattern, dtype in _DOC_TYPE_PATTERNS:
        if re.search(pattern, name_no_ext):
            meta['doc_type'] = dtype
            break

    # 3. Richting (inkomend/uitgaand)
    for pattern, richting in _RICHTING_PATTERNS:
        if re.search(pattern, name_no_ext):
            meta['doc_richting'] = richting
            break

    # 4. Datum uit bestandsnaam
    for dp in _DATUM_PATTERNS:
        m = re.search(dp, name_no_ext)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                y, mo, d = groups
                # Check of het YMD of DMY is
                if int(y) > 31:  # YMD
                    meta['doc_datum'] = f'{y}-{mo}-{d}'
                else:  # DMY
                    meta['doc_datum'] = f'{groups[2]}-{groups[1]}-{groups[0]}'
                break

    # 5. Bestandsdatum van schijf
    try:
        stat = os.stat(filepath)
        mod_time = datetime.fromtimestamp(stat.st_mtime)
        meta['file_modified'] = mod_time.strftime('%Y-%m-%d %H:%M')

        # Als geen datum uit bestandsnaam, gebruik bestandsdatum
        if 'doc_datum' not in meta:
            meta['doc_datum'] = mod_time.strftime('%Y-%m-%d')
    except OSError:
        pass

    # 6. Onderwerp raden uit bestandsnaam (opgeschoond)
    # Verwijder datums, underscores, nummers aan begin/eind
    cleaned = name_no_ext
    for dp in _DATUM_PATTERNS:
        cleaned = re.sub(dp, '', cleaned)
    cleaned = re.sub(r'[_\-]+', ' ', cleaned).strip()
    cleaned = re.sub(r'^\d+\s*', '', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if cleaned and len(cleaned) > 2:
        meta['doc_onderwerp'] = cleaned

    return meta


def scan_folder(folder_path):
    """
    Scan een map recursief voor ondersteunde documenten.
    Returns: lijst van {'path': str, 'name': str, 'type': str, 'size': int}
    """
    if not os.path.isdir(folder_path):
        return []

    results = []
    for root, dirs, files in os.walk(folder_path):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for fname in sorted(files):
            if fname.startswith('.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALL_EXTS:
                continue

            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                size = 0

            meta = detect_file_metadata(fpath)
            results.append({
                'path': fpath,
                'name': fname,
                'type': detect_type(fpath),
                'size': size,
                'meta': meta,
            })

    return results


# ---------------------------------------------------------------------------
# Capabilities check
# ---------------------------------------------------------------------------
def check_capabilities():
    """Check welke extractie-methoden beschikbaar zijn."""
    caps = {
        'pdf': HAS_PDFPLUMBER,
        'ocr': HAS_TESSERACT,
        'docx': HAS_DOCX,
        'txt': True,  # Altijd beschikbaar
    }

    # Check Tesseract binary
    if HAS_TESSERACT:
        try:
            pytesseract.get_tesseract_version()
            caps['tesseract_version'] = str(pytesseract.get_tesseract_version())
        except Exception:
            caps['ocr'] = False
            caps['tesseract_error'] = 'Tesseract-OCR binary niet gevonden. Installeer van https://github.com/tesseract-ocr/tesseract'

    # Check Nederlandse taaldata
    if caps['ocr']:
        try:
            langs = pytesseract.get_languages()
            caps['tesseract_nld'] = 'nld' in langs
        except Exception:
            caps['tesseract_nld'] = False

    return caps
