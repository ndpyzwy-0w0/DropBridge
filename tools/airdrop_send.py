#!/usr/bin/env python3
"""Send files from this PC to an iPhone over AirDrop, through awdl_netd.

Why this exists: netd only accepted *inbound* connections (phone -> PC). The
outbound proxy (opt-in, `AWDL_NETD_OUTBOUND`) lets a local process name the far
end and then speaks the normal AirDrop protocol over it. The protocol itself is
reused from upstream `airdrop/sender.py` (MIT); this file only replaces its
transport and TLS client-certificate assumptions:

  * discovery: the interface browser is replaced by queries through netd's mDNS
    proxy (127.0.0.1:15353), which already carries AWDL multicast;
  * connection: the Windows link-local 4-tuple connect is replaced by a local
    connection to netd's outbound proxy plus a `CONNECT <ipv6> <port>` line;
  * TLS identity: in "Everyone for 10 Minutes" mode an iPhone rejects a
    self-signed client certificate (SSLV3_ALERT_BAD_CERTIFICATE), so we connect
    anonymously (no client cert) unless an Apple-signed identity is provided.
"""
from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent if HERE.name == 'tools' else HERE.parent.parent


def resolve_airdrop_dir() -> Path:
    for candidate in (
        HERE,
        REPO / 'backend' / 'airdrop',
        REPO / 'upstream' / 'winawdl' / 'airdrop',
    ):
        if (candidate / 'sender.py').is_file():
            return candidate
    raise SystemExit('winawdl airdrop modules not found')


AIRDROP = resolve_airdrop_dir()
if str(AIRDROP) not in sys.path:
    sys.path.insert(0, str(AIRDROP))

import mdns  # noqa: E402
import sender  # noqa: E402
try:
    import apple_identity  # noqa: E402
except ImportError:
    apple_identity = None

SERVICE_TYPE = '_airdrop._tcp.local'
DEFAULT_PROXY_PORT = 8772
DEFAULT_MDNS_PORT = 15353
CONNECT_TIMEOUT = 10
PEER_STALE_SECONDS = 30
PROBE_RETRY_SECONDS = 3


def anonymous_client_context(apple_dir: str | None = None) -> ssl.SSLContext:
    """TLS client context: anonymous in Everyone mode, or Apple-signed when present."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if apple_identity is not None and apple_identity.present(apple_dir):
        apple_identity.load_into(context, apple_dir)
    return context


class ProxyBrowser:
    """Receiver discovery through netd's mDNS proxy instead of an interface."""

    def __init__(self, port=DEFAULT_MDNS_PORT, service_type=SERVICE_TYPE):
        self.port = port
        self.instances = mdns.Instances(service_type)
        self.heard = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(0.25)
        self._next_ask = 0.0

    def poll(self):
        now = time.monotonic()
        if now >= self._next_ask:
            self.sock.sendto(mdns.query(self.instances.questions()), ('127.0.0.1', self.port))
            self._next_ask = now + 1.0
        deadline = now + 0.4
        while time.monotonic() < deadline:
            try:
                data, _ = self.sock.recvfrom(9000)
            except socket.timeout:
                break
            self.instances.feed(data, None)
            self.heard += 1
        return self.instances.devices(within=PEER_STALE_SECONDS)

    def close(self):
        self.sock.close()


class ProxyConnection(http.client.HTTPConnection):
    """HTTPS to a link-local receiver, tunnelled through netd's outbound proxy."""

    proxy_port = DEFAULT_PROXY_PORT

    def __init__(self, address, port, scope, context, timeout):
        super().__init__(address, port, timeout=timeout)
        self.context = context

    def connect(self):
        sock = socket.create_connection(('127.0.0.1', self.proxy_port), timeout=self.timeout)
        try:
            sock.settimeout(self.timeout)
            sock.sendall(f'CONNECT {self.host} {self.port}\n'.encode('ascii'))
            self.sock = self.context.wrap_socket(sock)
        except OSError:
            sock.close()
            raise


class JsonEvents:
    def __init__(self, stream=None):
        self._stream = stream or sys.stdout
        self._lock = threading.Lock()

    def say(self, event: str, **fields):
        line = json.dumps({'event': event, **fields}, ensure_ascii=False)
        with self._lock:
            self._stream.write(line + '\n')
            self._stream.flush()


