# mobilebkptools

A Go CLI for creating encrypted backups of iPhones and iPads, and extracting and managing data from them. Single self-contained binary — no runtime dependencies.

## Requirements

- Linux or macOS
- `libimobiledevice` tools installed (`idevicebackup2`, `idevice_id`, `ideviceinfo`)
- `usbmuxd` running (standard on most Linux desktops; built into macOS)
- iPhone or iPad connected via USB and trusted on this machine

**Installing libimobiledevice on Ubuntu/Debian:**

```bash
sudo apt install libimobiledevice-utils usbmuxd
```

## Building

```bash
go build -o mobilebkptools .
```

Requires Go 1.22+. The resulting binary has no external runtime dependencies.

## First-time device setup

1. Connect the device via USB.
2. On the device, tap **Trust** when prompted.
3. If you have never trusted this machine before, you may need to unlock the device first.

## Commands

### `backup` — Create an encrypted backup

```bash
./mobilebkptools backup
```

Connects to the device, generates a random 32-character hex encryption password, enables backup encryption on the device, and runs a full backup. The password is stored in `iphonetools.db` alongside device metadata. Backups are saved under `backups/<timestamp>/<UDID>/`.

**Notes:**
- iOS keeps a single backup password per device. Once encryption is enabled, all subsequent backups use the same password until it is changed.
- If the device already has a backup password set from a previous run, you must manually update the `encryption_password` column in `iphonetools.db` to the correct value before extracting data.
- The backup files are standard iTunes-format encrypted backups compatible with any tool that supports them.

---

### `list-backups` — Show all stored backups

```bash
./mobilebkptools list-backups
```

Prints a table of all backups recorded in `iphonetools.db`, including the numeric ID used by other commands, device name, iOS version, creation timestamp, and backup path.

---

### `list-apps` — List installed apps

```bash
./mobilebkptools list-apps <BACKUP_ID>
```

Lists all apps installed on the device at the time of the backup. Reads from `Manifest.plist` — no decryption required. Output includes bundle ID, display name, and version.

---

### `extract-texts` — Extract SMS and iMessage messages

```bash
./mobilebkptools extract-texts <BACKUP_ID>
```

Decrypts the backup using the stored password and reads `Library/SMS/sms.db`. Displays a table of messages with date, contact, direction (sent/received), service (SMS or iMessage), and text.

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--limit N` | 200 | Maximum number of messages to retrieve (most recent first) |
| `--output FILE` | — | Save results to a CSV file instead of printing |

**Examples:**

```bash
# Print the 200 most recent messages
./mobilebkptools extract-texts 2

# Export all messages to CSV
./mobilebkptools extract-texts 2 --limit 99999 --output messages.csv
```

---

### `show-layout` — Display the home screen icon layout

```bash
./mobilebkptools show-layout <BACKUP_ID>
```

Decrypts and prints the home screen layout from the backup, showing each page's apps, widgets, folders, and dock items.

---

### `export-layout` — Export the home screen layout to JSON

```bash
./mobilebkptools export-layout <BACKUP_ID> <output.json>
```

Exports the home screen layout to a JSON file. String items are app bundle IDs that can be freely moved. Object items (widgets, folders, smart stacks) are preserved verbatim and should generally be left in place.

---

### `import-layout` — Write a modified layout back into a backup

```bash
./mobilebkptools import-layout <BACKUP_ID> <input.json>
```

Reads a modified layout JSON file, re-encrypts it, and writes it back into the backup. Updates `Manifest.db` with the new file digest and size so the restore is valid.

After running this command, restore to the device with:

```bash
idevicebackup2 restore --system <backup-path>
```

The backup path is shown in `list-backups` output.

---

### `list-backup-apps` — List apps with data in a backup

```bash
./mobilebkptools list-backup-apps <BACKUP_ID>
```

Decrypts `Manifest.db` and lists every app that has files in the backup (i.e. `AppDomain-*` entries), along with display names and file counts. Useful for identifying what data is present before pruning.

---

### `prune-apps` — Remove selected apps from a backup

```bash
./mobilebkptools prune-apps <BACKUP_ID>
```

Presents an interactive checkbox list of all apps with backup data. All apps are checked (kept) by default — uncheck any you want to remove. After confirmation, their files are deleted from disk and their entries are removed from `Manifest.db`, which is then re-encrypted.

**Options:**

| Flag | Description |
|---|---|
| `--keep <bundle-id>` | Keep this bundle ID (repeatable). If any `--keep` flags are set, the interactive prompt is skipped. |

**Examples:**

```bash
# Interactive — uncheck apps to remove
./mobilebkptools prune-apps 2

# Non-interactive — keep only two apps
./mobilebkptools prune-apps 2 --keep com.apple.mobilephone --keep com.example.myapp
```

---

## Database

All backup metadata and encryption passwords are stored in `iphonetools.db` (SQLite) in the project directory.

**Schema:**

```sql
CREATE TABLE backups (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_udid         TEXT,
    device_name         TEXT,
    product_type        TEXT,
    ios_version         TEXT,
    backup_path         TEXT,
    encryption_password TEXT,
    created_at          TEXT
);
```

You can query it directly:

```bash
sqlite3 iphonetools.db "SELECT id, device_name, ios_version, created_at FROM backups;"
```

## File layout

```
mobilebkptools/
├── main.go             # CLI entry point (cobra commands)
├── db.go               # SQLite helper (auto-creates iphonetools.db on first run)
├── backup.go           # Backup creation via idevicebackup2 subprocess
├── decrypt.go          # iOS backup decryption (keybag, AES Key Unwrap, AES-CBC)
├── apps.go             # App listing from Manifest.plist
├── sms.go              # SMS/iMessage extraction
├── layout.go           # Home screen layout commands (show/export/import)
├── manage_apps.go      # App management commands (list-backup-apps, prune-apps)
├── go.mod / go.sum
├── iphonetools.db      # Created at runtime
└── backups/
    └── <YYYYMMDD_HHMMSS>/
        └── <UDID>/     # Standard iTunes-format backup files
```

## Dependencies

| Package | Purpose |
|---|---|
| `github.com/spf13/cobra` | CLI framework |
| `github.com/olekukonko/tablewriter` | Terminal tables |
| `github.com/AlecAivazis/survey/v2` | Interactive checkbox prompts |
| `howett.net/plist` | iOS plist parsing and serialization |
| `modernc.org/sqlite` | Pure-Go SQLite driver |
| `golang.org/x/crypto` | PBKDF2 key derivation |
