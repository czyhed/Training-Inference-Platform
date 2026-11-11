from __future__ import annotations

import errno
import unittest
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError
from unittest.mock import MagicMock, patch

from backend.providers.youyun.ssh import YouyunSSHClient


class _Channel:
    def recv_exit_status(self) -> int:
        return 0


class _Stdin:
    def close(self) -> None:
        return None


class _Stream:
    channel = _Channel()

    def __init__(self, value: bytes) -> None:
        self.value = value

    def read(self) -> bytes:
        return self.value

    def close(self) -> None:
        return None


class _TransportClient:
    def __init__(self) -> None:
        self.calls = 0

    def exec_command(self, _command: str, timeout: float):
        self.calls += 1
        if self.calls == 1:
            raise EOFError()
        return _Stdin(), _Stream(b"ok"), _Stream(b"")

    def close(self) -> None:
        return None


class _Transport:
    def __init__(self) -> None:
        self.client = _TransportClient()

    def connect(self, **_kwargs):
        return self.client


class YouyunSSHTests(unittest.TestCase):
    def test_run_reconnects_after_eof_and_replays_idempotent_command(self) -> None:
        transport = _Transport()
        client = YouyunSSHClient(transport=transport, command_retries=1, retry_backoff=0)
        client.connect("host", 22, "root", "secret")

        with patch("backend.providers.youyun.ssh.time.sleep"):
            code, output = client.run("test -f /tmp/ready", 30, False, retry_on_disconnect=True)

        self.assertEqual(code, 0)
        self.assertEqual(output, "ok")
        self.assertEqual(transport.client.calls, 2)

    def test_put_text_retries_after_sftp_eof(self) -> None:
        transport = _Transport()
        client = YouyunSSHClient(transport=transport, command_retries=1, retry_backoff=0)
        client.connect("host", 22, "root", "secret")

        bad_sftp = MagicMock()
        bad_sftp.open.side_effect = EOFError()
        good_sftp = MagicMock()
        remote_file = MagicMock()
        good_sftp.open.return_value.__enter__.return_value = remote_file

        with patch(
            "backend.providers.youyun.ssh.YouyunSSHClient._ensure_sftp",
            side_effect=[bad_sftp, good_sftp],
        ), patch("backend.providers.youyun.ssh.time.sleep"):
            client.put_text("payload", "/tmp/install.sh")

        remote_file.write.assert_called_once_with("payload")


