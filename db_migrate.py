"""
DB Migratie Module — SQLite → Microsoft SQL Server

Kopieert alle data van de lokale SQLite database naar een MSSQL server.
Ondersteunt Windows Authentication en SQL Server Authentication.
"""

import logging
from datetime import datetime

log = logging.getLogger(__name__)

# Try importing pyodbc
try:
    import pyodbc
    HAS_PYODBC = True
except ImportError:
    HAS_PYODBC = False


def check_requirements():
    """Controleer of pyodbc beschikbaar is."""
    issues = []
    if not HAS_PYODBC:
        issues.append('pyodbc is niet geinstalleerd. Installeer met: pip install pyodbc')

    # Check for ODBC driver
    if HAS_PYODBC:
        drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
        if not drivers:
            issues.append('Geen SQL Server ODBC driver gevonden. Installeer "ODBC Driver 17 for SQL Server" of nieuwer.')

    return {
        'ready': len(issues) == 0,
        'issues': issues,
        'pyodbc': HAS_PYODBC,
        'odbc_drivers': [d for d in pyodbc.drivers()] if HAS_PYODBC else [],
    }


def build_connection_string(server, database, auth_type='windows', username=None, password=None, driver=None):
    """Bouw een ODBC connection string voor MSSQL."""
    if not driver:
        if HAS_PYODBC:
            sql_drivers = [d for d in pyodbc.drivers() if 'SQL Server' in d]
            # Prefer newest driver
            sql_drivers.sort(reverse=True)
            driver = sql_drivers[0] if sql_drivers else 'ODBC Driver 17 for SQL Server'
        else:
            driver = 'ODBC Driver 17 for SQL Server'

    if auth_type == 'windows':
        return f'DRIVER={{{driver}}};SERVER={server};DATABASE={database};Trusted_Connection=yes;'
    else:
        return f'DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={username};PWD={password};'


