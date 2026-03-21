"""
Juridisch Assistent — Database Modellen
SQLite-gebaseerd, alles lokaal. Patroon: string PKs, to_dict(), JSON kolommen.
"""
import uuid
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def maak_id():
    """Genereer uniek ID: timestamp + UUID fragment."""
    return f"id_{int(datetime.now(timezone.utc).timestamp()*1000)}_{uuid.uuid4().hex[:8]}"


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Zaak — Een juridische zaak/dossier
# ---------------------------------------------------------------------------
class Zaak(db.Model):
    __tablename__ = 'zaken'

    id              = db.Column(db.String(100), primary_key=True, default=maak_id)
    naam            = db.Column(db.String(500), nullable=False)
    omschrijving    = db.Column(db.Text, default='')
    procedure_type  = db.Column(db.String(50), default='')       # klacht/bezwaar/beroep/dagvaarding/woo/verzoekschrift/aangifte/art12
    status          = db.Column(db.String(30), default='nieuw')   # nieuw/documenten/anonimisering/analyse/wizard/afgerond
    folder_path     = db.Column(db.Text, default='')              # Absoluut pad naar zaakmap op schijf
    wederpartij     = db.Column(db.String(500), default='')
    instantie       = db.Column(db.String(500), default='')       # Bestuursorgaan / organisatie
    deadline        = db.Column(db.DateTime, nullable=True)
    notities        = db.Column(db.Text, default='')
    samenvatting            = db.Column(db.Text, default='')
    samenvatting_updated_at = db.Column(db.DateTime, nullable=True)
    samenvatting_doc_count  = db.Column(db.Integer, default=0)
    created_at      = db.Column(db.DateTime, default=utcnow)
    updated_at      = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    documenten = db.relationship('Document', backref='zaak', lazy='dynamic',
                                 cascade='all, delete-orphan')
    analyses   = db.relationship('Analyse', backref='zaak', lazy='dynamic',
                                 cascade='all, delete-orphan')

    def to_dict(self, include_docs=False):
        d = {
            'id': self.id,
            'naam': self.naam,
            'omschrijving': self.omschrijving,
            'procedure_type': self.procedure_type,
            'status': self.status,
            'folder_path': self.folder_path,
            'wederpartij': self.wederpartij,
            'instantie': self.instantie,
            'deadline': self.deadline.isoformat() if self.deadline else None,
            'notities': self.notities,
            'samenvatting': self.samenvatting or '',
            'samenvatting_updated_at': self.samenvatting_updated_at.isoformat() if self.samenvatting_updated_at else None,
            'samenvatting_doc_count': self.samenvatting_doc_count or 0,
            'doc_count': self.documenten.count(),
            'email_count': self.emails.count() if self.emails else 0,
            'analyse_count': self.analyses.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_docs:
            d['documenten'] = [doc.to_dict() for doc in self.documenten.all()]
        return d


# ---------------------------------------------------------------------------
# Document — Een document binnen een zaak
# ---------------------------------------------------------------------------
class Document(db.Model):
    __tablename__ = 'documenten'

    id                      = db.Column(db.String(100), primary_key=True, default=maak_id)
    zaak_id                 = db.Column(db.String(100), db.ForeignKey('zaken.id', ondelete='CASCADE'), nullable=False)
    bestandsnaam            = db.Column(db.String(500), nullable=False)
    bestandspad             = db.Column(db.Text, nullable=False)      # Volledig pad op schijf
    bestandstype            = db.Column(db.String(20), default='')    # pdf/image/txt/docx
    bestandsgrootte         = db.Column(db.Integer, default=0)        # Bytes

    # ---- Metadata velden ----
    doc_type                = db.Column(db.String(50), default='')    # brief/email/beschikking/formulier/scan/rapport/overig
    doc_datum               = db.Column(db.String(20), default='')    # Datum van het document (YYYY-MM-DD of vrij tekst)
    doc_afzender            = db.Column(db.String(300), default='')   # Van wie komt het
    doc_ontvanger           = db.Column(db.String(300), default='')   # Aan wie gericht
    doc_kenmerk             = db.Column(db.String(200), default='')   # Referentienummer / kenmerk
    doc_onderwerp           = db.Column(db.String(500), default='')   # Onderwerp / samenvatting
    doc_richting            = db.Column(db.String(20), default='')    # inkomend/uitgaand/intern
    doc_notities            = db.Column(db.Text, default='')          # Vrije notities
    file_modified           = db.Column(db.String(30), default='')    # Laatste wijzigingsdatum bestand op schijf

    # ---- Verwerking ----
    extracted_text          = db.Column(db.Text, default='')
    extraction_done         = db.Column(db.Boolean, default=False)
    anonymized_text         = db.Column(db.Text, default='')
    anonymization_map       = db.Column(db.JSON, default=dict)        # {"Jan de Vries": "Persoon 1", ...}
    anonymization_reviewed  = db.Column(db.Boolean, default=False)
    samenvatting            = db.Column(db.Text, default='')          # Per-document AI samenvatting
    samenvatting_at         = db.Column(db.DateTime, nullable=True)   # Wanneer samenvatting gegenereerd
    metadata_json           = db.Column(db.JSON, default=dict)        # Kenmerk, datums, afzender, etc. (uit extractie)
    created_at              = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'zaak_id': self.zaak_id,
            'bestandsnaam': self.bestandsnaam,
            'bestandspad': self.bestandspad,
            'bestandstype': self.bestandstype,
            'bestandsgrootte': self.bestandsgrootte,
            'doc_type': self.doc_type or '',
            'doc_datum': self.doc_datum or '',
            'doc_afzender': self.doc_afzender or '',
            'doc_ontvanger': self.doc_ontvanger or '',
            'doc_kenmerk': self.doc_kenmerk or '',
            'doc_onderwerp': self.doc_onderwerp or '',
            'doc_richting': self.doc_richting or '',
            'doc_notities': self.doc_notities or '',
            'file_modified': self.file_modified or '',
            'extraction_done': self.extraction_done,
            'has_text': bool(self.extracted_text),
            'text_length': len(self.extracted_text) if self.extracted_text else 0,
            'anonymization_reviewed': self.anonymization_reviewed,
            'has_anonymized': bool(self.anonymized_text),
            'has_samenvatting': bool(self.samenvatting),
            'samenvatting': self.samenvatting or '',
            'samenvatting_at': self.samenvatting_at.isoformat() if self.samenvatting_at else None,
            'pii_count': self.pii_items.count() if self.pii_items else 0,
            'metadata': self.metadata_json or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def to_dict_full(self):
        """Inclusief volledige tekst (voor detail-views)."""
        d = self.to_dict()
        d['extracted_text'] = self.extracted_text or ''
        d['anonymized_text'] = self.anonymized_text or ''
        d['anonymization_map'] = self.anonymization_map or {}
        d['pii_items'] = [p.to_dict() for p in self.pii_items.all()]
        return d


# ---------------------------------------------------------------------------
# Analyse — AI-analyseresultaat
# ---------------------------------------------------------------------------
class Analyse(db.Model):
    __tablename__ = 'analyses'

    id           = db.Column(db.String(100), primary_key=True, default=maak_id)
    zaak_id      = db.Column(db.String(100), db.ForeignKey('zaken.id', ondelete='CASCADE'), nullable=False)
    type         = db.Column(db.String(50), nullable=False)    # procedure_advies / deadline_check / document_draft / uitleg
    input_text   = db.Column(db.Text, default='')              # Wat naar AI is gestuurd (geanonimiseerd)
    result_text  = db.Column(db.Text, default='')              # AI response (tekst)
    result_json  = db.Column(db.JSON, default=dict)            # Gestructureerd resultaat
    ai_provider  = db.Column(db.String(30), default='')        # ollama / openai / anthropic
    ai_model     = db.Column(db.String(100), default='')
    created_at   = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'zaak_id': self.zaak_id,
            'type': self.type,
            'input_text': self.input_text,
            'result_text': self.result_text,
            'result_json': self.result_json or {},
            'ai_provider': self.ai_provider,
            'ai_model': self.ai_model,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Email — Opgeslagen e-mailberichten uit Outlook/Hotmail
# ---------------------------------------------------------------------------
class Email(db.Model):
    __tablename__ = 'emails'

    id                  = db.Column(db.String(100), primary_key=True, default=maak_id)
    graph_id            = db.Column(db.String(500), unique=True, nullable=True)    # Microsoft Graph message ID
    internet_message_id = db.Column(db.String(500), nullable=True)                  # RFC Message-ID
    conversation_id     = db.Column(db.String(500), default='')                     # Voor threading

    onderwerp           = db.Column(db.String(1000), default='')
    van_naam            = db.Column(db.String(300), default='')
    van_email           = db.Column(db.String(300), default='')
    aan                 = db.Column(db.JSON, default=list)                          # ["email1@x.com", "email2@x.com"]
    cc                  = db.Column(db.JSON, default=list)                          # ["email3@x.com"]

    datum               = db.Column(db.DateTime, nullable=True)                     # Ontvangen/verzonden datum
    datum_str           = db.Column(db.String(30), default='')                      # YYYY-MM-DD voor snel zoeken

    body_preview        = db.Column(db.Text, default='')                            # Korte preview (max 300 chars)
    body_text           = db.Column(db.Text, default='')                            # Volledige body als tekst
    body_html           = db.Column(db.Text, default='')                            # Volledige body als HTML

    heeft_bijlagen      = db.Column(db.Boolean, default=False)
    is_gelezen          = db.Column(db.Boolean, default=True)
    importance          = db.Column(db.String(20), default='normal')
    richting            = db.Column(db.String(20), default='')                      # inkomend/uitgaand (bepaald na sync)
    map                 = db.Column(db.String(50), default='inbox')                 # inbox/sent/drafts/etc.

    # Koppeling aan zaak (optioneel, handmatig of automatisch)
    zaak_id             = db.Column(db.String(100), db.ForeignKey('zaken.id', ondelete='SET NULL'), nullable=True)

    synced_at           = db.Column(db.DateTime, default=utcnow)                    # Wanneer gesynchroniseerd
    created_at          = db.Column(db.DateTime, default=utcnow)

    zaak = db.relationship('Zaak', backref=db.backref('emails', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'graph_id': self.graph_id or '',
            'onderwerp': self.onderwerp or '',
            'van_naam': self.van_naam or '',
            'van_email': self.van_email or '',
            'aan': self.aan or [],
            'cc': self.cc or [],
            'datum': self.datum.isoformat() if self.datum else None,
            'datum_str': self.datum_str or '',
            'body_preview': self.body_preview or '',
            'heeft_bijlagen': self.heeft_bijlagen,
            'is_gelezen': self.is_gelezen,
            'importance': self.importance or 'normal',
            'richting': self.richting or '',
            'map': self.map or '',
            'zaak_id': self.zaak_id,
            'zaak_naam': self.zaak.naam if self.zaak else None,
            'synced_at': self.synced_at.isoformat() if self.synced_at else None,
        }

    def to_dict_full(self):
        """Inclusief volledige body."""
        d = self.to_dict()
        d['body_text'] = self.body_text or ''
        d['body_html'] = self.body_html or ''
        return d


# ---------------------------------------------------------------------------
# DocumentPII — Gevonden persoonsgegevens PER document
# ---------------------------------------------------------------------------
class DocumentPII(db.Model):
    __tablename__ = 'document_pii'

    id          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    document_id = db.Column(db.String(100), db.ForeignKey('documenten.id', ondelete='CASCADE'), nullable=False)
    origineel   = db.Column(db.String(500), nullable=False)  # Originele tekst (bijv. "Jan de Vries")
    vervanging  = db.Column(db.String(500), nullable=False)  # Label (bijv. "Persoon 1")
    type        = db.Column(db.String(50), default='naam')   # naam/adres/bsn/telefoon/email/postcode/plaats/organisatie
    created_at  = db.Column(db.DateTime, default=utcnow)

    document = db.relationship('Document', backref=db.backref('pii_items', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'document_id': self.document_id,
            'origineel': self.origineel,
            'vervanging': self.vervanging,
            'type': self.type,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# AnonymisatieRegel — Vaste anonimiseerregels (persistent over zaken heen)
# ---------------------------------------------------------------------------
class AnonymisatieRegel(db.Model):
    __tablename__ = 'anonymisatie_regels'

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    origineel  = db.Column(db.String(500), nullable=False)
    vervanging = db.Column(db.String(500), nullable=False)
    type       = db.Column(db.String(50), default='naam')     # naam/adres/bsn/telefoon/email/organisatie
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'origineel': self.origineel,
            'vervanging': self.vervanging,
            'type': self.type,
        }


# ---------------------------------------------------------------------------
# Instelling — Key-value instellingen (API keys etc. lokaal opgeslagen)
# ---------------------------------------------------------------------------
class Instelling(db.Model):
    __tablename__ = 'instellingen'

    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, default='')

    @staticmethod
    def get(key, default=''):
        row = Instelling.query.get(key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = Instelling.query.get(key)
        if row:
            row.value = value
        else:
            row = Instelling(key=key, value=value)
            db.session.add(row)
        db.session.commit()
