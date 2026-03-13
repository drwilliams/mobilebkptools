import sqlite3
import tempfile
import csv
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table

console = Console()

# Apple's epoch starts Jan 1 2001 — offset in seconds from Unix epoch
APPLE_EPOCH_OFFSET = 978307200


def apple_ts_to_str(ts) -> str:
    """Convert Apple nanosecond timestamp to human-readable string."""
    if ts is None:
        return ''
    try:
        unix_ts = ts / 1_000_000_000 + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix_ts).strftime('%Y-%m-%d %H:%M:%S')
    except (OSError, OverflowError, ValueError):
        return ''


def extract_messages(backup_path: str, password: str, limit: int = 100, output_file: str = None):
    """Decrypt the backup and extract SMS/iMessage messages."""
    try:
        from iphone_backup_decrypt import EncryptedBackup
    except ImportError:
        console.print("[red]iphone-backup-decrypt not installed. Run: pip install iphone-backup-decrypt[/red]")
        return

    console.print(f"[cyan]Opening encrypted backup:[/cyan] {backup_path}")

    try:
        backup = EncryptedBackup(backup_directory=backup_path, passphrase=password)
    except Exception as e:
        console.print(f"[red]Failed to open backup: {e}[/red]")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        sms_db_path = Path(tmpdir) / 'sms.db'

        console.print("[cyan]Extracting SMS database from backup...[/cyan]")
        try:
            backup.extract_file(
                relative_path="Library/SMS/sms.db",
                output_filename=str(sms_db_path),
            )
        except TypeError:
            # Older API: positional args
            try:
                backup.extract_file("Library/SMS/sms.db", output_filename=str(sms_db_path))
            except Exception as e:
                console.print(f"[red]Failed to extract SMS database: {e}[/red]")
                return
        except Exception as e:
            console.print(f"[red]Failed to extract SMS database: {e}[/red]")
            return

        if not sms_db_path.exists():
            console.print("[red]sms.db was not found in this backup.[/red]")
            return

        conn = sqlite3.connect(str(sms_db_path))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            '''SELECT
                   m.ROWID,
                   m.date,
                   m.text,
                   m.is_from_me,
                   m.service,
                   h.id AS contact
               FROM message m
               LEFT JOIN handle h ON m.handle_id = h.ROWID
               ORDER BY m.date DESC
               LIMIT ?''',
            (limit,)
        ).fetchall()
        conn.close()

    if not rows:
        console.print("[yellow]No messages found in this backup.[/yellow]")
        return

    if output_file:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Contact', 'Direction', 'Service', 'Message'])
            for row in rows:
                writer.writerow([
                    apple_ts_to_str(row['date']),
                    row['contact'] or 'Unknown',
                    'Sent' if row['is_from_me'] else 'Received',
                    row['service'] or '',
                    row['text'] or '',
                ])
        console.print(f"[green]Saved {len(rows)} messages to {output_file}[/green]")
    else:
        table = Table(title=f"Messages — {len(rows)} results", show_lines=True)
        table.add_column("Date", style="dim", width=20)
        table.add_column("Contact", style="cyan", width=22)
        table.add_column("Dir", width=4)
        table.add_column("Via", width=7)
        table.add_column("Message")

        for row in rows:
            table.add_row(
                apple_ts_to_str(row['date']),
                row['contact'] or 'Unknown',
                '→' if row['is_from_me'] else '←',
                row['service'] or '',
                (row['text'] or '')[:100],
            )

        console.print(table)
