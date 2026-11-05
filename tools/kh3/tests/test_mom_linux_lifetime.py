"""Standalone tests: python3 -m unittest discover -s tools/kh3/tests -p test_mom_linux_lifetime.py."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import tempfile
import types
import importlib
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3] / 'worlds/kh3/client_support/mom_linux.py'
spec = importlib.util.spec_from_file_location('mom_linux', SOURCE)
mom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mom)


@unittest.skipUnless(sys.platform == 'linux', 'Native Linux required')
class LinuxLifetimeTests(unittest.TestCase):
    def setUp(self):
        # Reproduce the bundled Archipelago Python that reported this failure.
        if hasattr(os, 'pidfd_open'):
            original_pidfd = os.pidfd_open
            del os.pidfd_open
            self.addCleanup(setattr, os, 'pidfd_open', original_pidfd)

    def test_configuration_without_python_pidfd_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'KINGDOM HEARTS Melody of Memory.exe').touch()
            command = [sys.executable, '-c', 'pass']
            with patch.dict(os.environ, {
                    'KH3AP_MOM_DIR': directory,
                    'KH3AP_MOM_COMMAND': json.dumps(command),
                    'KH3AP_MOM_ACTIVITY_PORT': '38419'}, clear=True):
                self.assertEqual(mom.configuration(), (root.resolve(), command, 38419))

    def test_automatic_setup_checks_greeting_before_launch(self):
        package = types.ModuleType('kh3_mom_test')
        package.__path__ = [str(SOURCE.parent)]
        with patch.dict(sys.modules, {'kh3_mom_test': package}), patch.object(mom, '__package__', 'kh3_mom_test'):
            setup = importlib.import_module('kh3_mom_test.mom_linux_setup')
            for matching in (False, True):
                with self.subTest(matching=matching):
                    listener = socket.socket()
                    listener.bind(('127.0.0.1', 0))
                    listener.listen()
                    listener.settimeout(5)
                    token = 'a' * 32
                    def serve():
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(5)
                            connection.sendall(('KH3AP_MOM_V1 ' + (token if matching else 'b' * 32) + '\n').encode())
                            connection.recv(1)
                    server = threading.Thread(target=serve)
                    server.start()
                    command = [sys.executable, '-c', 'pass']
                    try:
                        with patch.dict(os.environ, {}, clear=True), \
                                patch.object(mom, 'configuration', return_value=(Path('/tmp'), command, listener.getsockname()[1])), \
                                patch.object(setup, 'read_json', return_value={'token': token}), \
                                patch.object(setup, 'resolve_launch', return_value=(command, dict(os.environ), Path('/tmp'))) as resolve:
                            if matching:
                                mom.launch()
                                resolve.assert_called_once()
                            else:
                                with self.assertRaisesRegex(RuntimeError, 'did not match'):
                                    mom.launch()
                                resolve.assert_not_called()
                    finally:
                        server.join(5)
                        listener.close()
                        for thread in threading.enumerate():
                            if thread.name == 'KH3MoMLinuxLifetime':
                                thread.join(5)
                        self.assertIsNone(mom._active)

    def test_launch_connects_and_child_exit_closes_lease(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(5)
        command = [sys.executable, '-c', 'pass']
        with patch.object(mom, 'configuration', return_value=(Path('/tmp'), command, listener.getsockname()[1])), \
                patch.dict(os.environ, {'KH3AP_MOM_COMMAND': 'fixture'}):
            mom.launch()
        connection, _ = listener.accept()
        connection.settimeout(5)
        self.assertEqual(connection.recv(1), b'')
        for thread in threading.enumerate():
            if thread.name == 'KH3MoMLinuxLifetime':
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertIsNone(mom._active)
        connection.close()
        listener.close()

    def test_exit_and_crash_release_wait(self):
        for killed in (False, True):
            with self.subTest(killed=killed):
                process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                           stdin=subprocess.PIPE)
                exit_signal, exit_sender = socket.socketpair()
                reaper = threading.Thread(target=lambda: (process.wait(), exit_sender.close()))
                reaper.start()
                client, server = socket.socketpair()
                done = threading.Event()
                thread = threading.Thread(target=lambda: (mom.wait_for_exit(exit_signal, client), done.set()))
                thread.start()
                try:
                    self.assertFalse(done.is_set())
                    if killed:
                        process.kill()
                    else:
                        process.stdin.close()
                    self.assertTrue(done.wait(5))
                finally:
                    process.kill() if process.poll() is None else None
                    process.wait()
                    thread.join(5)
                    process.stdin.close()
                    reaper.join(5)
                    self.assertFalse(reaper.is_alive())
                    exit_signal.close()
                    client.close()
                    server.close()

    def test_bridge_loss_retains_process_ownership_until_exit(self):
        process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        exit_signal, exit_sender = socket.socketpair()
        reaper = threading.Thread(target=lambda: (process.wait(), exit_sender.close()))
        reaper.start()
        client, server = socket.socketpair()
        done = threading.Event()
        thread = threading.Thread(target=lambda: (mom.wait_for_exit(exit_signal, client), done.set()))
        thread.start()
        server.close()
        try:
            self.assertFalse(done.wait(.1))
            process.stdin.close()
            self.assertTrue(done.wait(5))
        finally:
            process.kill() if process.poll() is None else None
            process.wait()
            thread.join(5)
            client.close()
            reaper.join(5)
            self.assertFalse(reaper.is_alive())
            process.stdin.close()
            exit_signal.close()

    def test_launch_bridge_loss_blocks_duplicate_until_child_crashes(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(5)
            command = [sys.executable, '-c', 'import time; time.sleep(60)']
            bridge_lost = threading.Event()
            with patch.object(mom, 'configuration', return_value=(Path('/tmp'), command, listener.getsockname()[1])), \
                    patch.dict(os.environ, {'KH3AP_MOM_COMMAND': 'fixture'}), \
                    patch.object(mom.logging.getLogger('KH3Client'), 'warning', side_effect=lambda *args: bridge_lost.set()):
                mom.launch()
                process = mom._active
                try:
                    connection, _ = listener.accept()
                    connection.close()
                    self.assertTrue(bridge_lost.wait(5))
                    self.assertIs(mom._active, process)
                    with self.assertRaisesRegex(RuntimeError, 'Close'):
                        mom.launch()
                finally:
                    process.kill()
                    process.wait()
                    for thread in threading.enumerate():
                        if thread.name in ('KH3MoMLinuxLifetime', 'KH3MoMLinuxReaper'):
                            thread.join(5)
                            self.assertFalse(thread.is_alive())
                self.assertIsNone(mom._active)

    def test_process_start_failure_closes_lease(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(5)
            with patch.object(mom, 'configuration', return_value=(Path('/tmp'), ['unused'], listener.getsockname()[1])), \
                    patch.dict(os.environ, {'KH3AP_MOM_COMMAND': 'fixture'}), \
                    patch.object(subprocess, 'Popen', side_effect=OSError('start failed')):
                with self.assertRaisesRegex(OSError, 'start failed'):
                    mom.launch()
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                self.assertEqual(connection.recv(1), b'')
            self.assertIsNone(mom._active)

    def test_missing_probe_does_not_launch(self):
        with patch.object(mom, 'configuration', return_value=(Path('/tmp'), ['unused'], 38419)), \
                patch.object(socket, 'create_connection', side_effect=ConnectionRefusedError), \
                patch.object(subprocess, 'Popen') as start:
            with self.assertRaises(ConnectionRefusedError):
                mom.launch()
            start.assert_not_called()

    def test_existing_owned_launch_rejected(self):
        with patch.object(mom, '_active', object()):
            with self.assertRaisesRegex(RuntimeError, 'Close'):
                mom.require_closed()


if __name__ == '__main__':
    unittest.main()
