"""
watcher.py
==========
Runs on your Windows PC in the background.
Polls GitHub every 60s for a run request from the Streamlit app.
When found: pulls latest clients.json, runs court_automation.py, pushes results back.

Setup:
  1. Edit GITHUB_TOKEN and GITHUB_REPO below
  2. Double-click START_WATCHER.bat
"""

import json, base64, os, sys, time, subprocess, logging
from datetime import datetime
from pathlib import Path
import requests

# ══════════════════════════════════════════════════════════════
# EDIT THESE TWO LINES
GITHUB_TOKEN = "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"
GITHUB_REPO  = "apmhAI/Court-automation"   # e.g.  apmhai/court-automation
# ══════════════════════════════════════════════════════════════

GITHUB_BRANCH = "main"
POLL_INTERVAL = 60   # seconds
BASE_DIR = Path(__file__).parent.resolve()
SCRIPT   = BASE_DIR / "court_automation.py"
PYTHON   = sys.executable
API      = "https://api.github.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "watcher.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def gh_get(path):
    url = f"{API}/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    raw  = base64.b64decode(data["content"]).decode("utf-8")
    try:
        return json.loads(raw), data["sha"]
    except Exception:
        return {}, data["sha"]


def gh_put(path, content, sha=None, msg="update"):
    encoded = base64.b64encode(
        json.dumps(content, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": msg, "content": encoded, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    url = f"{API}/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.put(url, headers=HEADERS, json=payload, timeout=15)
    r.raise_for_status()
    log.info("Pushed %s", path)


def poll():
    log.debug("Polling GitHub…")
    req_data, req_sha = gh_get("run_request.json")
    if not req_data or req_data.get("status") != "pending":
        return

    date_str = req_data.get("date", "")
    courts   = req_data.get("courts", [])
    log.info("▶ Run request: date=%s courts=%s", date_str, courts)

    # Mark as running
    req_data["status"] = "running"
    req_data["started_at"] = datetime.now().strftime("%d %b %Y %I:%M %p")
    gh_put("run_request.json", req_data, req_sha, "Mark as running")

    # Pull latest clients.json from GitHub
    clients_data, _ = gh_get("clients.json")
    if clients_data:
        (BASE_DIR / "clients.json").write_text(
            json.dumps(clients_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log.info("Updated clients.json (%d NCLT + %d extra)",
                 len(clients_data.get("client_list", [])),
                 len(clients_data.get("extra_names", [])))

    # Build and run command
    flags = []
    if "NCLT"  in courts: flags.append("--nclt")
    if "NCLAT" in courts: flags.append("--nclat")
    if "SCI"   in courts: flags.append("--sci")
    if "BHC"   in courts: flags.append("--bhc")

    cmd = [PYTHON, str(SCRIPT), "--date", date_str, *flags, "--no-prompt"]
    log.info("Running: %s", " ".join(cmd))

    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    log.info("Script finished — return code: %d", result.returncode)

    # Push results back to GitHub
    results_path = BASE_DIR / "last_results.json"
    if results_path.exists():
        try:
            results_data = json.loads(results_path.read_text(encoding="utf-8"))
            # Guard against pushing a stale file from a previous run on a different date.
            # Both NCLT and NCLAT/SCI/BHC now use DD-MM-YYYY so a direct compare works.
            results_date = results_data.get("date", "")
            if results_date != date_str:
                log.warning(
                    "last_results.json date '%s' does not match requested '%s' — "
                    "the automation produced no output for this date. Pushing empty results.",
                    results_date, date_str
                )
                results_data = {"date": date_str, "generated": datetime.now().strftime("%d %b %Y %I:%M %p"), "total": 0, "entries": []}
            old, old_sha = gh_get("last_results.json")
            gh_put("last_results.json", results_data, old_sha,
                   f"Results for {date_str} — {results_data.get('total',0)} match(es)")
            log.info("Pushed last_results.json (%d entries)", results_data.get("total", 0))
        except Exception as e:
            log.error("Could not push results: %s", e)
    else:
        log.warning("last_results.json not found after run — pushing empty results")
        try:
            empty = {"date": date_str, "generated": datetime.now().strftime("%d %b %Y %I:%M %p"), "total": 0, "entries": []}
            old, old_sha = gh_get("last_results.json")
            gh_put("last_results.json", empty, old_sha, f"Empty results for {date_str}")
        except Exception as e:
            log.error("Could not push empty results: %s", e)

    # Clear run request
    _, new_sha = gh_get("run_request.json")
    gh_put("run_request.json",
           {"status": "done", "date": date_str, "courts": courts,
            "completed_at": datetime.now().strftime("%d %b %Y %I:%M %p")},
           new_sha, "Run complete")
    log.info("✓ Done for %s", date_str)


def main():
    if GITHUB_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        print("\n[ERROR] Open watcher.py and set GITHUB_TOKEN and GITHUB_REPO\n")
        input("Press Enter to exit…")
        sys.exit(1)
    if not SCRIPT.exists():
        print(f"\n[ERROR] court_automation.py not found at {SCRIPT}\n")
        input("Press Enter to exit…")
        sys.exit(1)

    log.info("=" * 50)
    log.info("  Watcher started | Repo: %s | Poll: %ds", GITHUB_REPO, POLL_INTERVAL)
    log.info("=" * 50)

    while True:
        try:
            poll()
        except Exception as e:
            log.error("Poll error: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
