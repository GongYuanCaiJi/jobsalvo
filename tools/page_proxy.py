#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Loopback proxy that pins every browser connection to its validated IP."""
import contextlib
import select
import socket
import socketserver
import threading
import urllib.parse
from url_origin import origin as _origin


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, resolve, pinned_url, pinned_ip):
        self.resolve_url = resolve
        self.pinned_origin = _origin(pinned_url) if pinned_url else None
        self.pinned_ip = pinned_ip
        super().__init__(address, _Handler)


class _Handler(socketserver.BaseRequestHandler):
    def _read_head(self):
        data = bytearray()
        while b'\r\n\r\n' not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                return b'', b''
            data.extend(chunk)
            if len(data) > 65536:
                raise ValueError('proxy request headers too large')
        boundary = data.index(b'\r\n\r\n') + 4
        return bytes(data[:boundary]), bytes(data[boundary:])

    def _destination(self, url):
        server = self.server
        if server.pinned_origin and _origin(url) == server.pinned_origin:
            return server.pinned_ip
        return server.resolve_url(url)

    def handle(self):
        self.request.settimeout(120)
        upstream = None
        connected = False
        try:
            head, leftover = self._read_head()
            if not head:
                return
            lines = head.split(b'\r\n')
            method, target, version = lines[0].decode('latin-1').split(' ', 2)
            if method.upper() == 'CONNECT':
                parsed = urllib.parse.urlsplit('https://' + target + '/')
                url = urllib.parse.urlunsplit(('https', parsed.netloc, '/', '', ''))
                ip = self._destination(url)
                port = parsed.port or 443
                upstream = socket.create_connection((ip, port), timeout=60)
                upstream.settimeout(120)
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                connected = True
                if leftover:
                    upstream.sendall(leftover)
            else:
                parsed = urllib.parse.urlsplit(target)
                if parsed.scheme.lower() != 'http' or not parsed.netloc:
                    raise ValueError('proxy only accepts HTTP requests and HTTPS CONNECT')
                url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/',
                                               parsed.query, ''))
                ip = self._destination(url)
                port = parsed.port or 80
                upstream = socket.create_connection((ip, port), timeout=60)
                upstream.settimeout(120)
                path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
                lines[0] = f'{method} {path} {version}'.encode('latin-1')
                forwarded = [lines[0]]
                for line in lines[1:]:
                    if line.lower().startswith(b'proxy-connection:'):
                        continue
                    if line.lower().startswith(b'connection:'):
                        forwarded.append(b'Connection: close')
                    else:
                        forwarded.append(line)
                if not any(line.lower().startswith(b'connection:') for line in lines[1:]):
                    forwarded.append(b'Connection: close')
                upstream.sendall(b'\r\n'.join(forwarded) + b'\r\n\r\n' + leftover)
                connected = True
            self._relay(upstream)
        except (OSError, ValueError, IndexError, UnicodeError):
            if not connected:
                with contextlib.suppress(OSError):   # 瀏覽器可能在 502 送到前就關了
                    self.request.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        finally:
            if upstream is not None:
                with contextlib.suppress(OSError):   # 轉送失敗時可能已經關了
                    upstream.close()

    def _relay(self, upstream):
        clients = (self.request, upstream)
        while True:
            try:
                readable, _, _ = select.select(clients, (), (), 120)
            except (OSError, ValueError):
                return
            if not readable:
                return
            for source in readable:
                try:
                    data = source.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                destination = upstream if source is self.request else self.request
                try:
                    destination.sendall(data)
                except OSError:
                    return


class PublicFetchProxy:
    """Short-lived localhost proxy for a single headless page read."""

    def __init__(self, resolve_url, pinned_url='', pinned_ip=''):
        self.server = _Server(('127.0.0.1', 0), resolve_url, pinned_url, pinned_ip)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
