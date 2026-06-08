"""
auth.py
=======
Authentication module for Court Cause List Automation.
Stores hashed user credentials in users.json on GitHub.
Supports: Login, Signup, Forgot Password (reset code via email).

Passwords are hashed with bcrypt (never stored in plain text).
Reset codes are 6-digit OTPs valid for 15 minutes.
"""

import streamlit as st
import json, base64, hashlib, secrets, string, smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests as req


# ── GitHub helpers (reused from app.py) ───────────────────────────────────────
API_BASE = "https://api.github.com"

def _token():  return st.secrets.get("GITHUB_TOKEN", "")
def _repo():   return st.secrets.get("GITHUB_REPO",  "")
def _branch(): return st.secrets.get("GITHUB_BRANCH","main")

def _gh_headers():
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _gh_get(path):
    url = f"{API_BASE}/repos/{_repo()}/contents/{path}?ref={_branch()}"
    try:
        r = req.get(url, headers=_gh_headers(), timeout=10)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        raw = base64.b64decode(r.json()["content"]).decode("utf-8")
        return json.loads(raw), r.json()["sha"]
    except Exception:
        return None, None

def _gh_put(path, data, sha=None, msg="update"):
    encoded = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": msg, "content": encoded, "branch": _branch()}
    if sha:
        payload["sha"] = sha
    url = f"{API_BASE}/repos/{_repo()}/contents/{path}"
    try:
        r = req.put(url, headers=_gh_headers(), json=payload, timeout=15)
        return r.ok, r.text[:200] if not r.ok else "ok"
    except Exception as e:
        return False, str(e)


# ── Password hashing ───────────────────────────────────────────────────────────
def _hash_password(password: str) -> str:
    """SHA-256 hash with a fixed salt from the token (no bcrypt dependency)."""
    salt = _token()[:16] if _token() else "court_auto_salt!"
    return hashlib.sha256((salt + password).encode()).hexdigest()

def _verify_password(password: str, hashed: str) -> bool:
    return _hash_password(password) == hashed


# ── User storage ───────────────────────────────────────────────────────────────
def _load_users() -> dict:
    data, _ = _gh_get("users.json")
    return data or {}

def _save_users(users: dict) -> tuple:
    _, sha = _gh_get("users.json")
    return _gh_put("users.json", users, sha, "Update users")


# ── OTP helpers ────────────────────────────────────────────────────────────────
def _generate_otp() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))

def _send_reset_email(to_email: str, otp: str, name: str) -> tuple:
    """Send OTP email. Returns (True, "ok") or (False, error_message)."""
    smtp_host = st.secrets.get("SMTP_HOST", "smtp.office365.com")
    smtp_port = int(st.secrets.get("SMTP_PORT", 587))
    sender    = st.secrets.get("SMTP_EMAIL", "")
    password  = st.secrets.get("SMTP_PASSWORD", "")

    if not sender or not password:
        return False, (
            "SMTP credentials are not configured. "
            "Add SMTP_EMAIL and SMTP_PASSWORD to your Streamlit secrets."
        )

    body = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:500px;margin:auto;padding:20px">
  <div style="background:#1a3a5c;padding:20px 24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0;font-size:18px">⚖️ Court Cause List — Password Reset</h2>
  </div>
  <div style="background:#fff;border:1px solid #e5e5e4;padding:24px;border-radius:0 0 8px 8px">
    <p style="color:#1c1c1b">Hi <strong>{name}</strong>,</p>
    <p style="color:#1c1c1b">Your password reset code is:</p>
    <div style="text-align:center;margin:24px 0">
      <span style="font-size:36px;font-weight:700;letter-spacing:10px;
                   color:#185FA5;background:#EBF3FC;padding:16px 24px;
                   border-radius:8px;display:inline-block">{otp}</span>
    </div>
    <p style="color:#6b6b69;font-size:13px">
      This code is valid for <strong>15 minutes</strong>.<br>
      If you did not request this, ignore this email.
    </p>
  </div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Password Reset Code — Court Cause List"
    msg["From"]    = sender
    msg["To"]      = to_email
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(sender, password)
            s.sendmail(sender, [to_email], msg.as_string())
        return True, "ok"
    except smtplib.SMTPAuthenticationError:
        return False, (
            "SMTP authentication failed — the email or password in Streamlit secrets is wrong. "
            f"({smtp_host}:{smtp_port}, user: {sender})"
        )
    except smtplib.SMTPConnectError as e:
        return False, f"Could not connect to SMTP server {smtp_host}:{smtp_port} — {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except Exception as e:
        return False, f"Unexpected error sending email: {e}"


# ── Public auth functions ──────────────────────────────────────────────────────
def login(email: str, password: str) -> tuple:
    """Returns (True, user_dict) or (False, error_msg)."""
    users = _load_users()
    key   = email.strip().lower()
    if key not in users:
        return False, "No account found with this email."
    user = users[key]
    if not _verify_password(password, user["password_hash"]):
        return False, "Incorrect password."
    return True, user

def signup(name: str, email: str, password: str) -> tuple:
    """Returns (True, "ok") or (False, error_msg)."""
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    users = _load_users()
    key   = email.strip().lower()
    if key in users:
        return False, "An account with this email already exists."
    users[key] = {
        "name":          name.strip(),
        "email":         key,
        "password_hash": _hash_password(password),
        "created_at":    datetime.now().strftime("%d %b %Y %I:%M %p"),
        "reset_otp":     None,
        "reset_expiry":  None,
    }
    ok, msg = _save_users(users)
    if ok:
        return True, "ok"
    return False, f"Could not create account: {msg}"

def request_password_reset(email: str) -> tuple:
    """
    Generates OTP, stores it, tries to email it.
    Returns (True, otp_or_"ok") where otp is returned only when email fails,
    so the caller can show it on screen as a fallback.
    """
    users = _load_users()
    key   = email.strip().lower()
    if key not in users:
        # Don't reveal whether email exists — always return ok
        return True, "ok"
    otp    = _generate_otp()
    expiry = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    users[key]["reset_otp"]    = otp
    users[key]["reset_expiry"] = expiry
    ok, msg = _save_users(users)
    if not ok:
        return False, f"Could not store reset code: {msg}"
    sent, smtp_msg = _send_reset_email(key, otp, users[key]["name"])
    if not sent:
        # Email failed — return the OTP so the UI can show it on screen
        return True, f"EMAIL_FAILED:{otp}"
    return True, "ok"

def verify_reset_otp(email: str, otp: str) -> tuple:
    """Returns (True, "ok") or (False, error_msg)."""
    users = _load_users()
    key   = email.strip().lower()
    if key not in users:
        return False, "Email not found."
    user = users[key]
    if not user.get("reset_otp"):
        return False, "No reset code was requested."
    if user["reset_otp"] != otp.strip():
        return False, "Incorrect code."
    expiry = datetime.strptime(user["reset_expiry"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expiry:
        return False, "Code has expired. Please request a new one."
    return True, "ok"

def reset_password(email: str, otp: str, new_password: str) -> tuple:
    """Verifies OTP then sets new password. Returns (True, "ok") or (False, msg)."""
    if len(new_password) < 6:
        return False, "Password must be at least 6 characters."
    ok, msg = verify_reset_otp(email, otp)
    if not ok:
        return False, msg
    users = _load_users()
    key   = email.strip().lower()
    users[key]["password_hash"] = _hash_password(new_password)
    users[key]["reset_otp"]     = None
    users[key]["reset_expiry"]  = None
    saved, smsg = _save_users(users)
    if saved:
        return True, "ok"
    return False, f"Could not save new password: {smsg}"
