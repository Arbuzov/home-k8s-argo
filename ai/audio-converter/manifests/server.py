import hmac
import os
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

TOKEN = os.environ['CONVERTER_TOKEN']
if not TOKEN:
    raise RuntimeError('CONVERTER_TOKEN must not be empty')
MAX_BYTES = 20_000_000
SLOTS = BoundedSemaphore(1)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != '/convert':
            self.send_error(404)
            return
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            self.send_error(411)
            return
        if not 0 < size <= MAX_BYTES:
            self.send_error(413)
            return
        if not SLOTS.acquire(blocking=False):
            self.send_error(503, 'Converter busy')
            return
        try:
            self.convert(size)
        finally:
            SLOTS.release()

    def convert(self, size):
        with tempfile.TemporaryDirectory(prefix='audio-', dir='/tmp') as directory:
            source = os.path.join(directory, 'input.m4a')
            result = os.path.join(directory, 'output.mp3')
            self.connection.settimeout(30)
            try:
                with open(source, 'wb') as f:
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            raise EOFError('Incomplete upload')
                        f.write(chunk)
                        remaining -= len(chunk)
                conversion = subprocess.run(
                    ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                     '-i', source, '-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-b:a', '96k',
                     '-threads', '1', '-y', result],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    timeout=300, check=False,
                )
            except (TimeoutError, subprocess.TimeoutExpired, EOFError, OSError):
                self.send_error(500, 'Audio conversion failed')
                return
            if conversion.returncode or not os.path.isfile(result) or not os.path.getsize(result):
                self.send_error(422, 'Audio conversion failed')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Length', str(os.path.getsize(result)))
            self.end_headers()
            with open(result, 'rb') as f:
                shutil.copyfileobj(f, self.wfile)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
