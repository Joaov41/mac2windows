# Mac2Windows

Transfer files and clipboard text between a Mac and a Windows PC on the same local network. The Mac runs a menu bar app; Windows only needs a browser.

## Set up on the Mac

Requires macOS and Python 3.9 or newer. Tested with Python 3.9, Flask 3.1.3, and rumps 0.4.0.

```bash
git clone https://github.com/Joaov41/mac2windows.git
cd mac2windows
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./start-mac2Win.command
```

After setup, double-click `start-mac2Win.command` in the project folder to start the app. It opens the transfer page in **Safari** automatically and adds a menu bar icon. Keep the app running while transferring.

If macOS Firewall asks, allow incoming connections for Python.

## Connect Windows

Connect both computers to the same Wi-Fi or LAN. Find the Mac's address in the menu bar menu, then open this address in the Windows browser:

```text
http://<mac-ip>:5577/
```

No Python or other software is required on Windows. The page checks for updates every two seconds. Refresh the browser page after updating or restarting the Mac app.

## Mac → Windows

The Mac browser shows **Send to Windows** and **Available on Windows**.

- **Clipboard:** copy text on the Mac to share it automatically, or paste text into the page and click **Send to Windows**. On Windows, click **Copy to clipboard**, then paste with **Ctrl+V**.
- **Files:** choose files on the Mac page and click **Send files to Windows**. They appear under **From Mac** in the Windows browser; click a filename to open a browser preview, or **Download** to save it.
- **Folders:** use **Send Folder...** in the Mac menu. The app creates a ZIP file for Windows to download.

The browser does not silently change the Windows system clipboard or download files. Use the Copy and Download buttons on Windows.

## Windows → Mac

The Windows browser shows **Send to Mac**.

- **Clipboard:** paste text into the page and click **Send to Mac clipboard**. After confirmation, paste on the Mac with **Command+V**.
- **Files:** choose one or more files and click **Send files to Mac**. Progress and a result appear for each upload.
- Keep the Mac page open to see a brief filename notification whenever a file arrives from Windows. Multiple arrival notifications appear in sequence.
- The Mac page lists incoming files under **Received from Windows**. Click a filename to open the saved file in its usual Mac app, or use **Download**. Native opening is available only from the page on the Mac. On Windows, browser-supported formats open in a preview tab; other formats download for opening from the browser’s downloads.
- Received files are saved in the project's `received/` folder. Use **Open Received Files** in the Mac menu to find them.

Windows clipboard sending is manual. With **Auto Clipboard** enabled on the Mac, text received from Windows also appears in the shared clipboard display when the Mac watcher reads it.

## Mac menu

- **Auto Clipboard:** turn automatic Mac clipboard sharing on or off; enabled by default.
- **Send Clipboard Now:** share the current Mac clipboard immediately.
- **Send File... / Send Folder...:** share a file or a folder from a native picker.
- **Open Web Page:** open the page in Safari.
- **Open Received Files:** open received files in Finder.
- **Copy IP to Clipboard:** copy the Mac's local IP address.
- **Quit:** stop the app and its server.

## Storage and limits

- Text submissions through the page are limited to **1 MiB** of UTF-8 text.
- Each file uploaded through the page is limited to **2 GiB**. Multiple files upload one at a time.
- Browser uploads use safe filenames and numbered suffixes for duplicates.
- Outgoing files live in `transfers/`; incoming files live in `received/`.
- **Remove** hides one file; **Clear list** hides all currently listed files in that browser. These actions do not delete saved files, change the clipboard, or clear the other computer’s list. Hidden entries stay hidden after a page refresh; newly sent files still appear. Delete saved files through Finder or Windows File Explorer when needed.
- File listings survive app restarts. Shared text is held in memory and resets when the app restarts; received files remain on disk.

## Local network use

The app serves HTTP on port 5577 and listens on all network interfaces. It has no account login or encryption and is intended for a trusted local network. Anyone who can reach the page can access its sharing controls and content. Do not expose the port to the internet.

The virtual environment, clipboard contents, and transferred files are not included in this repository.

## Run the tests

```bash
./venv/bin/python -B -m unittest discover -s tests -v
```

Tests cover both device views, transfer directions, downloads, filename collisions, size limits, request guards, and outgoing-only clearing. They use temporary files and mock writes to the Mac clipboard.

## License

[MIT License](LICENSE). Copyright (c) 2026 Joaov41.
