"""
Mac2Windows Menubar App.
Shows a menubar icon. Click it to:
  - Send Clipboard (push current clipboard to the web page)
  - Send File... (pick a file, it becomes downloadable on the web page)
  - Open Web Page (opens the Windows side URL in your browser)
  - Copy Mac IP (so you can paste it on the Windows machine)
"""

import os
import shutil
import threading
import subprocess
import socket
import time
import rumps

import server

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "transfers")
PORT = 5577
CLIPBOARD_POLL_INTERVAL = 1.0  # seconds between clipboard checks


def get_local_ip():
    """Get the Mac's LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_clipboard_text():
    """Read the macOS clipboard via pbpaste."""
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except Exception:
        return ""


class Mac2WindowsApp(rumps.App):
    def __init__(self):
        super().__init__(
            "📡",
            title=None,
            quit_button=None,
        )
        # Build menu
        auto_cb_item = rumps.MenuItem("Auto Clipboard", callback=self.toggle_auto_clipboard)
        auto_cb_item.state = True
        self.menu = [
            auto_cb_item,
            rumps.MenuItem("Send Clipboard Now", callback=self.send_clipboard),
            rumps.MenuItem("Send File...", callback=self.send_file),
            rumps.MenuItem("Send Folder...", callback=self.send_folder),
            None,
            rumps.MenuItem("Open Web Page", callback=self.open_web),
            rumps.MenuItem("Open Received Files", callback=self.open_received_files),
            rumps.MenuItem(f"Mac IP: {get_local_ip()}", callback=None),
            rumps.MenuItem("Copy IP to Clipboard", callback=self.copy_ip),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app, key="q"),
        ]
        # Seed initial clipboard so first change is detected
        self._last_clipboard = get_clipboard_text()
        # Start server in background
        self.server_thread = threading.Thread(
            target=server.run_server,
            kwargs={"host": "0.0.0.0", "port": PORT},
            daemon=True,
        )
        self.server_thread.start()
        threading.Thread(target=self._open_web_when_ready, daemon=True).start()

        # Auto clipboard monitoring
        self.auto_clipboard = True
        self._last_clipboard = ""
        self.clipboard_thread = threading.Thread(
            target=self._clipboard_watcher, daemon=True
        )
        self.clipboard_thread.start()

        rumps.notification(
            "Mac2Windows", "Server started",
            f"Open http://{get_local_ip()}:{PORT} on your Windows machine"
        )

    def _clipboard_watcher(self):
        """Background thread: polls macOS clipboard every second.
        When it detects a change, auto pushes it to the web page."""
        while True:
            try:
                if self.auto_clipboard:
                    text = get_clipboard_text()
                    if text and text != self._last_clipboard:
                        self._last_clipboard = text
                        server.set_clipboard(text)
            except Exception:
                pass
            time.sleep(CLIPBOARD_POLL_INTERVAL)

    def toggle_auto_clipboard(self, sender):
        """Toggle the auto clipboard watcher on/off."""
        self.auto_clipboard = not self.auto_clipboard
        sender.state = self.auto_clipboard
        if self.auto_clipboard:
            rumps.notification("Mac2Windows", "Auto clipboard ON", "Clipboard changes will be sent automatically")
        else:
            rumps.notification("Mac2Windows", "Auto clipboard OFF", "Manual send only")

    def send_clipboard(self, _):
        text = get_clipboard_text()
        if not text:
            rumps.notification("Mac2Windows", "Clipboard empty", "Nothing to send")
            return
        server.set_clipboard(text)
        rumps.notification(
            "Mac2Windows", "Clipboard sent",
            f"{len(text)} chars — open the web page on Windows"
        )

    def send_file(self, _):
        """Open a native macOS file picker via AppleScript, copy file to transfers dir."""
        apple = (
            'set theFile to (choose file with prompt "Select a file to send")\n'
            'set posixPath to POSIX path of theFile\n'
            'return posixPath'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", apple],
                capture_output=True, text=True, timeout=120
            )
        except Exception as e:
            rumps.notification("Mac2Windows", "Error", str(e))
            return
        if result.returncode != 0 or not result.stdout.strip():
            # User cancelled or closed the dialog
            return
        src = result.stdout.strip()
        if not os.path.isfile(src):
            rumps.notification("Mac2Windows", "Not a file", src)
            return
        dst = os.path.join(UPLOAD_DIR, os.path.basename(src))
        shutil.copy2(src, dst)
        server.add_file(dst)
        size_mb = os.path.getsize(dst) / (1024 * 1024)
        rumps.notification(
            "Mac2Windows", "File sent",
            f"{os.path.basename(src)} ({size_mb:.1f} MB)"
        )

    def send_folder(self, _):
        """Open a native macOS folder picker via AppleScript, zip and send."""
        apple = (
            'set theFolder to (choose folder with prompt "Select a folder to send")\n'
            'set posixPath to POSIX path of theFolder\n'
            'return posixPath'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", apple],
                capture_output=True, text=True, timeout=120
            )
        except Exception as e:
            rumps.notification("Mac2Windows", "Error", str(e))
            return
        if result.returncode != 0 or not result.stdout.strip():
            return
        folder_path = result.stdout.strip().rstrip("/")
        if not os.path.isdir(folder_path):
            rumps.notification("Mac2Windows", "Not a folder", folder_path)
            return
        folder_name = os.path.basename(folder_path)
        zip_name = f"{folder_name}.zip"
        zip_path = os.path.join(UPLOAD_DIR, zip_name)
        # Remove trailing slash for shutil.make_archive
        rumps.notification("Mac2Windows", "Compressing...", folder_name)
        shutil.make_archive(
            os.path.join(UPLOAD_DIR, folder_name),  # base path (no .zip)
            "zip",
            root_dir=folder_path,
        )
        # make_archive creates folder_name.zip in UPLOAD_DIR
        if not os.path.exists(zip_path):
            # fallback: find it
            for f in os.listdir(UPLOAD_DIR):
                if f == zip_name:
                    zip_path = os.path.join(UPLOAD_DIR, f)
                    break
        server.add_file(zip_path)
        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        rumps.notification(
            "Mac2Windows", "Folder sent",
            f"{folder_name}.zip ({size_mb:.1f} MB)"
        )

    def _open_web_when_ready(self):
        """Open Safari once the local server is accepting connections."""
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                    pass
            except OSError:
                time.sleep(0.1)
                continue
            self.open_web(None)
            return

    def open_web(self, _):
        url = f"http://{get_local_ip()}:{PORT}/"
        subprocess.Popen(["open", "-a", "Safari", url])

    def copy_ip(self, _):
        ip = get_local_ip()
        subprocess.run(["pbcopy"], input=ip, text=True)
        rumps.notification("Mac2Windows", "IP copied", ip)

    def open_received_files(self, _):
        os.makedirs(server.RECEIVE_DIR, exist_ok=True)
        subprocess.Popen(["open", server.RECEIVE_DIR])

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    Mac2WindowsApp().run()
