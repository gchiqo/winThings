"""Loopback control channel between the CLI and the running tray app.

On Linux `read-aloud.py --stop` wrote/read a PID file and killed a process
group. On Windows the speaker is a thread inside the long-running tray process,
so a second invocation has nothing to kill — it has to *ask* the first one to
stop. A 127.0.0.1 socket on an ephemeral port is the least-ceremony way to do
that; the port and a random token are recorded next to the config, and the token
is checked so another local user's process can't drive the reader.

Any failure (no file, stale port, refused connection) means "no instance is
running", which the CLI treats as a no-op rather than an error.
"""

import json
import os
import secrets
import socket
import threading

import bootstrap  # noqa: F401  (repo root on sys.path)
from common.paths import data_path

PORT_FILE = "control.json"
TIMEOUT = 1.5
MAX_REQUEST_CHARS = 64 * 1024


def _port_file(app):
    return data_path(app, PORT_FILE)


class ControlServer:
    """Accepts one-line JSON commands and hands them to `handler(cmd, payload)`."""

    def __init__(self, app, handler):
        self.app = app
        self.handler = handler
        self.token = secrets.token_hex(16)
        self._sock = None
        self._thread = None
        self._running = False

    def start(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.bind(("127.0.0.1", 0))
            self._sock.listen(4)
        except OSError:
            self._sock = None
            return False
        port = self._sock.getsockname()[1]
        try:
            with open(_port_file(self.app), "w", encoding="utf-8") as fh:
                json.dump({"port": port, "token": self.token, "pid": os.getpid()}, fh)
        except OSError:
            pass
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return True

    def _serve(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            with conn:
                conn.settimeout(TIMEOUT)
                try:
                    line = conn.makefile("r", encoding="utf-8").readline(MAX_REQUEST_CHARS)
                    request = json.loads(line or "{}")
                    token = request.get("token")
                    if not (isinstance(token, str)
                            and secrets.compare_digest(token, self.token)):
                        reply = {"ok": False, "error": "bad token"}
                    else:
                        result = self.handler(request.get("cmd", ""),
                                              request.get("payload") or {})
                        reply = {"ok": True, "result": result}
                except Exception as exc:
                    reply = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(_port_file(self.app))
        except OSError:
            pass


def send(app, cmd, **payload):
    """Send a command to a running instance. Returns the reply dict, or None."""
    try:
        with open(_port_file(app), encoding="utf-8") as fh:
            info = json.load(fh)
        with socket.create_connection(("127.0.0.1", info["port"]), TIMEOUT) as conn:
            conn.settimeout(TIMEOUT)
            conn.sendall((json.dumps(
                {"cmd": cmd, "token": info["token"], "payload": payload}
            ) + "\n").encode("utf-8"))
            return json.loads(conn.makefile("r", encoding="utf-8").readline() or "{}")
    except (OSError, ValueError, KeyError):
        return None


def is_running(app):
    return send(app, "ping") is not None
