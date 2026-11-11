"""Isolated nginx/installer regression; no production site or GPU is touched."""
from __future__ import annotations

import base64
import hashlib
import http.server
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/nginx/cppt-platform.production.conf"
INSTALLER = ROOT / "scripts/install-platform-gateway.sh"


class GatewayContractTest(unittest.TestCase):
    def test_template_and_pipeline_preserve_upgrade_and_cache_contract(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        for required in ["listen 13101;", "proxy_pass http://127.0.0.1:13100;",
                         "proxy_http_version 1.1;", "proxy_set_header Upgrade $http_upgrade;",
                         "proxy_set_header Connection $cppt_platform_connection_upgrade;",
                         "max-age=31536000, immutable", 'Cache-Control "no-cache"']:
            self.assertIn(required, text)
        pipeline = (ROOT / ".drone.yml").read_text(encoding="utf-8")
        self.assertIn("install-platform-gateway.sh", pipeline)
        self.assertIn("check_training_websocket.py", pipeline)
        self.assertNotIn("configure-app-gateway.sh upsert cppt-platform", pipeline)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "installer requires Linux bash")
class GatewayInstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = self.root / "conf"
        self.conf.mkdir()
        self.target = self.conf / "cppt-platform.conf"
        self.target.write_text("# original gateway\n")
        (self.conf / "other-app.conf").write_text("# untouched\n")
        bindir = self.root / "bin"
        bindir.mkdir()
        nginx = bindir / "nginx"
        nginx.write_text("""#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['GATEWAY_TEST_ROOT'])
args=' '.join(sys.argv[1:])
with (root/'calls').open('a') as f: f.write(args+'\\n')
new='cppt_platform_connection_upgrade' in (root/'conf/cppt-platform.conf').read_text()
if new and ((args=='-t' and (root/'fail-test').exists()) or (args=='-s reload' and (root/'fail-reload').exists())):
    sys.exit(1)
""")
        nginx.chmod(0o755)
        self.env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"], "GATEWAY_TEST_ROOT": str(self.root)}

    def install(self):
        return subprocess.run(["bash", str(INSTALLER), str(self.conf), str(self.root / "backups")], env=self.env, capture_output=True, text=True)

    def test_install_is_repeatable_and_only_updates_this_gateway(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.target.read_text(), TEMPLATE.read_text())
        self.assertEqual((self.conf / "other-app.conf").read_text(), "# untouched\n")
        self.assertEqual(len(list((self.root / "backups").iterdir())), 2)

    def test_config_validation_failure_restores_previous_config(self):
        (self.root / "fail-test").touch()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.target.read_text(), "# original gateway\n")

    def test_reload_failure_restores_and_reloads_previous_config(self):
        (self.root / "fail-reload").touch()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.target.read_text(), "# original gateway\n")
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["-t", "-s reload", "-t", "-s reload"])


@unittest.skipUnless(os.name == "posix" and shutil.which("nginx"), "requires a real nginx binary")
class GatewayUpgradeIntegrationTest(unittest.TestCase):
    def test_nginx_forwards_upgrade_and_websocket_frames(self):
        class Upstream(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.headers.get("Upgrade", "").lower() != "websocket" or self.headers.get("Connection", "").lower() != "upgrade":
                    self.send_error(404)
                    return
                key = self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
                self.end_headers()
                message = b'{"type":"state","taskId":"gateway-probe"}'
                self.wfile.write(bytes([0x81, len(message)]) + message + b"\x88\x02\x03\xe8")
                self.wfile.flush()
                self.close_connection = True
        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = TEMPLATE.read_text().replace("listen 13101;", f"listen 127.0.0.1:{port};").replace("127.0.0.1:13100", f"127.0.0.1:{upstream.server_port}")
            (root / "logs").mkdir()
            lua_root = Path(shutil.which("nginx")).resolve().parents[1] / "lib/lua"
            lua = f'lua_package_path "{lua_root}/?.lua;;";' if (lua_root / "resty/core.lua").is_file() else ""
            (root / "nginx.conf").write_text(f"worker_processes 1;\npid {root}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{ access_log off; {lua} {config} }}")
            with (root / "nginx.log").open("w+") as log:
                process = subprocess.Popen([shutil.which("nginx"), "-p", str(root), "-c", str(root / "nginx.conf"), "-g", "daemon off;"], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            connection = socket.create_connection(("127.0.0.1", port), timeout=1)
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail(log.read())
                            time.sleep(0.02)
                    with connection:
                        connection.sendall(b"GET /api/tasks/gateway-probe/stream HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: Z2F0ZXdheS1wcm9iZTEyMw==\r\n\r\n")
                        data = b""
                        while b"gateway-probe" not in data:
                            chunk = connection.recv(4096)
                            if not chunk:
                                break
                            data += chunk
                    self.assertTrue(data.startswith(b"HTTP/1.1 101"), data[:200])
                    self.assertIn(b"gateway-probe", data)
                finally:
                    process.terminate()
                    process.wait(timeout=5)
