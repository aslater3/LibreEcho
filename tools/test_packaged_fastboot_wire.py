"""Host-only wire regression for the packaged fastboot binary.

Opt-in: set AMONET_RADAR_ZIP to a local amonet-radar ZIP. Without it the test
is skipped. The packaged amonet/bin/fastboot is extracted to a private scratch
directory and run against a loopback TCP fake-fastboot endpoint only
(-s tcp:127.0.0.1:<ephemeral port>). No USB device is addressed.

Asserts that the packaged binary streams the full real fastbrick payload
(length, SHA-256, AMNT prefix) as one raw download, and never takes the system
sparse path.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

ARCHIVE_ENV = "AMONET_RADAR_ZIP"
FASTBOOT_MEMBER = "amonet/bin/fastboot"
PAYLOAD_MEMBER = "amonet/bin/fastbrick.img"
SCRATCH_ROOT = Path.home() / ".hermes" / "cache" / "scratch"
SERVER_TIMEOUT = 30
PROCESS_TIMEOUT = 60
THREAD_JOIN_TIMEOUT = 5
SERIAL_RE = re.compile(r"^tcp:127\.0\.0\.1:(\d{1,5})$")


class FakeFastbootServer:
    """Minimal TCP fake of the fastboot wire protocol, loopback only.

    Records the commands it saw and, for the download data phase, the exact
    byte count and SHA-256 of what the client transmitted.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(SERVER_TIMEOUT)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []
        self.download_len: int | None = None
        self.received_len = 0
        self.received_sha = hashlib.sha256()
        self.received_head = b""
        self.flash_seen = False
        self.errors: list[str] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        self._thread.join(THREAD_JOIN_TIMEOUT)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        conn = None
        try:
            conn, _ = self.sock.accept()
            conn.settimeout(SERVER_TIMEOUT)

            def read(n: int) -> bytes:
                buf = bytearray()
                while len(buf) < n:
                    chunk = conn.recv(n - len(buf))
                    if not chunk:
                        raise EOFError("client closed connection")
                    buf += chunk
                return bytes(buf)

            def send(payload: bytes) -> None:
                conn.sendall(struct.pack(">Q", len(payload)) + payload)

            read(4)
            conn.sendall(b"FB01")
            remaining = 0
            while True:
                size = struct.unpack(">Q", read(8))[0]
                data = read(size)
                if remaining:
                    if len(self.received_head) < 4:
                        self.received_head += data[: 4 - len(self.received_head)]
                    self.received_len += len(data)
                    self.received_sha.update(data)
                    remaining -= len(data)
                    if remaining == 0:
                        send(b"OKAY")
                    continue
                cmd = data.decode("utf-8", "replace")
                self.commands.append(cmd)
                if cmd.startswith("download:"):
                    length = int(cmd.split(":", 1)[1], 16)
                    self.download_len = length
                    remaining = length
                    send(b"DATA" + cmd.split(":", 1)[1].encode())
                elif cmd == "getvar:max-download-size":
                    send(b"OKAY0x6d00000")
                elif cmd.startswith("getvar:partition-size:"):
                    send(b"OKAY0x0")
                elif cmd.startswith("getvar:partition-type:"):
                    send(b"OKAYraw")
                elif cmd.startswith("getvar:has-slot:"):
                    send(b"OKAYno")
                elif cmd == "getvar:is-userspace":
                    send(b"OKAYno")
                elif cmd.startswith("flash:"):
                    self.flash_seen = True
                    send(b"FAILloopback diagnostic stop")
                    break
                else:
                    send(b"FAILunknown variable")
        except Exception as exc:  # recorded for the assertion, never raised in thread
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            self.close()


@unittest.skipUnless(os.environ.get(ARCHIVE_ENV), f"set {ARCHIVE_ENV} to a local amonet-radar ZIP")
class PackagedFastbootWireTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        archive = Path(os.environ[ARCHIVE_ENV]).expanduser()
        if not archive.is_file():
            raise unittest.SkipTest(f"{ARCHIVE_ENV} does not point to a file: {archive}")
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.workdir = Path(tempfile.mkdtemp(prefix="fb-wire-", dir=SCRATCH_ROOT))
        with zipfile.ZipFile(archive) as zf:
            with zf.open(PAYLOAD_MEMBER) as src, open(cls.workdir / "fastbrick.img", "wb") as dst:
                shutil.copyfileobj(src, dst)
            cls.payload_len = zf.getinfo(PAYLOAD_MEMBER).file_size
            with zf.open(FASTBOOT_MEMBER) as src, open(cls.workdir / "fastboot", "wb") as dst:
                shutil.copyfileobj(src, dst)
        (cls.workdir / "fastboot").chmod(0o700)
        cls.payload_path = cls.workdir / "fastbrick.img"
        cls.fastboot_path = cls.workdir / "fastboot"
        cls.payload_sha = hashlib.sha256(cls.payload_path.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "workdir", None) and cls.workdir.exists():
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def test_packaged_fastboot_streams_full_raw_payload(self) -> None:
        self.assertEqual(self.payload_path.stat().st_size, self.payload_len)
        with open(self.payload_path, "rb") as fh:
            self.assertEqual(fh.read(4), b"AMNT", "payload must start with the AMNT prefix")

        server = FakeFastbootServer()
        server.start()
        serial = f"tcp:127.0.0.1:{server.port}"
        self.assertRegex(serial, SERIAL_RE)
        argv = [str(self.fastboot_path), "-s", serial, "flash", "brick", str(self.payload_path)]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=PROCESS_TIMEOUT)
        finally:
            server.join()
            server.close()

        combined = result.stdout + result.stderr
        self.assertEqual(server.errors, [], "fake server reported an error")
        self.assertTrue(server.flash_seen, "client never reached flash after transfer")
        self.assertEqual(server.download_len, self.payload_len, "download length mismatch")
        self.assertIn(f"download:{self.payload_len:08x}", server.commands)
        self.assertEqual(server.received_len, self.payload_len, "transmitted byte count mismatch")
        self.assertEqual(server.received_sha.hexdigest(), self.payload_sha, "transmitted SHA-256 mismatch")
        self.assertEqual(server.received_head, b"AMNT", "transmitted prefix is not AMNT")
        self.assertNotIn("Sending sparse", combined, "packaged binary took the sparse path")
        self.assertIn("Sending 'brick'", combined)
        self.assertNotRegex(combined.lower(), r"\busb\b", "output mentions USB transport")
        self.assertEqual(argv[1], "-s")
        self.assertRegex(argv[2], SERIAL_RE)
        self.assertNotEqual(result.returncode, 0, "fake flash stop should fail the client")


if __name__ == "__main__":
    unittest.main()
