import queue
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from gui import run_command


class CommandRunnerTests(unittest.TestCase):
    def test_scan_routes_discoveries_and_preserves_diagnostics(self):
        events = queue.Queue()
        process = MagicMock()
        process.stdout = iter(['{"path": "/backup", "device": "iPad"}\n',
                               'Skipped 1 inaccessible folders.\n'])
        process.wait.return_value = 0
        process.__enter__.return_value = process
        with patch('gui.subprocess.Popen', return_value=process):
            run_command(['scan-backups', '--json'], events)
        self.assertEqual(events.get_nowait(), ('discovered', {'path': '/backup', 'device': 'iPad'}))
        self.assertEqual(events.get_nowait()[0], 'output')
        self.assertEqual(events.get_nowait(), ('done', 0))

    def test_streams_output_redacts_password_and_reports_exit(self):
        events = queue.Queue()
        process = MagicMock()
        process.stdout = iter([
            'Connecting to device\n',
            'Generated encryption password: secret-value\n',
            'Backup failed\n',
        ])
        process.wait.return_value = 1
        process.__enter__.return_value = process
        with patch('gui.subprocess.Popen', return_value=process) as popen:
            run_command(['backup'], events)
        output = [events.get_nowait() for _ in range(4)]
        self.assertEqual(output[0], ('output', 'Connecting to device\n'))
        self.assertNotIn('secret-value', str(output))
        self.assertEqual(output[-1], ('done', 1))
        self.assertEqual(popen.call_args.args[0][-1], 'backup')
        self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.STDOUT)

    def test_launch_failure_releases_busy_state(self):
        events = queue.Queue()
        with patch('gui.subprocess.Popen', side_effect=OSError('Cannot launch')):
            run_command(['list-backups'], events)
        self.assertIn('Cannot launch', events.get_nowait()[1])
        self.assertEqual(events.get_nowait(), ('done', 1))

    def test_real_cli_list_backups(self):
        events = queue.Queue()
        run_command(['list-backups'], events)
        output = []
        while not events.empty():
            output.append(events.get_nowait())
        self.assertEqual(output[-1], ('done', 0))
        self.assertTrue(any(kind == 'output' for kind, _ in output))


if __name__ == '__main__':
    unittest.main()
