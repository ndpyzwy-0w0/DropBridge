#!/usr/bin/env python3
"""Optional Apple ID / Mac AirDrop identity and Contacts-Only verification.

Used by both receiver.py and airdrop_send.py when DROPBRIDGE_APPLE_IDENTITY
points to a directory containing:
  - certificate.der        (Apple-signed AirDrop certificate)
  - key.pk8.der            (PKCS#8 DER private key)
  - validation_record.cms  (Apple validation record)
  - intermediate.der       (optional issuing CA)
  - contact_hashes.txt     (optional SHA-256 hex hashes, one per line)
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import re
import shutil
from pathlib import Path

REQUIRED_FILES = ('certificate.der', 'key.pk8.der', 'validation_record.cms')
OPTIONAL_FILES = ('intermediate.der', 'contact_hashes.txt', 'my_identifiers.txt', 'ble_hashes.txt')
_HEX64 = re.compile(rb'<string>\s*([0-9A-Fa-f]{64})\s*</string>')
_DATA = re.compile(rb'<data>\s*([A-Za-z0-9+/=\s]+?)\s*</data>')


def identity_dir(explicit: str | None = None) -> str:
    return (explicit or os.environ.get('DROPBRIDGE_APPLE_IDENTITY') or '').strip()


def receive_mode() -> str:
    mode = (os.environ.get('DROPBRIDGE_RECEIVE_MODE') or 'auto').strip().lower()
    if mode in ('contacts', 'ask'):
        return mode
    return 'auto'


def media_format() -> str:
    fmt = (os.environ.get('DROPBRIDGE_MEDIA_FORMAT') or 'compatible').strip().lower()
    return 'original' if fmt == 'original' else 'compatible'


def media_capabilities_dict() -> dict:
    if media_format() == 'original':
        return {
            'Version': 1,
            'AssetBundle': {
                'Version': 1,
                'Video': ['hevc', 'h264'],
                'Image': ['heic', 'jpeg'],
            },
        }
    return {'Version': 1}


def present(directory: str | None = None) -> bool:
    d = identity_dir(directory)
    if not d:
        return False
    p = Path(d)
    return all((p / name).is_file() for name in REQUIRED_FILES)


def record_bytes(directory: str | None = None) -> bytes | None:
    d = identity_dir(directory)
    if not present(d):
        return None
    try:
        return (Path(d) / 'validation_record.cms').read_bytes()
    except OSError:
        return None


def _pem(der: bytes, label: str) -> str:
    b64 = base64.b64encode(der).decode('ascii')
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return f'-----BEGIN {label}-----\n' + '\n'.join(lines) + f'\n-----END {label}-----\n'


def load_into(context, directory: str | None = None) -> bool:
    d = identity_dir(directory)
    if not present(d):
        return False
    p = Path(d)
    try:
        tls_dir = p / 'tls'
        tls_dir.mkdir(parents=True, exist_ok=True)
        chain = _pem((p / 'certificate.der').read_bytes(), 'CERTIFICATE')
        inter = p / 'intermediate.der'
        if inter.is_file():
            chain += _pem(inter.read_bytes(), 'CERTIFICATE')
        cert_pem = tls_dir / 'chain.pem'
        key_pem = tls_dir / 'key.pem'
        cert_pem.write_text(chain, encoding='ascii')
        key_pem.write_text(_pem((p / 'key.pk8.der').read_bytes(), 'PRIVATE KEY'), encoding='ascii')
        context.load_cert_chain(str(cert_pem), str(key_pem))
        return True
    except Exception as error:
        logging.warning('Apple identity unusable, staying anonymous: %s', error)
        return False


def load_contact_hashes(directory: str | None = None) -> set[str]:
    d = identity_dir(directory)
    if not d:
        return set()
    p = Path(d) / 'contact_hashes.txt'
    if not p.is_file():
        return set()
    hashes: set[str] = set()
    try:
        for raw in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            h = raw.strip().lower()
            if len(h) == 64 and all(c in '0123456789abcdef' for c in h):
                hashes.add(h)
    except OSError:
        pass
    return hashes


def normalize_contact(identifier: str) -> str:
    s = identifier.strip()
    if '@' in s:
        return s.lower()
    digits = ''.join(ch for ch in s if ch.isdigit() or ch == '+')
    if digits.startswith('00'):
        digits = '+' + digits[2:]
    elif not digits.startswith('+'):
        if len(digits) == 11 and digits.startswith('1'):
            digits = '+86' + digits
        else:
            digits = '+' + digits
    return digits


def hash_contact(identifier: str) -> str:
    norm = normalize_contact(identifier)
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()


def compute_ble_hashes(identifiers: list[str]) -> list[str]:
    prefixes: list[str] = []
    for item in identifiers:
        item = item.strip()
        if not item:
            continue
        h = hash_contact(item)
        prefix = h[:4]
        if prefix not in prefixes:
            prefixes.append(prefix)
        if len(prefixes) >= 4:
            break
    return prefixes


def save_my_identifiers(identifiers: list[str], directory: str | None = None) -> list[str]:
    d = identity_dir(directory)
    if not d:
        return []
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    cleaned = [x.strip() for x in identifiers if x.strip()]
    (p / 'my_identifiers.txt').write_text('\n'.join(cleaned) + ('\n' if cleaned else ''), encoding='utf-8')
    prefixes = compute_ble_hashes(cleaned)
    (p / 'ble_hashes.txt').write_text(','.join(prefixes), encoding='ascii')
    return prefixes


def load_ble_hashes(directory: str | None = None) -> str:
    d = identity_dir(directory)
    if not d:
        return ''
    p = Path(d) / 'ble_hashes.txt'
    if p.is_file():
        return p.read_text(encoding='ascii', errors='ignore').strip()
    return ''


def extract_record_hashes(blob: bytes) -> set[str]:
    if not blob:
        return set()
    found: set[str] = set()
    for m in _HEX64.finditer(blob):
        found.add(m.group(1).decode('ascii').lower())
    for m in _DATA.finditer(blob):
        try:
            raw = base64.b64decode(b''.join(m.group(1).split()))
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 32:
            found.add(raw.hex())
    return found


def sender_is_trusted(ask_plist: dict, directory: str | None = None) -> bool:
    trusted = load_contact_hashes(directory)
    if not trusted:
        return False
    rec = ask_plist.get('SenderRecordData')
    if not isinstance(rec, (bytes, bytearray)) or not rec:
        return False
    return bool(trusted & extract_record_hashes(bytes(rec)))


def import_from(src_dir: str, dst_dir: str) -> tuple[bool, str]:
    src = Path(src_dir)
    missing = [name for name in REQUIRED_FILES if not (src / name).is_file()]
    if missing:
        return False, '缺少必需文件：' + ', '.join(missing)
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES + OPTIONAL_FILES:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dst / name)
    return True, 'ok'
