#!/usr/bin/env python3
"""
server.py — Local launcher for Court Cause List Automation
===========================================================
Run once:  python server.py
Starts web server on http://localhost:5050
Opens court_ui.html in your browser.
"""

import json, os, subprocess, sys, threading, webbrowser, platform
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE_DIR   = Path(__file__).parent.resolve()
SCRIPT     = BASE_DIR / "court_automation.py"
CLIENTS    = BASE_DIR / "clients.json"
UI_FILE    = BASE_DIR / "court_ui.html"
PORT       = 5050
PYTHON     = sys.executable


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # FIX: Serve the HTML file directly from the server
    def _serve_html(self):
        if not UI_FILE.exists():
            self.send_response(404)
            self.end_headers()
            return
        content = UI_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlparse(self.path).path

        # FIX: Serve the UI from the server to avoid CORS file:// issues
        if path in ("/", "/ui", "/index.html"):
            self._serve_html()
        elif path == "/clients":
            if CLIENTS.exists():
                data = json.loads(CLIENTS.read_text(encoding="utf-8"))
            else:
                data = {"client_list": [], "extra_names": []}
            self._json(200, data)
        elif path == "/ping":
            self._json(200, {"status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path    = urlparse(self.path).path
        length  = int(self.headers.get("Content-Length", 0))
        body    = self.rfile.read(length)

        if path == "/save_clients":
            try:
                data = json.loads(body)
                CLIENTS.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                print(f"[server] clients.json saved  "
                      f"({len(data.get('client_list',[]))} NCLT + "
                      f"{len(data.get('extra_names',[]))} extra)")
                self._json(200, {"status": "saved"})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif path == "/run":
            try:
                params = json.loads(body)
                date_str  = params.get("date", "")
                courts    = params.get("courts", [])

                if not date_str:
                    self._json(400, {"error": "date is required"}); return
                if not courts:
                    self._json(400, {"error": "select at least one court"}); return

                flags = []
                if "NCLT"  in courts: flags.append("--nclt")
                if "NCLAT" in courts: flags.append("--nclat")
                if "SCI"   in courts: flags.append("--sci")
                if "BHC"   in courts: flags.append("--bhc")

                cmd = [
                    PYTHON, str(SCRIPT),
                    "--date", date_str,
                    *flags,
                    "--no-prompt",
                ]

                print(f"\n[server] Launching: {' '.join(str(c) for c in cmd)}")

                system = platform.system()
                if system == "Windows":
                    # Write a temp batch file to avoid CMD quoting issues with
                    # paths that contain spaces (e.g. "Cauelist UI" folder).
                    bat = BASE_DIR / "_run_automation.bat"
                    bat.write_text(
                        "@echo off\r\n"
                        f'cd /d "{BASE_DIR}"\r\n'
                        f'"{PYTHON}" "{SCRIPT}" --date {date_str} '
                        f'{" ".join(flags)} --no-prompt\r\n'
                        "echo.\r\n"
                        "echo === Automation finished. You may close this window. ===\r\n"
                        "pause\r\n",
                        encoding="utf-8",
                    )
                    subprocess.Popen(
                        ["cmd", "/c", "start", "Court Automation", str(bat)],
                        cwd=str(BASE_DIR),
                        shell=False,
                    )
                elif system == "Darwin":  # macOS
                    # Use osascript to open Terminal
                    escaped_cmd = " ".join(f'"{c}"' if " " in c else c for c in cmd)
                    subprocess.Popen([
                        "osascript", "-e",
                        f'tell application "Terminal" to do script "cd {BASE_DIR} && {escaped_cmd}"'
                    ])
                else:  # Linux
                    # Try common terminal emulators
                    for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
                        try:
                            if term == "gnome-terminal":
                                subprocess.Popen(
                                    [term, "--", "bash", "-c",
                                     f"cd {BASE_DIR} && {' '.join(cmd)}; read -p 'Press Enter to close...'"],
                                )
                            else:
                                subprocess.Popen(
                                    [term, "-e",
                                     f"bash -c 'cd {BASE_DIR} && {' '.join(cmd)}; read -p Press_Enter'"],
                                )
                            break
                        except FileNotFoundError:
                            continue
                    else:
                        # Fallback: run in background
                        subprocess.Popen(cmd, cwd=str(BASE_DIR))

                self._json(200, {
                    "status":  "launched",
                    "command": " ".join(cmd),
                })

            except Exception as e:
                print(f"[server] Run error: {e}")
                self._json(500, {"error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()


def open_browser():
    import time; time.sleep(1.5)
    # FIX: Open the server-served URL, not the file:// URL
    webbrowser.open(f"http://localhost:{PORT}/")


def main():
    if not SCRIPT.exists():
        print(f"[ERROR] court_automation.py not found in {BASE_DIR}")
        input("Press Enter to exit…")
        sys.exit(1)

    # FIX: Create default clients.json if it doesn't exist
    if not CLIENTS.exists():
        default_data = {
            "client_list": [
                {"name": "MRS. ANITA RAHEJA",                       "court": "MUMBAI BENCH COURT-I"},
                {"name": "MR. SHIV RAHEJA",                         "court": "MUMBAI BENCH COURT-I"},
                {"name": "MR. DEEPAK RAHEJA",                       "court": "MUMBAI BENCH COURT-I"},
                {"name": "MR. ADITYA RAHEJA",                       "court": "MUMBAI BENCH COURT-I"},
                {"name": "MUMBAI WTR PRIVATE LIMITED",              "court": "MUMBAI BENCH COURT-I"},
                {"name": "ALKA RAJESH MIMANI",                      "court": "MUMBAI BENCH COURT-II"},
                {"name": "DEEPAK VIJAY BAGRA",                      "court": "MUMBAI BENCH COURT-II"},
                {"name": "BHARAT MEHTA",                            "court": "MUMBAI BENCH COURT-II"},
                {"name": "LOK HOUSING & CONSTRUCTIONS LTD",         "court": "MUMBAI BENCH COURT-III"},
                {"name": "CALYX LENORA REALTY LLP",                 "court": "MUMBAI BENCH COURT-IV"},
                {"name": "ESSEL INFRAPROJECTS LIMITED",             "court": "MUMBAI BENCH COURT-V"},
                {"name": "ATUL S BHARANI",                          "court": "MUMBAI BENCH COURT-V"},
                {"name": "KASHYAP MEHTA",                           "court": "MUMBAI BENCH COURT-V"},
                {"name": "MR. ATUL TANSUKHLAL MEHTA",               "court": "MUMBAI BENCH COURT-V"},
                {"name": "MR. HEMANT JITENDRARAI MEHTA",            "court": "MUMBAI BENCH COURT-V"},
                {"name": "OKI INDIA PRIVATE LIMITED",               "court": "MUMBAI BENCH COURT-VI"},
                {"name": "SHROFF TEXTILES LIMITED",                 "court": "MUMBAI BENCH COURT-VI"},
                {"name": "KAMLESH CORPORATION",                     "court": "MUMBAI BENCH COURT-VI"},
                {"name": "CHANEL INDIA BUSINESS SOLUTIONS P LTD",  "court": "BENGALURU BENCH COURT-I"},
            ],
            "extra_names": [
                "DEEPAK BAGRA",
                "POOJA RAMESH SINGH",
                "JAMMU AND KASHMIR BANK",
                "J. C. FLOWERS ASSET RECONSTRUCTION PRIVATE LIMITED",
                "S.J.THANAWALLA and ORS",
            ]
        }
        CLIENTS.write_text(json.dumps(default_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[server] Created default {CLIENTS.name}")

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 55)
    print("  Court Automation — Local Server")
    print(f"  Listening on http://localhost:{PORT}")
    print(f"  UI: http://localhost:{PORT}/")
    print(f"  Folder : {BASE_DIR}")
    print(f"  Script : {SCRIPT.name}")
    print(f"  Clients: {CLIENTS.name}")
    print("  Opening UI in browser…")
    print("  Close this window to stop the server.")
    print("=" * 55)

    threading.Thread(target=open_browser, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] Stopped.")


if __name__ == "__main__":
    main()
