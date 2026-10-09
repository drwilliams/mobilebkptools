#!/usr/bin/env python3
"""Desktop interface for the existing iOS backup commands."""

import os
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

PROJECT_DIR = Path(__file__).resolve().parent


def run_command(args, events):
    """Stream CLI output without blocking the Tk event loop."""
    try:
        env = dict(os.environ, NO_COLOR='1', TERM='dumb', COLUMNS='160')
        with subprocess.Popen(
            [sys.executable, '-u', str(PROJECT_DIR / 'main.py'), *args],
            cwd=PROJECT_DIR, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors='replace',
        ) as process:
            for line in process.stdout:
                if args[0] == 'scan-backups' and '--json' in args:
                    try:
                        discovery = json.loads(line)
                    except ValueError:
                        pass
                    else:
                        if isinstance(discovery, dict) and 'path' in discovery:
                            events.put(('discovered', discovery))
                            continue
                # The CLI currently prints newly generated backup passwords.
                if 'Generated encryption password:' in line:
                    line = 'Encryption password generated; stored in the local database.\n'
                events.put(('output', line))
            events.put(('done', process.wait()))
    except Exception as exc:
        events.put(('output', f'Unable to run command: {exc}\n'))
        events.put(('done', 1))


def main():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from tkinter.scrolledtext import ScrolledText
    except ImportError:
        raise SystemExit(
            'The GUI requires Tkinter. On Fedora: sudo dnf install python3-tkinter\n'
            'On Ubuntu/Debian: sudo apt install python3-tk'
        )

    from db import get_all_backups, update_backup_password, import_backup

    class BackupApp:
        def __init__(self, root):
            self.root = root
            self.events = queue.Queue()
            self.busy = False
            self.buttons = []
            self.discoveries = {}
            self.scan_window = None
            root.title('iOS Backup Tools')
            root.geometry('1120x760')
            root.minsize(850, 600)
            root.protocol('WM_DELETE_WINDOW', self.close)
            style = ttk.Style(root)
            style.configure('Treeview', rowheight=28)
            style.configure('Title.TLabel', font=('TkDefaultFont', 20, 'bold'))

            frame = ttk.Frame(root, padding=20)
            frame.pack(fill='both', expand=True)
            ttk.Label(frame, text='iOS Backup Tools', style='Title.TLabel').pack(anchor='w')
            ttk.Label(frame, text='Create encrypted backups and inspect your saved device data.').pack(anchor='w', pady=(4, 16))
            toolbar = ttk.Frame(frame)
            toolbar.pack(fill='x')
            self.button(toolbar, 'Create backup', self.backup).pack(side='left')
            self.incremental = tk.BooleanVar(value=False)
            self.incremental_check = ttk.Checkbutton(toolbar, text='Incremental', variable=self.incremental)
            self.incremental_check.pack(side='left', padx=12)
            self.button(toolbar, 'Refresh backups', self.refresh).pack(side='right')
            ttk.Label(frame, text='Connect your iPhone or iPad via USB, unlock it, and trust this computer.').pack(anchor='w', pady=(8, 12))

            scan_actions = ttk.Frame(frame)
            scan_actions.pack(fill='x', pady=(0, 12))
            self.button(scan_actions, 'Scan for backups', self.scan).pack(side='left')
            self.button(scan_actions, 'Scan a folder…', self.scan_folder).pack(side='left', padx=8)
            scan_location = 'Finder backup folder' if sys.platform == 'darwin' else 'home folder'
            ttk.Label(scan_actions, text=f'Default: {scan_location}. Select discovered backups to import.').pack(side='left')

            columns = ('id', 'device', 'ios', 'created', 'path')
            table_frame = ttk.Frame(frame)
            table_frame.pack(fill='both', expand=True)
            self.table = ttk.Treeview(table_frame, columns=columns, show='headings', selectmode='browse', height=8)
            for key, title, width in zip(columns, ('ID', 'Device', 'iOS', 'Created', 'Backup path'), (45, 180, 65, 160, 400)):
                self.table.heading(key, text=title)
                self.table.column(key, width=width, minwidth=40, stretch=key in ('device', 'path'))
            scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
            self.table.configure(yscrollcommand=scrollbar.set)
            self.table.pack(side='left', fill='both', expand=True)
            scrollbar.pack(side='right', fill='y')

            actions = ttk.Frame(frame)
            actions.pack(fill='x', pady=12)
            self.button(actions, 'List apps', lambda: self.selected_command('list-apps')).pack(side='left')
            self.button(actions, 'View messages', lambda: self.messages(False)).pack(side='left', padx=8)
            self.button(actions, 'Export messages to CSV', lambda: self.messages(True)).pack(side='left')
            ttk.Label(actions, text='Message limit:').pack(side='left', padx=(16, 4))
            self.limit = tk.StringVar(value='200')
            ttk.Spinbox(actions, from_=1, to=999999, textvariable=self.limit, width=8).pack(side='left')
            self.button(actions, 'Set password', self.password).pack(side='right')
            ttk.Label(frame, text='Activity and results').pack(anchor='w', pady=(0, 6))
            self.log = ScrolledText(frame, height=15, wrap='none', font=('TkFixedFont', 10), state='disabled')
            self.log.pack(fill='both', expand=True)
            self.status = tk.StringVar(value='Ready')
            ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(8, 0))
            self.refresh()
            root.after(100, self.poll)

        def button(self, parent, text, command):
            widget = ttk.Button(parent, text=text, command=command)
            self.buttons.append(widget)
            return widget

        def refresh(self):
            selected = self.table.selection()
            try:
                backups = get_all_backups()
            except Exception as exc:
                messagebox.showerror('Cannot load backups', str(exc), parent=self.root)
                return
            self.table.delete(*self.table.get_children())
            for backup in backups:
                self.table.insert('', 'end', iid=str(backup['id']), values=(
                    backup['id'], backup['device_name'] or 'Unknown',
                    backup['ios_version'] or '?', backup['created_at'], backup['backup_path']))
            if selected and self.table.exists(selected[0]):
                self.table.selection_set(selected[0])
            elif backups:
                self.table.selection_set(str(backups[0]['id']))
            if not self.busy:
                self.status.set(f'{len(backups)} saved backups' if backups else 'No backups yet. Connect a device and create a backup.')

        def selected_id(self):
            selection = self.table.selection()
            if not selection:
                messagebox.showinfo('Select a backup', 'Select a saved backup first.', parent=self.root)
                return None
            return selection[0]

        def start(self, args):
            if self.busy:
                return
            self.busy = True
            for button in self.buttons:
                button.configure(state='disabled')
            self.incremental_check.configure(state='disabled')
            self.status.set(f'Running {args[0]}…')
            self.append('\n' + ' '.join(args) + '\n' + '─' * 60 + '\n')
            threading.Thread(target=run_command, args=(args, self.events), daemon=True).start()

        def backup(self):
            self.start(['backup'] + (['--incremental'] if self.incremental.get() else []))

        def scan_folder(self):
            if self.busy:
                return
            path = filedialog.askdirectory(parent=self.root, title='Choose a folder to scan for backups', mustexist=True)
            if path:
                self.scan([path])

        def scan(self, paths=None):
            if self.busy:
                return
            if self.scan_window is None:
                self.scan_window = tk.Toplevel(self.root)
                self.scan_window.title('Discovered backups')
                self.scan_window.geometry('1000x420')
                self.scan_window.protocol('WM_DELETE_WINDOW', self.scan_window.withdraw)
                content = ttk.Frame(self.scan_window, padding=12)
                content.pack(fill='both', expand=True)
                ttk.Label(content, text='Select backups and import them into your saved list. Files stay in their current location.').pack(anchor='w')
                columns = ('device', 'ios', 'date', 'encrypted', 'path')
                self.found_table = ttk.Treeview(content, columns=columns, show='headings', selectmode='extended')
                for key, title in zip(columns, ('Device', 'iOS', 'Backup date', 'Encrypted', 'Path')):
                    self.found_table.heading(key, text=title)
                    self.found_table.column(key, width=250 if key == 'path' else 140)
                self.found_table.pack(fill='both', expand=True, pady=10)
                self.button(content, 'Import selected backups', self.import_selected).pack(anchor='e')
                ttk.Label(content, text='For encrypted backups, select the saved backup and use Set password before viewing messages.').pack(anchor='w', pady=(8, 0))
            self.found_table.delete(*self.found_table.get_children())
            self.discoveries.clear()
            self.scan_window.deiconify()
            self.scan_window.lift()
            self.start(['scan-backups', '--json', *(paths or [])])

        def import_selected(self):
            if self.busy:
                return
            selected = self.found_table.selection()
            if not selected:
                messagebox.showinfo('Select backups', 'Select one or more discovered backups to import.', parent=self.scan_window)
                return
            imported = existing = 0
            for item in selected:
                path = self.discoveries[item]['path']
                try:
                    backup_id, created = import_backup(path)
                except Exception as exc:
                    self.append(f'Could not import {path}: {exc}\n')
                    continue
                imported += int(created)
                existing += int(not created)
                self.append(f'{"Imported" if created else "Already saved"}: backup {backup_id}, {path}\n')
            self.refresh()
            self.status.set(f'Imported {imported} backups; {existing} already saved. See activity for details.')

        def selected_command(self, command):
            backup_id = self.selected_id()
            if backup_id:
                self.start([command, backup_id])

        def messages(self, export):
            backup_id = self.selected_id()
            if not backup_id:
                return
            try:
                limit = int(self.limit.get())
                if limit < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror('Invalid limit', 'Enter a positive whole number for the message limit.', parent=self.root)
                return
            args = ['extract-texts', backup_id, '--limit', str(limit)]
            if export:
                path = filedialog.asksaveasfilename(parent=self.root, title='Export messages', defaultextension='.csv', filetypes=[('CSV files', '*.csv')], initialfile=f'messages-{backup_id}.csv')
                if not path:
                    return
                args += ['--output', path]
            self.start(args)

        def password(self):
            backup_id = self.selected_id()
            if not backup_id:
                return
            dialog = tk.Toplevel(self.root)
            dialog.title(f'Password for backup {backup_id}')
            dialog.transient(self.root)
            dialog.resizable(False, False)
            content = ttk.Frame(dialog, padding=20)
            content.pack()
            ttk.Label(content, text='Save the existing encryption password for this backup.\nThis does not change the device password or verify it.').pack(anchor='w', pady=(0, 12))
            entries = []
            for label in ('Password', 'Confirm password'):
                ttk.Label(content, text=label).pack(anchor='w')
                entry = ttk.Entry(content, show='•', width=42)
                entry.pack(pady=(3, 10))
                entries.append(entry)

            def save():
                value = entries[0].get()
                if not value or value != entries[1].get():
                    messagebox.showerror('Password mismatch', 'Enter a nonempty password and matching confirmation.', parent=dialog)
                    return
                try:
                    if not update_backup_password(int(backup_id), value):
                        raise ValueError('This backup no longer exists.')
                except Exception as exc:
                    messagebox.showerror('Cannot save password', str(exc), parent=dialog)
                    return
                dialog.destroy()
                self.status.set(f'Stored password updated for backup {backup_id}.')

            ttk.Button(content, text='Save password', command=save).pack(side='right')
            ttk.Button(content, text='Cancel', command=dialog.destroy).pack(side='right', padx=8)
            dialog.bind('<Return>', lambda event: save())
            dialog.bind('<Escape>', lambda event: dialog.destroy())
            dialog.grab_set()
            entries[0].focus_set()

        def append(self, text):
            self.log.configure(state='normal')
            self.log.insert('end', text)
            self.log.see('end')
            self.log.configure(state='disabled')

        def poll(self):
            # Bound each pass so large command outputs do not starve the UI.
            for _ in range(100):
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'output':
                    self.append(value)
                elif kind == 'discovered':
                    item = self.found_table.insert('', 'end', values=tuple(value[key] for key in ('device', 'ios', 'date', 'encrypted', 'path')))
                    self.discoveries[item] = value
                    self.append(f'Found backup: {value["device"]} — {value["path"]}\n')
                else:
                    self.busy = False
                    for button in self.buttons:
                        button.configure(state='normal')
                    self.incremental_check.configure(state='normal')
                    self.refresh()
                    self.status.set('Command finished — see activity for results.' if value == 0 else 'Command failed — see activity for details.')
            self.root.after(100, self.poll)

        def close(self):
            if self.busy:
                messagebox.showinfo('Operation in progress', 'Wait for the current operation to finish before closing the window.', parent=self.root)
                return
            self.root.destroy()

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise SystemExit(f'Cannot open the GUI: {exc}. Run this from a desktop session.')
    BackupApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
