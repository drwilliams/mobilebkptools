# iphonetools

A Python desktop app and CLI for creating encrypted backups of iPhones and iPads, and extracting data from them.

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

When starting a backup, unlock the device and enter its device passcode if prompted
on its screen. The tool waits up to five minutes for the device's backup sync lock
before timing out. This is separate from the backup encryption password.

## Desktop GUI

Run from a desktop session with the same Python environment used for the CLI:

```bash
python3 main.py gui
# Or launch directly:
python3 gui.py
```

The window lists saved backups and provides full or incremental backup creation,
app listings, message viewing, CSV export, and a masked password editor. Select a
backup before inspecting it or updating its stored password. The message limit
applies to both viewing and CSV export. Results and command errors appear in the
activity panel; backup and extraction commands run in the background so the
window remains responsive. Wait for an operation to finish before closing.

The connected-device list supports multiple selection (Ctrl/Shift-click).
Click **Refresh devices** after connecting devices, then **Back up selected devices**
to start an independent backup process for each selected device. Each job has its
own progress bar and result; a failed or disconnected device does not stop other
jobs. Devices already backing up are skipped when starting another selection.
Passwords remain available through **Saved passwords** during all jobs. USB and
disk bandwidth are shared between simultaneous backups.

Use **Scan for backups** to search the Finder backup folder on macOS or your home
folder on other systems. **Scan a folder…** lets you choose another folder or
mounted drive. Scans run in the background and open a results window. Select one
or more results and click **Import selected backups** to add them to the saved
list. Import registers files in place without copying them and skips backups
already registered. Keep imported backup folders accessible at their original
paths. For encrypted backups, use **Set password** with the existing backup
password before extracting messages. Message extraction currently supports
encrypted backups; unencrypted and older-format backups can be registered but
may not support all inspection tools.

The GUI uses Tkinter, which is supplied separately by some Python distributions:

```bash
# Fedora
sudo dnf install python3-tkinter
# Ubuntu / Debian
sudo apt install python3-tk
```

On macOS, use a Python installation with Tk support. Tkinter is a system Python
component, not a package installed by `pip install -r requirements.txt`.

The GUI uses the same local database and backup folders as the CLI. Stored backup
passwords are plaintext in SQLite; protect access to the project directory. The
GUI masks password input and hides generated passwords from its activity panel.
Use **Saved passwords**, then **Show passwords** or **Copy**, to retrieve a saved
device password, including during a running backup or after a failed attempt.
New passwords are committed to `iphonetools.db` before the tool attempts to enable
encryption on the device, and are reused on retries. If the connection fails while
enabling encryption, the saved value is the attempted password; the device may
or may not have accepted it. Completed backups are registered separately.
The progress bar animates during preparation and displays the device's reported
percentage during backup. Updates may pause while the device processes files;
completion is confirmed in the activity panel.

## Commands

### `backup` — Create an encrypted backup

```bash
python3 main.py backup
```

Connects to the device and, if encryption is off, generates a random 32-character
hex encryption password and saves it in `iphonetools.db` before enabling encryption.
It then runs a full backup. Completed backup metadata is saved after success.
Backups are saved under `backups/<timestamp>/<UDID>/`.

**Options:**

| Flag | Description |
|---|---|
| `--incremental` | Run an incremental backup instead of a full one |
| `--udid ID` | Select a specific connected USB device |

Run `python3 main.py list-devices` to list connected USB devices and identifiers
as JSON. Separate CLI processes can back up different `--udid` values concurrently;
each run creates a unique session directory.

**Notes:**
- iOS keeps a single backup password per device. Once encryption is enabled, all subsequent backups use the same password until it is changed.
- If the device already has a backup password set, the tool reuses the latest password stored for that device. If none is stored, use `set-password <BACKUP_ID>` after the backup to record the correct password before extracting data.
- The actual backup files are standard iTunes-format encrypted backups and can be verified with any tool that supports them.

---

### `list-backups` — Show all stored backups

```bash
python3 main.py list-backups
```

Prints a table of all backups recorded in `iphonetools.db`, including the numeric ID used by other commands, device name, iOS version, UDID, creation timestamp, and backup path.

---

### `scan-backups` — Find existing iPhone and iPad backups

```bash
# Search the standard Finder backup folder on macOS (home folder elsewhere)
python3 main.py scan-backups

# Search specific folders, other user folders, or mounted drives
python3 main.py scan-backups /Users /Volumes
```

With no paths supplied, macOS uses
`~/Library/Application Support/MobileSync/Backup`; other systems use your home
folder. Supplying paths overrides this default.

Recursively finds standard iTunes-format backup folders using their metadata and
manifest files. Displays device name, iOS version, backup date, encryption status,
and path. The scan is read-only and does not add backups to the local database.
It does not follow directory symlinks. Inaccessible folders and unreadable metadata
are skipped and counted; macOS may require Full Disk Access for your terminal to
search protected folders. Large folder trees can take time to scan. This finds
local backups, not backups stored only in iCloud.

---

### `import-backup` — Register an existing backup folder

```bash
python3 main.py import-backup "/path/to/MobileSync/Backup/device-backup-folder"
```

Supply the individual folder containing `Info.plist`, `Manifest.plist`, and
`Manifest.db` (or an older `Manifest.mbdb`). Import records device metadata and
the backup date in the local database without copying files. Importing the same
folder again returns its existing ID and preserves any stored password.
For encrypted backups, run `set-password <BACKUP_ID>` afterward.

---

### `set-password` — Update a stored backup password

```bash
python3 main.py set-password <BACKUP_ID>
```

Find the backup ID with `list-backups`. Enter the existing encryption password (for example, the one set in Finder) and enter it again to confirm. Input is hidden, and the password is stored only for the selected backup in `iphonetools.db`. This command does not change the device's encryption password or verify that the password decrypts the backup.

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
├── gui.py              # Tkinter desktop interface
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
