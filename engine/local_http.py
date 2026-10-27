"""Bounded HTTP helpers for direct, loopback-only development servers."""

import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class RequestError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class LocalHTTPServer(ThreadingHTTPServer):
    """Limit accepted workers before parsing even the first request line."""

    request_queue_size = 16

    def __init__(self, *args, max_workers=16, header_timeout=10, body_timeout=30, **kwargs):
        if max_workers < 1 or header_timeout <= 0 or body_timeout <= 0:
            raise ValueError("HTTP limits must be positive")
        self.header_timeout = header_timeout
        self.body_timeout = body_timeout
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.header_timeout)
        return request, address

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            try:
                request.setblocking(False)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n"
                                b"Connection: close\r\nRetry-After: 1\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


class LocalRequestMixin:
    """Bound header lifetime and refuse recognizable proxy traffic."""

    def setup(self):
        super().setup()
        self.connection.settimeout(getattr(self.server, 'header_timeout', 10))
        self._headers_expired = False

        def expire():
            self._headers_expired = True
            try:
                self.connection.shutdown(socket.SHUT_RD)
            except OSError:
                pass

        self._header_timer = threading.Timer(getattr(self.server, 'header_timeout', 10), expire)
        self._header_timer.daemon = True
        self._header_timer.start()

    def parse_request(self):
        try:
            valid = super().parse_request()
        finally:
            self._header_timer.cancel()
        if not valid:
            return False
        if self._headers_expired:
            self.send_error(408, 'Request headers timed out')
            return False
        if len(self.headers.get_all('Host', [])) != 1:
            self.send_error(400, 'One Host header is required')
            return False
        if any(name.lower() in {'forwarded', 'via', 'x-real-ip'}
               or name.lower().startswith('x-forwarded-') for name in self.headers):
            self.send_error(403, 'Local servers require a direct connection')
            return False
        self.connection.settimeout(getattr(self.server, 'body_timeout', 30))
        return True

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def finish(self):
        self._header_timer.cancel()
        try:
            super().finish()
        except (ConnectionError, TimeoutError):
            pass


def read_json_body(handler, maximum):
    """Read exactly one uncompressed JSON body within an absolute deadline."""
    headers = handler.headers
    if (len(headers.get_all('Content-Type', [])) != 1
            or headers.get_content_type() != 'application/json'
            or headers.get_all('Transfer-Encoding')
            or len(headers.get_all('Content-Encoding', [])) > 1
            or headers.get('Content-Encoding', 'identity') != 'identity'):
        raise RequestError(415, 'Send an uncompressed JSON request')
    lengths = headers.get_all('Content-Length', [])
    if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
        raise RequestError(400, 'One valid Content-Length is required')
    if len(lengths[0]) > 10:
        raise RequestError(413, 'Request exceeds the size limit')
    size = int(lengths[0])
    if not 0 < size <= maximum:
        raise RequestError(413, 'Request exceeds the size limit')
    deadline = time.monotonic() + getattr(handler.server, 'body_timeout', 30)
    payload = bytearray()
    while len(payload) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RequestError(408, 'Request body timed out')
        handler.connection.settimeout(remaining)
        try:
            chunk = handler.rfile.read1(min(65536, size - len(payload)))
        except TimeoutError:
            raise RequestError(408, 'Request body timed out') from None
        if not chunk:
            raise RequestError(400, 'Incomplete request body')
        payload.extend(chunk)
    if time.monotonic() > deadline:
        raise RequestError(408, 'Request body timed out')
    return bytes(payload)


def _public_parts(raw_path):
    path = unquote(urlsplit(raw_path).path, errors='strict')
    if not path.startswith('/') or '\\' in path or any(ord(c) < 32 for c in path):
        raise ValueError('Invalid public path')
    parts = path[1:].split('/') if path != '/' else ['index.html']
    if any(not part or part.startswith('.') or re.fullmatch(r'[A-Za-z0-9@_.-]+', part) is None
           for part in parts):
        raise ValueError('Invalid public path')
    name = '/'.join(parts)
    allowed = name in {'index.html', 'THIRD_PARTY_NOTICES.md', 'bench/app.js',
                       'bench/style.css', 'runner/manifest.json'}
    allowed |= (len(parts) == 2 and parts[0] == 'runner'
                and re.fullmatch(r'[A-Za-z0-9_-]+\.apk', parts[1]) is not None)
    allowed |= (len(parts) in {3, 4} and parts[0] == 'licenses'
                and (parts[-1] == 'package.json'
                     or re.match(r'^(license|copying|notice)', parts[-1], re.I) is not None))
    if not allowed:
        raise ValueError('Not a published asset')
    return parts


def _open_public_file(directory, parts):
    root = Path(directory).resolve()
    # Open each component without following links where directory-relative opens
    # are supported, including when a local process swaps a directory mid-request.
    if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            os.close(file_fd)
            raise ValueError('Not a regular public file')
        return os.fdopen(file_fd, 'rb')
    path = root
    for part in parts:
        path /= part
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Not a regular public file')
    if not path.is_file():
        raise ValueError('Not a regular public file')
    return path.open('rb')


class PublicFilesMixin:
    """Serve only the packaged artifact contract, never arbitrary site files."""

    def send_head(self):
        try:
            parts = _public_parts(self.path)
            stream = _open_public_file(self.directory, parts)
        except (OSError, ValueError, UnicodeError):
            self.send_error(404, 'Public asset not found')
            return None
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(parts[-1]))
        self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
        self.end_headers()
        return stream