class JsonWatch(sender.Watch):
    def __init__(self, events: JsonEvents, total_bytes: int):
        self.events = events
        self.total_bytes = max(total_bytes, 1)
        self._conn = None
        self._lock = threading.Lock()
        self.cancelled = False
        self.accepted_flag = False
        self._started = time.monotonic()
        self._last_emit = 0.0

    def connected(self, connection):
        with self._lock:
            self._conn = connection
            if self.cancelled:
                self._close_locked()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            self._close_locked()

    def _close_locked(self):
        if self._conn is not None:
            try:
                if self._conn.sock is not None:
                    self._conn.sock.close()
            except OSError:
                pass
            try:
                self._conn.close()
            except OSError:
                pass

    def accepted(self, total):
        self.accepted_flag = True
        self._started = time.monotonic()
        self.events.say('accepted', total=total)

    def progress(self, done):
        now = time.monotonic()
        if now - self._last_emit < 0.15:
            return
        self._last_emit = now
        elapsed = max(now - self._started, 0.001)
        mbps = round(done / (1024 * 1024) / elapsed, 2)
        self.events.say('progress', done=min(done, self.total_bytes),
                        total=self.total_bytes, mbps=mbps)


def run_json_session(settings, context) -> int:
    events = JsonEvents()
    browser = ProxyBrowser(settings.mdns_port)
    peers = {}          # id -> {'name', 'model', 'address', 'port'}
    shy_addrs = set()
    next_probe = {}     # id -> monotonic time
    stop = threading.Event()
    active_watch_lock = threading.Lock()
    active_watch: dict[str, JsonWatch | None] = {'w': None}
    send_lock = threading.Lock()

    def refresh_peers_once():
        found = browser.poll()
        now = time.monotonic()
        # Remove gone peers
        for gone in [k for k in list(peers) if k not in found]:
            peers.pop(gone, None)
            next_probe.pop(gone, None)
            events.say('peer_lost', id=gone)

        for inst, (addr, port) in found.items():
            if inst in peers and peers[inst]['address'] == addr and peers[inst]['port'] == port:
                continue
            if now < next_probe.get(inst, 0.0):
                continue
            try:
                ident = sender.receiver_identity(addr, port, None, context)
            except sender.Unwilling:
                next_probe[inst] = now + 10.0
                if addr not in shy_addrs:
                    shy_addrs.add(addr)
                    events.say('shy', address=addr)
                continue
            except sender.Unreachable:
                next_probe[inst] = now + PROBE_RETRY_SECONDS
                continue
            if ident is None:
                next_probe[inst] = now + 10.0
                if addr not in shy_addrs:
                    shy_addrs.add(addr)
                    events.say('shy', address=addr)
                continue
            name, model = ident
            peers[inst] = {'name': name, 'model': model or '', 'address': addr, 'port': port}
            events.say('peer', id=inst, name=name, model=model or '', address=addr, port=port)

    def discover_loop():
        events.say('ready')
        while not stop.is_set():
            try:
                refresh_peers_once()
            except OSError as error:
                logging.debug('discovery error: %s', error)
            stop.wait(0.35)

    def do_send(peer_id: str, file_paths: list[str]):
        with send_lock:
            target = peers.get(peer_id)
            if not target:
                for p in peers.values():
                    if p['name'] == peer_id:
                        target = p
                        break
            if not target:
                events.say('error', reason='未找到目标设备，请确认 iPhone 隔空投送界面保持开启')
                return
            try:
                files = sender.outgoing(file_paths)
            except sender.Unsendable as error:
                events.say('error', reason=str(error))
                return

            total = sum(f.size for f in files)
            watch = JsonWatch(events, total)
            with active_watch_lock:
                active_watch['w'] = watch

            events.say('asking', id=peer_id, peer=target['name'])
            addr, port = target['address'], target['port']
            try:
                try:
                    outcome = sender.deliver(addr, port, None, context, settings.name, files, watch)
                except (ConnectionRefusedError, TimeoutError):
                    if watch.accepted_flag or watch.cancelled:
                        raise
                    refresh_peers_once()
                    refreshed = peers.get(peer_id)
                    if refreshed and (refreshed['address'], refreshed['port']) != (addr, port):
                        addr, port = refreshed['address'], refreshed['port']
                        outcome = sender.deliver(addr, port, None, context, settings.name, files, watch)
                    else:
                        raise
                if watch.cancelled:
                    events.say('cancelled')
                elif outcome == sender.SENT:
                    events.say('progress', done=total, total=total, mbps=0.0)
                    events.say('sent')
                elif outcome == sender.DECLINED:
                    events.say('declined')
                else:
                    events.say('error', reason='对方拒绝接收文件')
            except sender.Unwilling:
                events.say('error', reason='对方拒收（请确认 iPhone 隔空投送设为「所有人 10 分钟」）')
            except TimeoutError:
                events.say('error', reason='等待对方确认超时')
            except (OSError, http.client.HTTPException) as error:
                if watch.cancelled:
                    events.say('cancelled')
                else:
                    events.say('error', reason=f'传输中断: {error or type(error).__name__}')
            finally:
                with active_watch_lock:
                    active_watch['w'] = None

    disc_thread = threading.Thread(target=discover_loop, name='discover', daemon=True)
    disc_thread.start()

    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = cmd.get('do')
            if action == 'quit':
                break
            elif action == 'cancel':
                with active_watch_lock:
                    w = active_watch['w']
                if w is not None:
                    w.cancel()
            elif action == 'send':
                peer_id = str(cmd.get('peer') or '')
                files = list(cmd.get('files') or [])
                t = threading.Thread(target=do_send, args=(peer_id, files), daemon=True)
                t.start()
    finally:
        stop.set()
        with active_watch_lock:
            if active_watch['w'] is not None:
                active_watch['w'].cancel()
        browser.close()
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('files', nargs='*', help='files to send')
    parser.add_argument('--json', action='store_true',
                        help='interactive JSON lines mode for GUI frontends')
    parser.add_argument('--to', help='part of the receiver name, when several answer')
    parser.add_argument('--address',
                        help='send straight to this IPv6 (skips discovery; the port defaults '
                             'to 8771). Use when the phone answers nothing but its mDNS queries '
                             'show its link-local address in the netd log.')
    parser.add_argument('--search', type=int, default=20,
                        help='seconds to look for receivers (default 20)')
    parser.add_argument('--name', default=socket.gethostname().split('.')[0],
                        help='name the phone shows in its prompt')
    parser.add_argument('--keys', default=str(REPO / 'dist' / 'DropBridge' / 'data' / 'keys'),
                        help='unused for anonymous Everyone mode; kept for CLI compatibility')
    parser.add_argument('--apple-identity', default=None,
                        help='directory with Apple-signed AirDrop cert/key/validation_record.cms')
    parser.add_argument('--proxy-port', type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument('--mdns-port', type=int, default=DEFAULT_MDNS_PORT)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--list', action='store_true',
                        help='only list what answers, send nothing')
    return parser.parse_args(argv)


