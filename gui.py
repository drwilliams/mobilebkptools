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


class JobEvents:
    """Attach a device identifier to every event from its subprocess."""
    def __init__(self, events, udid):
        self.events, self.udid = events, udid

    def put(self, event):
        self.events.put(('job', (self.udid, *event)))


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
                if args[0] == 'list-devices':
                    try:
                        devices = json.loads(line)
                        if isinstance(devices, list):
                            events.put(('devices', devices))
                            continue
                    except ValueError:
                        pass
                if args[0] == 'backup' and line.startswith('BACKUP_PROGRESS '):
                    try:
                        events.put(('progress', max(0, min(100, float(line.split(' ', 1)[1])))))
                        continue
                    except ValueError:
                        pass
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

    from db import get_all_backups, update_backup_password, import_backup, get_saved_device_passwords

    class BackupApp:
        def __init__(self, root):
            self.root = root
            self.events = queue.Queue()
            self.busy = False
            self.buttons = []
            self.discoveries = {}
            self.scan_window = None
            self.jobs = {}
            self.devices = {}
            self.discovering = False
            root.title('iOS Backup Tools')
            root.geometry('1120x920')
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
            ttk.Button(toolbar, text='Back up selected devices', command=self.backup).pack(side='left')
            self.incremental = tk.BooleanVar(value=False)
            self.incremental_check = ttk.Checkbutton(toolbar, text='Incremental', variable=self.incremental)
            self.incremental_check.pack(side='left', padx=12)
            self.button(toolbar, 'Refresh backups', self.refresh).pack(side='right')
            ttk.Button(toolbar, text='Saved passwords', command=self.saved_passwords).pack(side='right', padx=8)
            ttk.Label(frame, text='Connect your iPhone or iPad via USB, unlock it, and trust this computer.').pack(anchor='w', pady=(8, 12))

            device_header = ttk.Frame(frame)
            device_header.pack(fill='x')
            ttk.Label(device_header, text='Connected devices — Ctrl/Shift-click to select several').pack(side='left')
            self.device_refresh = ttk.Button(device_header, text='Refresh devices', command=self.refresh_devices)
            self.device_refresh.pack(side='right')
            self.device_table = ttk.Treeview(frame, columns=('name', 'ios', 'udid', 'status'), show='headings', selectmode='extended', height=3)
            for key, title in zip(('name', 'ios', 'udid', 'status'), ('Device', 'iOS', 'UDID', 'Status')):
                self.device_table.heading(key, text=title)
                self.device_table.column(key, width=80 if key == 'ios' else 260)
            self.device_table.pack(fill='x', pady=(6, 8))
            self.job_frame = ttk.Frame(frame)
            self.job_frame.pack(fill='x', pady=(0, 8))

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
            self.progress = ttk.Progressbar(frame, mode='indeterminate', maximum=100)
            self.progress.pack(fill='x', pady=(8, 0))
            ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(8, 0))
            self.refresh()
            self.refresh_devices()
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
            self.progress.configure(mode='indeterminate', value=0)
            self.progress.start(15)
            self.append('\n' + ' '.join(args) + '\n' + '─' * 60 + '\n')
            threading.Thread(target=run_command, args=(args, self.events), daemon=True).start()

        def backup(self):
            selected = self.device_table.selection()
            if not selected:
                messagebox.showinfo('Select devices', 'Select one or more connected devices first.', parent=self.root)
                return
            for udid in selected:
                if udid in self.jobs and self.jobs[udid]['running']:
                    continue
                if udid in self.jobs:
                    self.jobs[udid]['frame'].destroy()
                name = self.devices[udid]['name']
                frame = ttk.Frame(self.job_frame)
                frame.pack(fill='x', pady=3)
                ttk.Label(frame, text=f'{name} ({udid[-8:]})', width=32).pack(side='left')
                bar = ttk.Progressbar(frame, mode='indeterminate', maximum=100)
                bar.pack(side='left', fill='x', expand=True, padx=8)
                status = tk.StringVar(value='Preparing…')
                ttk.Label(frame, textvariable=status, width=28).pack(side='right')
                self.jobs[udid] = {'frame': frame, 'bar': bar, 'status': status, 'running': True, 'name': name}
                bar.start(15)
                self.device_table.set(udid, 'status', 'Backing up')
                args = ['backup', '--udid', udid] + (['--incremental'] if self.incremental.get() else [])
                self.append(f'\n[{name} / {udid}] Starting backup\n')
                threading.Thread(target=run_command, args=(args, JobEvents(self.events, udid)), daemon=True).start()

        def refresh_devices(self):
            if self.discovering:
                return
            self.discovering = True
            self.device_refresh.configure(state='disabled')
            # Discovery uses its own queue wrapper, independently of other commands.
            threading.Thread(target=run_command, args=(['list-devices'], JobEvents(self.events, 'discovery')), daemon=True).start()

        def job_event(self, udid, kind, value):
            if udid == 'discovery':
                if kind == 'devices':
                    selected = self.device_table.selection()
                    self.device_table.delete(*self.device_table.get_children())
                    self.devices = {row['udid']: row for row in value}
                    for row in value:
                        job = self.jobs.get(row['udid'])
                        status = 'Backing up' if job and job['running'] else row['status']
                        self.device_table.insert('', 'end', iid=row['udid'], values=(row['name'], row['ios'], row['udid'], status))
                    kept = [udid for udid in selected if udid in self.devices]
                    self.device_table.selection_set(kept or list(self.devices))
                elif kind == 'output':
                    self.append(value)
                elif kind == 'done':
                    self.discovering = False
                    self.device_refresh.configure(state='normal')
                return
            job = self.jobs[udid]
            if kind == 'output':
                self.append(f'[{job["name"]} / {udid[-8:]}] {value}')
            elif kind == 'progress':
                job['bar'].stop()
                job['bar'].configure(mode='determinate', value=value)
                job['status'].set(f'Backing up… {value:.0f}%')
            elif kind == 'done':
                job['running'] = False
                job['bar'].stop()
                if value == 0:
                    job['bar'].configure(mode='determinate', value=100)
                result = 'Complete' if value == 0 else 'Failed — see activity'
                job['status'].set(result)
                if self.device_table.exists(udid):
                    self.device_table.set(udid, 'status', result)
                self.refresh()

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

        def saved_passwords(self):
            dialog = tk.Toplevel(self.root)
            dialog.title('Saved backup passwords')
            content = ttk.Frame(dialog, padding=20)
            content.pack(fill='both', expand=True)
            ttk.Label(content, text='New passwords are saved before encryption is enabled, even if the backup fails.').pack(anchor='w')
            rows = get_saved_device_passwords()
            known = {row['device_udid'] for row in rows}
            for backup in get_all_backups():
                if backup['encryption_password'] and backup['device_udid'] not in known:
                    rows.append(backup)
                    known.add(backup['device_udid'])
            if not rows:
                ttk.Label(content, text='No passwords saved yet.').pack(pady=12)
            entries = []
            for row in rows:
                ttk.Label(content, text=f"{row['device_name'] or 'Unknown device'} — {row['device_udid']}").pack(anchor='w', pady=(12, 3))
                line = ttk.Frame(content)
                line.pack(fill='x')
                value = tk.StringVar(value=row['encryption_password'])
                entry = ttk.Entry(line, textvariable=value, show='•', width=42, state='readonly')
                entry.pack(side='left', fill='x', expand=True)
                entries.append(entry)
                def copy(password=row['encryption_password']):
                    self.root.clipboard_clear()
                    self.root.clipboard_append(password)
                ttk.Button(line, text='Copy', command=copy).pack(side='left', padx=8)
            visible = tk.BooleanVar(value=False)
            def reveal():
                for entry in entries:
                    entry.configure(show='' if visible.get() else '•')
            ttk.Checkbutton(content, text='Show passwords', variable=visible, command=reveal).pack(anchor='w', pady=12)
            ttk.Button(content, text='Close', command=dialog.destroy).pack(anchor='e')

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
                if kind == 'job':
                    self.job_event(*value)
                elif kind == 'output':
                    self.append(value)
                elif kind == 'discovered':
                    item = self.found_table.insert('', 'end', values=tuple(value[key] for key in ('device', 'ios', 'date', 'encrypted', 'path')))
                    self.discoveries[item] = value
                    self.append(f'Found backup: {value["device"]} — {value["path"]}\n')
                elif kind == 'progress':
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=value)
                    self.status.set(f'Backing up… {value:.0f}%')
                else:
                    self.progress.stop()
                    self.busy = False
                    for button in self.buttons:
                        button.configure(state='normal')
                    self.incremental_check.configure(state='normal')
                    self.refresh()
                    self.status.set('Command finished — see activity for results.' if value == 0 else 'Command failed — see activity for details.')
            self.root.after(100, self.poll)

        def close(self):
            if self.busy or self.discovering or any(job['running'] for job in self.jobs.values()):
                messagebox.showinfo('Operation in progress', 'Wait for the current operation to finish before closing the window.', parent=self.root)
                return
            self.root.destroy()

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise SystemExit(f'Cannot open the GUI: {exc}. Run this from a desktop session.')
    app = BackupApp(root)
    root.mainloop()
    return app


if __name__ == '__main__':
    main()