def test_connection(connection_string):
    """Test de verbinding met de SQL Server."""
    if not HAS_PYODBC:
        return {'success': False, 'error': 'pyodbc niet geinstalleerd'}

    try:
        conn = pyodbc.connect(connection_string, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        cursor.execute("SELECT DB_NAME()")
        db_name = cursor.fetchone()[0]
        conn.close()
        return {
            'success': True,
            'version': version.split('\n')[0],
            'database': db_name,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def migrate_to_mssql(sqlite_db, connection_string, callback=None):
    """
    Migreer alle data van SQLite naar MSSQL.

    Args:
        sqlite_db: SQLAlchemy db instance (met de huidige SQLite sessie)
        connection_string: MSSQL ODBC connection string
        callback: Optionele callback functie voor voortgang (step, total, message)

    Returns:
        dict met resultaten per tabel
    """
    if not HAS_PYODBC:
        return {'success': False, 'error': 'pyodbc niet geinstalleerd'}

    results = {
        'success': False,
        'tables': {},
        'total_rows': 0,
        'errors': [],
        'started_at': datetime.now().isoformat(),
    }

    def progress(step, total, msg):
        if callback:
            callback(step, total, msg)
        log.info(f'[{step}/{total}] {msg}')

    try:
        conn = pyodbc.connect(connection_string, timeout=30)
        conn.autocommit = False
        cursor = conn.cursor()

        # Define tables and their schemas
        tables = _get_table_definitions()
        total_steps = len(tables) * 2 + 1  # create + insert per table + final
        step = 0

        # Step 1: Create tables
        for table_name, create_sql in tables.items():
            step += 1
            progress(step, total_steps, f'Tabel aanmaken: {table_name}')

            # Drop if exists (for clean migration)
            try:
                cursor.execute(f"IF OBJECT_ID('dbo.{table_name}', 'U') IS NOT NULL DROP TABLE dbo.{table_name}")
            except Exception:
                pass

            cursor.execute(create_sql)

        conn.commit()

        # Step 2: Copy data from SQLite
        from models import Zaak, Document, Analyse, AnonymisatieRegel, Instelling

        model_table_map = [
            ('zaken', Zaak),
            ('documenten', Document),
            ('analyses', Analyse),
            ('anonymisatie_regels', AnonymisatieRegel),
            ('instellingen', Instelling),
        ]

        for table_name, model in model_table_map:
            step += 1

            try:
                rows = model.query.all()
                row_count = len(rows)
                progress(step, total_steps, f'Data kopieren: {table_name} ({row_count} rijen)')

                if row_count == 0:
                    results['tables'][table_name] = {'rows': 0, 'status': 'leeg'}
                    continue

                # Get column names from the model
                columns = [c.name for c in model.__table__.columns]

                # Build INSERT statement
                placeholders = ', '.join(['?' for _ in columns])
                col_names = ', '.join([f'[{c}]' for c in columns])
                insert_sql = f'INSERT INTO dbo.{table_name} ({col_names}) VALUES ({placeholders})'

                # Insert each row
                inserted = 0
                for row in rows:
                    values = []
                    for col in columns:
                        val = getattr(row, col, None)
                        # Convert Python types for MSSQL
                        if isinstance(val, dict) or isinstance(val, list):
                            import json
                            val = json.dumps(val, ensure_ascii=False)
                        values.append(val)

                    try:
                        cursor.execute(insert_sql, values)
                        inserted += 1
                    except Exception as e:
                        results['errors'].append(f'{table_name} rij {inserted}: {str(e)[:200]}')

                results['tables'][table_name] = {'rows': inserted, 'status': 'ok'}
                results['total_rows'] += inserted

            except Exception as e:
                results['tables'][table_name] = {'rows': 0, 'status': 'fout', 'error': str(e)[:200]}
                results['errors'].append(f'Tabel {table_name}: {str(e)[:200]}')

        conn.commit()

        # Final step
        step += 1
        progress(step, total_steps, 'Migratie voltooid!')

        conn.close()
        results['success'] = True
        results['completed_at'] = datetime.now().isoformat()

    except Exception as e:
        results['errors'].append(f'Verbindingsfout: {str(e)}')
        log.error(f'Migratie mislukt: {e}')

    return results


def _get_table_definitions():
    """MSSQL CREATE TABLE statements die overeenkomen met SQLAlchemy modellen."""
    return {
        'zaken': """
            CREATE TABLE dbo.zaken (
                id NVARCHAR(100) PRIMARY KEY,
                naam NVARCHAR(200) NOT NULL,
                omschrijving NVARCHAR(MAX),
                procedure_type NVARCHAR(50),
                status NVARCHAR(50) DEFAULT 'open',
                folder_path NVARCHAR(500),
                wederpartij NVARCHAR(200),
                deadline NVARCHAR(50),
                created_at DATETIME2 DEFAULT GETDATE(),
                updated_at DATETIME2 DEFAULT GETDATE()
            )
        """,
        'documenten': """
            CREATE TABLE dbo.documenten (
                id NVARCHAR(100) PRIMARY KEY,
                zaak_id NVARCHAR(100) REFERENCES dbo.zaken(id),
                bestandsnaam NVARCHAR(300) NOT NULL,
                bestandspad NVARCHAR(500),
                bestandstype NVARCHAR(20),
                bestandsgrootte INT,
                extracted_text NVARCHAR(MAX),
                anonymized_text NVARCHAR(MAX),
                anonymization_map NVARCHAR(MAX),
                anonymization_reviewed BIT DEFAULT 0,
                metadata_json NVARCHAR(MAX),
                created_at DATETIME2 DEFAULT GETDATE()
            )
        """,
        'analyses': """
            CREATE TABLE dbo.analyses (
                id NVARCHAR(100) PRIMARY KEY,
                zaak_id NVARCHAR(100) REFERENCES dbo.zaken(id),
                type NVARCHAR(50) NOT NULL,
                input_text NVARCHAR(MAX),
                result_text NVARCHAR(MAX),
                result_json NVARCHAR(MAX),
                ai_provider NVARCHAR(50),
                ai_model NVARCHAR(100),
                created_at DATETIME2 DEFAULT GETDATE()
            )
        """,
        'anonymisatie_regels': """
            CREATE TABLE dbo.anonymisatie_regels (
                id NVARCHAR(100) PRIMARY KEY,
                origineel NVARCHAR(500) NOT NULL,
                vervanging NVARCHAR(500) NOT NULL,
                type NVARCHAR(50),
                created_at DATETIME2 DEFAULT GETDATE()
            )
        """,
        'instellingen': """
            CREATE TABLE dbo.instellingen (
                id NVARCHAR(100) PRIMARY KEY,
                sleutel NVARCHAR(200) NOT NULL UNIQUE,
                waarde NVARCHAR(MAX),
                updated_at DATETIME2 DEFAULT GETDATE()
            )
        """,
    }


def export_sqlite_dump(sqlite_path, output_path=None):
    """
    Exporteer de SQLite database als een .sql dump file.
    Handig als backup of voor handmatige import.
    """
    import sqlite3

    if not output_path:
        output_path = sqlite_path.replace('.db', '_dump.sql')

    conn = sqlite3.connect(sqlite_path)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f'-- Juridisch Assistent database dump\n')
        f.write(f'-- Gegenereerd: {datetime.now().isoformat()}\n')
        f.write(f'-- Bron: {sqlite_path}\n\n')

        for line in conn.iterdump():
            f.write(f'{line}\n')

    conn.close()
    return output_path
