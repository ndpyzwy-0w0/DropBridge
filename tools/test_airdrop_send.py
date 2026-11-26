#!/usr/bin/env python3
"""Offline regression test for tools/airdrop_send.py (Q73).

Simulates:
  1. awdl_netd's mDNS UDP proxy (answering _airdrop._tcp PTR/SRV/AAAA)
  2. awdl_netd's outbound TCP proxy (`CONNECT <ipv6> <port>\\n` -> mock iPhone)
  3. A mock iPhone AirDrop HTTPS server that enforces anonymous TLS (rejects
     any self-signed client certificate, matching real iOS Everyone mode) and
     receives /Discover, /Ask, and /Upload (DVZip + cpio).
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import plistlib
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AIRDROP = REPO / 'upstream' / 'winawdl' / 'airdrop'
sys.path.insert(0, str(AIRDROP))
sys.path.insert(0, str(REPO / 'tools'))

import airdrop_send  # noqa: E402
import identity  # noqa: E402
import mdns  # noqa: E402
import transfer  # noqa: E402
import cpio  # noqa: E402


class MockPhoneState:
    def __init__(self, outdir: Path):
        self.outdir = outdir
        self.saw_client_cert = False
        self.received_files = []
        self.ask_status = 200
        self.discover_status = 200


def make_handler(state: MockPhoneState):
    class MockHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):
            pass

        def _read_body(self):
            te = (self.headers.get('Transfer-Encoding') or '').lower()
            if 'chunked' in te:
                chunks = []
                while True:
                    line = self.rfile.readline().strip()
                    size = int(line, 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
                return b''.join(chunks)
            length = int(self.headers.get('Content-Length') or 0)
            return self.rfile.read(length) if length > 0 else b''

        def _send_plist(self, status, value):
            body = plistlib.dumps(value, fmt=plistlib.FMT_BINARY)
            self.send_response(status)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            cert = self.connection.getpeercert(binary_form=True)
            if cert:
                state.saw_client_cert = True
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return

            body = self._read_body()
            if self.path == '/Discover':
                if state.discover_status != 200:
                    self.send_response(state.discover_status)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self._send_plist(200, {
                    'ReceiverComputerName': 'Test iPhone',
                    'ReceiverModelName': 'iPhone16,2',
                })
            elif self.path == '/Ask':
                _ = plistlib.loads(body)
                if state.ask_status != 200:
                    self._send_plist(state.ask_status, {})
                    return
                self._send_plist(200, {'ReceiverRecordData': b''})
            elif self.path == '/Upload':
                import io
                state.outdir.mkdir(parents=True, exist_ok=True)
                decoded = io.BytesIO()
                transfer.decode_dvzip(io.BytesIO(body), decoded, 16 * 1024 * 1024, str(state.outdir))
                decoded.seek(0)
                for entry in cpio.read_entries(decoded):
                    data = b''.join(entry.blocks())
                    if not entry.isfile or entry.pathname in ('.', './'):
                        continue
                    rel = entry.pathname.lstrip('./')
                    target = state.outdir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    state.received_files.append(target)
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()

    return MockHandler


def start_mock_phone(tmpdir: Path, state: MockPhoneState):
    cert, key = identity.ensure_self_signed(str(tmpdir / 'phone_keys'))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    ctx.verify_mode = ssl.CERT_OPTIONAL  # mirrors iOS: inspects cert if sent

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def start_mock_netd_outbound(target_ipv6: str, real_port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen(8)
    proxy_port = srv.getsockname()[1]
    stop = threading.Event()

    def relay(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            try:
                a.close()
            except OSError:
                pass
            try:
                b.close()
            except OSError:
                pass

    def accept_loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                client, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            buf = b''
            while not buf.endswith(b'\n') and len(buf) < 256:
                ch = client.recv(1)
                if not ch:
                    break
                buf += ch
            line = buf.decode('ascii', errors='replace').strip()
            parts = line.split()
            if len(parts) == 3 and parts[0] == 'CONNECT' and parts[1] == target_ipv6:
                up = socket.create_connection(('127.0.0.1', real_port), timeout=5)
                threading.Thread(target=relay, args=(client, up), daemon=True).start()
                threading.Thread(target=relay, args=(up, client), daemon=True).start()
            else:
                client.close()

    threading.Thread(target=accept_loop, daemon=True).start()
    return srv, proxy_port, stop


def start_mock_mdns(target_ipv6: str, service_port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 0))
    mdns_port = sock.getsockname()[1]
    stop = threading.Event()
    svc = mdns.Service('_airdrop._tcp.local', 'aabbccddeeff', 'aabbccddeeff.local',
                       target_ipv6, service_port, 'flags=1')
    reply = svc.response()

    def loop():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                _, addr = sock.recvfrom(4096)
                sock.sendto(reply, addr)
            except socket.timeout:
                continue
            except OSError:
                break

    threading.Thread(target=loop, daemon=True).start()
    return sock, mdns_port, stop


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_file = tmp / 'hello-airdrop.bin'
        payload = os.urandom(96 * 1024)
        src_file.write_bytes(payload)
        expected_hash = hashlib.sha256(payload).hexdigest()

        state = MockPhoneState(tmp / 'phone_out')
        phone_srv, phone_port = start_mock_phone(tmp, state)
        target_ipv6 = 'fe80::1122:33ff:fe44:5566'
        netd_srv, proxy_port, netd_stop = start_mock_netd_outbound(target_ipv6, phone_port)
        mdns_sock, mdns_port, mdns_stop = start_mock_mdns(target_ipv6, phone_port)

        try:
            # 1. CLI single-shot send
            rc = airdrop_send.main([
                '--proxy-port', str(proxy_port),
                '--mdns-port', str(mdns_port),
                '--search', '3',
                '--name', 'DropBridge-Test',
                str(src_file),
            ])
            assert rc == 0, f'CLI send failed: rc={rc}'
            assert not state.saw_client_cert, 'anonymous TLS must NOT send a client cert'
            assert len(state.received_files) == 1
            got_hash = hashlib.sha256(state.received_files[0].read_bytes()).hexdigest()
            assert got_hash == expected_hash, 'SHA-256 mismatch after DVZip/cpio roundtrip'
            print('PASS: CLI anonymous send roundtrip')

            # 2. Interactive --json mode (discover -> send -> sent, plus declined case)
            state.received_files.clear()
            proc = subprocess.Popen(
                [sys.executable, str(REPO / 'tools' / 'airdrop_send.py'),
                 '--json', '--proxy-port', str(proxy_port),
                 '--mdns-port', str(mdns_port), '--name', 'DropBridge-UI'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8',
            )
            seen_events = []
            peer_id = None
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                ev = json.loads(line)
                seen_events.append(ev)
                if ev['event'] == 'peer':
                    peer_id = ev['id']
                    assert ev['name'] == 'Test iPhone'
                    proc.stdin.write(json.dumps({'do': 'send', 'peer': peer_id,
                                                 'files': [str(src_file)]}) + '\n')
                    proc.stdin.flush()
                elif ev['event'] == 'sent':
                    break
            assert peer_id is not None, f'never discovered peer: {seen_events}'
            assert any(e['event'] == 'sent' for e in seen_events), f'no sent event: {seen_events}'
            assert hashlib.sha256(state.received_files[0].read_bytes()).hexdigest() == expected_hash

            # 3. Declined path
            state.ask_status = 401
            proc.stdin.write(json.dumps({'do': 'send', 'peer': peer_id,
                                         'files': [str(src_file)]}) + '\n')
            proc.stdin.flush()
            declined = False
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                ev = json.loads(line)
                if ev['event'] == 'declined':
                    declined = True
                    break
            assert declined, 'expected declined event on HTTP 401'
            proc.stdin.write(json.dumps({'do': 'quit'}) + '\n')
            proc.stdin.flush()
            proc.wait(timeout=5)
            assert proc.returncode == 0
            print('PASS: --json discover/send/declined')
        finally:
            netd_stop.set()
            mdns_stop.set()
            netd_srv.close()
            mdns_sock.close()
            phone_srv.shutdown()
            phone_srv.server_close()

    return 0


if __name__ == '__main__':
    sys.exit(main())
