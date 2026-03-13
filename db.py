import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / 'iphonetools.db'


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_udid TEXT NOT NULL,
                device_name TEXT,
                product_type TEXT,
                ios_version TEXT,
                backup_path TEXT NOT NULL,
                encryption_password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()


def store_backup(device_udid, device_name, product_type, ios_version, backup_path, encryption_password):
    with get_connection() as conn:
        cursor = conn.execute(
            '''INSERT INTO backups
               (device_udid, device_name, product_type, ios_version, backup_path, encryption_password)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (device_udid, device_name, product_type, ios_version, str(backup_path), encryption_password)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_backups():
    with get_connection() as conn:
        rows = conn.execute('SELECT * FROM backups ORDER BY created_at DESC').fetchall()
        return [dict(row) for row in rows]


def get_backup_by_id(backup_id):
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM backups WHERE id = ?', (backup_id,)).fetchone()
        return dict(row) if row else None


# Initialize DB on import
init_db()
