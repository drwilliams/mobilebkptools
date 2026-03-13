# iphonetools

A Python CLI for creating encrypted backups of iPhones and iPads, and extracting data from them.

## Requirements

- Python 3.10+
- Linux or macOS
- `usbmuxd` running (standard on most Linux desktops; built into macOS)
- iPhone or iPad connected via USB and trusted on this machine

## Installation

```bash
pip3 install --break-system-packages -r requirements.txt
```

Or inside a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## First-time device setup

1. Connect the device via USB.
2. On the device, tap **Trust** when prompted.
3. If you have never trusted this machine before, you may need to unlock the device first.

## Commands

### `backup` — Create an encrypted backup

```bash
python3 main.py backup
```

Connects to the device, generates a random 32-character hex encryption password, enables backup encryption on the device, and runs a full backup. The password is stored in `iphonetools.db` alongside device metadata. Backups are saved under `backups/<timestamp>/<UDID>/`.

**Options:**

| Flag | Description |
|---|---|
| `--incremental` | Run an incremental backup instead of a full one |

**Notes:**
- iOS keeps a single backup password per device. Once encryption is enabled, all subsequent backups use the same password until it is changed.
- If the device already has a backup password set (i.e. you previously ran `backup`), the tool will detect this and proceed using the existing password. The newly generated password stored in the DB will **not** match — you must manually update the `encryption_password` column in `iphonetools.db` to the correct value before extracting data.
- The actual backup files are standard iTunes-format encrypted backups and can be verified with any tool that supports them.

---

### `list-backups` — Show all stored backups

```bash
python3 main.py list-backups
```

Prints a table of all backups recorded in `iphonetools.db`, including the numeric ID used by other commands, device name, iOS version, UDID, creation timestamp, and backup path.

---

### `list-apps` — List installed apps

```bash
python3 main.py list-apps <BACKUP_ID>
```

Lists all apps installed on the device at the time of the backup. Reads from `Manifest.plist` inside the backup directory — no decryption required. Falls back to scanning `Manifest.db` domain names if the Applications key is absent.

**Example:**

```bash
python3 main.py list-apps 2
```

Output includes bundle ID, display name, and version for each app (1000+ entries is normal as Apple system services are included alongside user-installed apps).

---

### `extract-texts` — Extract SMS and iMessage messages

```bash
python3 main.py extract-texts <BACKUP_ID>
```

Decrypts the backup using the stored password and reads `Library/SMS/sms.db`. Displays a table of messages with date, contact (phone number or Apple ID), direction (sent `→` / received `←`), service (SMS or iMessage), and message text.

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--limit N` | 200 | Maximum number of messages to retrieve (most recent first) |
| `--output FILE` | — | Save results to a CSV file instead of printing |

**Examples:**

```bash
# Print the 200 most recent messages
python3 main.py extract-texts 2

# Print up to 1000 messages
python3 main.py extract-texts 2 --limit 1000

# Export all messages to a CSV file
python3 main.py extract-texts 2 --limit 99999 --output messages.csv
```

---

## Database

All backup metadata and encryption passwords are stored in `iphonetools.db` (SQLite) in the project directory.

**Schema:**

```sql
CREATE TABLE backups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_udid         TEXT NOT NULL,
    device_name         TEXT,
    product_type        TEXT,
    ios_version         TEXT,
    backup_path         TEXT NOT NULL,
    encryption_password TEXT NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

You can query it directly with any SQLite client:

```bash
sqlite3 iphonetools.db "SELECT id, device_name, ios_version, created_at FROM backups;"
```

## File layout

```
iphonetools/
├── main.py             # CLI entry point
├── db.py               # SQLite helper (auto-creates iphonetools.db on first run)
├── device_backup.py    # Backup creation via pymobiledevice3
├── extract_sms.py      # SMS/iMessage extraction via iphone-backup-decrypt
├── extract_apps.py     # App listing from Manifest.plist / Manifest.db
├── requirements.txt
├── iphonetools.db      # Created at runtime
└── backups/
    └── <YYYYMMDD_HHMMSS>/
        └── <UDID>/     # Standard iTunes-format backup files
```

## Dependencies

| Package | Purpose |
|---|---|
| `pymobiledevice3` | USB device communication and backup creation |
| `iphone-backup-decrypt` | Decrypting iOS encrypted backup files |
| `click` | CLI framework |
| `rich` | Terminal tables and formatting |