class ConnectionRecoveryTests(unittest.TestCase):
    def make_client(self, failures: list[Exception], **kwargs):
        transport = MagicMock()
        connection = MagicMock()
        connection.get_transport.return_value.is_active.return_value = True
        connection.exec_command.return_value = (_Stdin(), _Stream(b"GPU ok"), _Stream(b""))
        transport.connect.side_effect = [connection, *failures, connection]
        client = YouyunSSHClient(transport=transport, retry_backoff=0, **kwargs)
        client.connect("192.0.2.1", 23, "fake", "fake")
        client.run("nvidia-smi")
        client._close_connection()
        connection.exec_command.reset_mock()
        return client, transport, connection

    @staticmethod
    def refused() -> NoValidConnectionsError:
        return NoValidConnectionsError({("192.0.2.1", 23): ConnectionRefusedError(errno.ECONNREFUSED, "refused")})

    def test_predispatch_reconnect_recovers_and_launches_once(self) -> None:
        client, transport, connection = self.make_client([self.refused()])
        self.assertEqual(client.run("installer-launch"), (0, "GPU ok"))
        self.assertEqual(transport.connect.call_count, 3)
        connection.exec_command.assert_called_once_with("installer-launch", timeout=120.0)

    def test_exhausted_reconnect_never_dispatches(self) -> None:
        client, transport, connection = self.make_client([self.refused()] * 8)
        with self.assertRaises(NoValidConnectionsError):
            client.run("installer-launch")
        self.assertGreater(transport.connect.call_count, 2)
        self.assertLessEqual(transport.connect.call_count, 7)
        connection.exec_command.assert_not_called()

    def test_reconnect_cancellation_stops_before_next_attempt(self) -> None:
        class Cancelled(Exception):
            pass
        check = MagicMock(side_effect=[None, Cancelled()])
        client, transport, connection = self.make_client([self.refused()], check_cancelled=check)
        with self.assertRaises(Cancelled):
            client.run("installer-launch")
        self.assertEqual(transport.connect.call_count, 2)
        connection.exec_command.assert_not_called()

    def test_cancellation_during_connect_prevents_command_dispatch(self) -> None:
        class Cancelled(Exception):
            pass
        check = MagicMock(side_effect=[None, Cancelled()])
        client, transport, connection = self.make_client([], check_cancelled=check)
        with self.assertRaises(Cancelled):
            client.run("installer-launch")
        connection.exec_command.assert_not_called()

    def test_reconnect_budget_prevents_another_attempt(self) -> None:
        client, transport, connection = self.make_client([self.refused()])
        with patch("backend.providers.youyun.ssh.time.monotonic", side_effect=[0, 0, 1000]):
            with self.assertRaises(NoValidConnectionsError):
                client.run("installer-launch")
        self.assertEqual(transport.connect.call_count, 2)
        connection.exec_command.assert_not_called()

    def test_authentication_transport_eof_is_retried_not_bad_password(self) -> None:
        client, transport, connection = self.make_client([
            AuthenticationException("Authentication failed: transport shut down or saw EOF")
        ])
        client.run("installer-launch")
        self.assertEqual(transport.connect.call_count, 3)
        connection.exec_command.assert_called_once()

    def test_authentication_failure_is_not_retried(self) -> None:
        client, transport, connection = self.make_client([AuthenticationException("bad credentials")])
        with self.assertRaises(AuthenticationException):
            client.run("installer-launch")
        self.assertEqual(transport.connect.call_count, 2)
        connection.exec_command.assert_not_called()

    def test_local_filesystem_errors_are_not_connection_failures(self) -> None:
        for error in (PermissionError(errno.EACCES, "denied"), FileNotFoundError("missing"), OSError(errno.ENOSPC, "full"), FileNotFoundError(errno.ENOENT, "missing eof.log")):
            self.assertFalse(YouyunSSHClient._is_disconnect_error(error))

    def test_lost_reply_does_not_replay_installer(self) -> None:
        client, transport, connection = self.make_client([])
        stdout = MagicMock()
        stdout.read.side_effect = EOFError()
        connection.exec_command.return_value = (_Stdin(), stdout, _Stream(b""))
        with self.assertRaises(EOFError):
            client.run("installer-launch")
        connection.exec_command.assert_called_once()

    def test_reconnect_backoff_can_be_cancelled(self) -> None:
        class Cancelled(Exception):
            pass
        client = YouyunSSHClient(check_cancelled=MagicMock(side_effect=[None, Cancelled()]))
        with patch("backend.providers.youyun.ssh.time.sleep") as sleep:
            with self.assertRaises(Cancelled):
                client._wait_for_reconnect(8)
        sleep.assert_called_once_with(0.25)

    def test_retry_diagnostics_include_errno_not_credentials(self) -> None:
        client, _, _ = self.make_client([self.refused()])
        callback = MagicMock()
        client.on_connection_retry = callback
        client.run("installer-launch")
        message = callback.call_args.args[0]
        self.assertIn("NoValidConnectionsError; errno=111", message)
        self.assertNotIn("192.0.2.1", message)
        self.assertNotIn("fake", message)

    def test_failed_paramiko_connection_is_closed(self) -> None:
        with patch("paramiko.SSHClient") as factory:
            factory.return_value.connect.side_effect = self.refused()
            with self.assertRaises(NoValidConnectionsError):
                YouyunSSHClient().connect("192.0.2.1", 23, "fake", "fake")
            factory.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
