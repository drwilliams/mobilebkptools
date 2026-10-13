import asyncio
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import gui
from device_backup import list_connected_devices


class DiscoveryTests(unittest.TestCase):
    def test_filters_network_and_duplicates_and_keeps_untrusted(self):
        devices = [SimpleNamespace(serial='a', connection_type='USB'),
                   SimpleNamespace(serial='a', connection_type='USB'),
                   SimpleNamespace(serial='b', connection_type='USB'),
                   SimpleNamespace(serial='c', connection_type='Network')]
        lockdown = AsyncMock()
        lockdown.get_value.side_effect = ['Test iPhone', '18']
        with patch('pymobiledevice3.usbmux.list_devices', new=AsyncMock(return_value=devices)), patch('pymobiledevice3.lockdown.create_using_usbmux', new=AsyncMock(side_effect=[lockdown, RuntimeError('Not trusted')])) as connect:
            rows = asyncio.run(list_connected_devices())
        self.assertEqual([r['udid'] for r in rows], ['a', 'b'])
        self.assertEqual(rows[0]['name'], 'Test iPhone')
        self.assertIn('trust', rows[1]['status'])
        self.assertFalse(connect.call_args.kwargs['autopair'])
        lockdown.close.assert_awaited_once()

    def test_job_events_keep_device_identity(self):
        events = queue.Queue()
        gui.JobEvents(events, 'a').put(('progress', 25))
        gui.JobEvents(events, 'b').put(('done', 1))
        self.assertEqual(events.get(), ('job', ('a', 'progress', 25)))
        self.assertEqual(events.get(), ('job', ('b', 'done', 1)))


class MultiDeviceGuiTests(unittest.TestCase):
    def test_independent_jobs_duplicate_guard_and_failure_isolation(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Desktop display unavailable')
        try:
            with patch('tkinter.Tk', return_value=root), patch.object(root, 'mainloop'), patch('gui.threading.Thread') as thread:
                app = gui.main()
                app.job_event('discovery', 'devices', [
                    {'udid': udid, 'name': f'Device {udid}', 'ios': '18', 'status': 'Connected'}
                    for udid in ('a', 'b')])
                app.job_event('discovery', 'done', 0)
                app.backup()
                launches = thread.call_args_list[1:]
                self.assertEqual(len(launches), 2)
                self.assertEqual([call.kwargs['args'][0] for call in launches],
                                 [['backup', '--udid', 'a'], ['backup', '--udid', 'b']])
                app.backup()
                self.assertEqual(thread.call_count, 3)
                app.job_event('a', 'progress', 25)
                app.job_event('b', 'progress', 60)
                self.assertEqual(app.jobs['a']['bar']['value'], 25)
                self.assertEqual(app.jobs['b']['bar']['value'], 60)
                app.job_event('a', 'done', 1)
                self.assertTrue(app.jobs['b']['running'])
                self.assertIn('Failed', app.jobs['a']['status'].get())
                app.job_event('b', 'done', 0)
                self.assertEqual(app.jobs['b']['status'].get(), 'Complete')
                self.assertEqual(app.jobs['b']['bar']['value'], 100)
        finally:
            root.destroy()
