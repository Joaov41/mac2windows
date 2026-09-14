"""
Flask server for Mac2Windows transfer.
Runs in a background thread inside the menubar app (or standalone).
The Windows machine connects to http://<mac-ip>:5577/ in any browser.
"""

import os
import secrets
import subprocess
import tempfile
import time
import threading
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "transfers")
RECEIVE_DIR = os.path.join(BASE_DIR, "received")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RECEIVE_DIR, exist_ok=True)

MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CLIPBOARD_BYTES = 1024 * 1024
_incoming_token = secrets.token_urlsafe(32)
_incoming_clipboard_lock = threading.Lock()

app = Flask(__name__)

# Shared state (written by menubar app, read by web page)
_state_lock = threading.Lock()
_clipboard_content = {"text": "", "timestamp": 0}
_sent_files = []  # list of {"name", "path", "size", "timestamp"}


def set_clipboard(text: str):
    """Called by menubar app to push clipboard."""
    with _state_lock:
        _clipboard_content["text"] = text
        _clipboard_content["timestamp"] = time.time()


def add_file(filepath: str):
    """Called by menubar app after copying a file into UPLOAD_DIR."""
    with _state_lock:
        _sent_files.append({
            "name": os.path.basename(filepath),
            "path": filepath,
            "size": os.path.getsize(filepath),
            "timestamp": time.time(),
        })
        # Keep last 50
        del _sent_files[:-50]


@app.route("/")
def index():
    # Choose the browser's transfer direction, not the server's operating system.
    user_agent = request.headers.get("User-Agent", "")
    client_is_mac = "Macintosh" in user_agent and "Mobile" not in user_agent
    response = app.make_response(render_template(
        "index.html", incoming_token=_incoming_token, client_is_mac=client_is_mac,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def protect_incoming_transfers():
    """Only this app's page can submit transfers in either direction."""
    if request.endpoint not in ("receive_clipboard", "receive_file", "share_clipboard", "share_file") or request.method != "POST":
        return
    token = request.headers.get("X-Mac2Windows-Token", "")
    if not secrets.compare_digest(token.encode("utf-8"), _incoming_token.encode("utf-8")):
        return jsonify({"error": "Refresh this page and try again."}), 403
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return jsonify({"error": "Send transfers from the Mac2Windows page."}), 403
    request.max_content_length = (
        8 * MAX_CLIPBOARD_BYTES if request.endpoint in ("receive_clipboard", "share_clipboard")
        else MAX_FILE_BYTES + 1024 * 1024
    )


@app.route("/api/receive/clipboard", methods=["POST"])
def receive_clipboard():
    return submit_clipboard(to_windows=False)


@app.route("/api/share/clipboard", methods=["POST"])
def share_clipboard():
    return submit_clipboard(to_windows=True)


def submit_clipboard(to_windows):
    try:
        data = request.get_json(silent=True)
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text:
            return jsonify({"error": "Paste some text before sending."}), 400
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            return jsonify({"error": "The text contains invalid characters."}), 400
        if len(encoded) > MAX_CLIPBOARD_BYTES:
            raise RequestEntityTooLarge()
        if to_windows:
            set_clipboard(text)
        else:
            with _incoming_clipboard_lock:
                subprocess.run(
                    ["/usr/bin/pbcopy"], input=encoded, check=True, timeout=5,
                    env={**os.environ, "LC_ALL": "en_US.UTF-8"},
                )
        return jsonify({"ok": True})
    except RequestEntityTooLarge:
        return jsonify({"error": "Clipboard text is too large (maximum 1 MiB)."}), 413
    except (OSError, subprocess.SubprocessError):
        return jsonify({"error": "Could not update the Mac clipboard. Please try again."}), 503


@app.route("/api/receive/file", methods=["POST"])
def receive_file():
    return submit_file(to_windows=False)


@app.route("/api/share/file", methods=["POST"])
def share_file():
    return submit_file(to_windows=True)


def submit_file(to_windows):
    destination = UPLOAD_DIR if to_windows else RECEIVE_DIR
    temporary_path = None
    try:
        uploads = request.files.getlist("file")
        if len(uploads) != 1 or len(request.files) != 1:
            return jsonify({"error": "Choose one file per upload."}), 400
        uploaded = uploads[0]
        # Strip both Windows and Unix paths; keep writes inside the transfer folder.
        name = secure_filename((uploaded.filename or "").replace("\\", "/").rsplit("/", 1)[-1])
        if not name:
            return jsonify({"error": "This file needs a valid filename."}), 400
        stem, extension = os.path.splitext(name)
        name = stem[:180] + extension[:20]
        os.makedirs(destination, exist_ok=True)
        size = 0
        with tempfile.NamedTemporaryFile(prefix=".incoming-", dir=destination, delete=False) as output:
            temporary_path = output.name
            while True:
                chunk = uploaded.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise RequestEntityTooLarge()
                output.write(chunk)

        stem, extension = os.path.splitext(name)
        number = 1
        while True:
            saved_name = name if number == 1 else f"{stem} ({number}){extension}"
            try:
                # Publish only complete files and never overwrite an existing file.
                os.link(temporary_path, os.path.join(destination, saved_name))
                break
            except FileExistsError:
                number += 1
        if to_windows:
            add_file(os.path.join(destination, saved_name))
        return jsonify({"ok": True, "name": saved_name, "size": size}), 201
    except RequestEntityTooLarge:
        return jsonify({"error": "File is too large (maximum 2 GiB per file)."}), 413
    except OSError:
        app.logger.exception("Could not save an incoming file")
        return jsonify({"error": "Could not save this file on the Mac. Check free space and try again."}), 507
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                app.logger.warning("Could not remove an incomplete upload")


@app.route("/api/state")
def api_state():
    """Polled by the web page via JS for live updates."""
    with _state_lock:
        return jsonify({
            "clipboard": _clipboard_content["text"],
            "clipboard_time": _clipboard_content["timestamp"],
            "files": [
                {"name": f["name"], "size": f["size"], "timestamp": f["timestamp"]}
                for f in _sent_files
            ],
        })


@app.route("/api/clipboard")
def api_clipboard():
    with _state_lock:
        return jsonify({"text": _clipboard_content["text"]})


@app.route("/api/file/<name>")
def api_file(name):
    with _state_lock:
        match = next((f for f in _sent_files if f["name"] == name), None)
    if not match or not os.path.exists(match["path"]):
        return "Not found", 404
    return send_file(match["path"], as_attachment=True, download_name=name)


@app.route("/api/clear", methods=["POST"])
def api_clear():
    with _state_lock:
        _clipboard_content["text"] = ""
        _clipboard_content["timestamp"] = 0
        _sent_files.clear()
    # Remove files from disk
    for f in os.listdir(UPLOAD_DIR):
        os.remove(os.path.join(UPLOAD_DIR, f))
    return jsonify({"ok": True})


def run_server(host="0.0.0.0", port=5577):
    """Start the Flask server (call in a thread)."""
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