def main(argv=None):
    settings = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if settings.trace else logging.INFO,
                        stream=sys.stderr if settings.json else sys.stdout,
                        format='%(asctime)s %(levelname)s %(message)s')
    ProxyConnection.proxy_port = settings.proxy_port
    sender.Connection = ProxyConnection      # every sender helper now tunnels
    apple_dir = settings.apple_identity or os.environ.get('DROPBRIDGE_APPLE_IDENTITY')
    if apple_identity is not None and apple_identity.present(apple_dir):
        rec = apple_identity.record_bytes(apple_dir)
        if rec:
            orig_ask_body = sender.ask_body

            def ask_body_with_record(sender_name, files, transfer_id):
                body = orig_ask_body(sender_name, files, transfer_id)
                body['SenderRecordData'] = rec
                return body

            sender.ask_body = ask_body_with_record
    context = anonymous_client_context(apple_dir)

    if settings.json:
        return run_json_session(settings, context)

    if not settings.files and not settings.list:
        raise SystemExit('provide at least one file, or use --list / --json')

    files = sender.outgoing(settings.files) if settings.files else []

    if settings.address:
        address = settings.address.split('%')[0]
        logging.info('sending straight to %s; accept on the phone', address)
        try:
            ok = sender.send(address, 8771, None, context, settings.name, files)
        except TimeoutError:
            logging.warning('%s did not answer in time', address)
            return 1
        except sender.Unwilling:
            logging.warning('%s refused this computer: the iPhone is probably '
                            'set to contacts only', address)
            return 1
        except (OSError, http.client.HTTPException) as error:
            logging.warning('the connection to %s broke: %s', address,
                            error or type(error).__name__)
            return 1
        if ok:
            logging.info('sent %d file(s) to %s', len(files), address)
            return 0
        logging.warning('the phone did not take the files')
        return 1

    browser = ProxyBrowser(settings.mdns_port)
    try:
        named = sender.find_receivers(browser, None, context, settings.to, settings.search)
        logging.info('%d mDNS packets came back through the proxy', browser.heard)
    finally:
        browser.close()
    if not named:
        logging.warning('no receiver answered. On the iPhone open AirDrop and choose '
                        '"Everyone for 10 Minutes", then keep the share sheet open.')
        return 2
    for name, (address, port) in sorted(named.items()):
        logging.info('candidate: %s at %s port %d', name, address, port)
    if settings.list:
        return 0

    name, (address, port) = sender.choose(named, settings.to)
    logging.info('asking %s at %s; accept on the phone', name, address)
    try:
        ok = sender.send(address, port, None, context, settings.name, files)
    except TimeoutError:
        logging.warning('%s did not answer in time', name)
        return 1
    except sender.Unwilling:
        logging.warning('%s refused this computer: the iPhone is probably '
                        'set to contacts only', name)
        return 1
    except (OSError, http.client.HTTPException) as error:
        logging.warning('the connection to %s broke: %s', name, error or type(error).__name__)
        return 1
    if ok:
        logging.info('sent %d file(s) to %s', len(files), name)
        return 0
    logging.warning('the phone did not take the files')
    return 1


if __name__ == '__main__':
    sys.exit(main())
