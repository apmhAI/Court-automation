"""
login_page.py
=============
Renders the login / signup / forgot-password UI.
Called from app.py before showing the main app.
"""

import streamlit as st
from auth import login, signup, request_password_reset, reset_password, verify_reset_otp


def render_login_page():
    """
    Renders the full auth flow.
    Returns True if the user is authenticated, False otherwise.
    Sets st.session_state.user on success.
    """

    # ── Already logged in ──────────────────────────────────────────────
    if st.session_state.get("authenticated"):
        return True

    # ── Page styling ───────────────────────────────────────────────────
    st.markdown("""
    <style>
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding-top: 2rem; }

    .auth-card {
        background: #ffffff;
        border: 1px solid #e5e5e4;
        border-radius: 16px;
        padding: 2.5rem 2rem;
        max-width: 420px;
        margin: 0 auto;
        box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    }
    .auth-logo {
        text-align: center;
        margin-bottom: 1.5rem;
    }
    .auth-logo .icon {
        font-size: 48px;
        display: block;
        margin-bottom: 8px;
    }
    .auth-logo h1 {
        font-size: 22px;
        font-weight: 700;
        color: #1c1c1b;
        margin: 0;
    }
    .auth-logo p {
        font-size: 13px;
        color: #6b6b69;
        margin: 4px 0 0;
    }
    .auth-divider {
        text-align: center;
        color: #9b9b99;
        font-size: 12px;
        margin: 12px 0;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Logo / header ──────────────────────────────────────────────────
    st.markdown("""
    <div class="auth-logo">
        <span class="icon">⚖️</span>
        <h1>Court Cause List</h1>
        <p>APMH Consulting — Automation Portal</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Screen state ───────────────────────────────────────────────────
    if "auth_screen" not in st.session_state:
        st.session_state.auth_screen = "login"   # login | signup | forgot | verify | done
    if "reset_email" not in st.session_state:
        st.session_state.reset_email = ""

    screen = st.session_state.auth_screen

    # ══════════════════════════════════════════════════════════════════
    # LOGIN
    # ══════════════════════════════════════════════════════════════════
    if screen == "login":
        st.markdown("### Sign in")

        email    = st.text_input("Email address", placeholder="you@apmh.in",
                                  key="login_email")
        password = st.text_input("Password", type="password",
                                  placeholder="Enter your password",
                                  key="login_password")

        col1, col2 = st.columns([1, 1])
        with col1:
            login_btn = st.button("Sign in", type="primary",
                                   use_container_width=True)
        with col2:
            forgot_btn = st.button("Forgot password?",
                                    use_container_width=True)

        if login_btn:
            if not email or not password:
                st.error("Please enter your email and password.")
            else:
                with st.spinner("Signing in…"):
                    ok, result = login(email, password)
                if ok:
                    st.session_state.authenticated = True
                    st.session_state.user = result
                    st.success(f"Welcome back, **{result['name']}**! 👋")
                    st.rerun()
                else:
                    st.error(result)

        if forgot_btn:
            st.session_state.auth_screen = "forgot"
            st.rerun()

        st.markdown("<div class='auth-divider'>— or —</div>", unsafe_allow_html=True)
        if st.button("Create a new account", use_container_width=True):
            st.session_state.auth_screen = "signup"
            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # SIGNUP
    # ══════════════════════════════════════════════════════════════════
    elif screen == "signup":
        st.markdown("### Create account")

        name     = st.text_input("Full name", placeholder="Your name",
                                  key="signup_name")
        email    = st.text_input("Email address", placeholder="you@apmh.in",
                                  key="signup_email")
        password = st.text_input("Password", type="password",
                                  placeholder="Min 6 characters",
                                  key="signup_password")
        confirm  = st.text_input("Confirm password", type="password",
                                  placeholder="Repeat password",
                                  key="signup_confirm")

        signup_btn = st.button("Create account", type="primary",
                                use_container_width=True)

        if signup_btn:
            if not all([name, email, password, confirm]):
                st.error("Please fill in all fields.")
            elif password != confirm:
                st.error("Passwords do not match.")
            else:
                with st.spinner("Creating account…"):
                    ok, msg = signup(name, email, password)
                if ok:
                    st.success("Account created! You can now sign in.")
                    st.session_state.auth_screen = "login"
                    st.rerun()
                else:
                    st.error(msg)

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("← Back to sign in", use_container_width=True):
            st.session_state.auth_screen = "login"
            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # FORGOT PASSWORD — Step 1: Enter email
    # ══════════════════════════════════════════════════════════════════
    elif screen == "forgot":
        st.markdown("### Reset password")
        st.caption("Enter your email and we'll send you a 6-digit reset code.")

        email = st.text_input("Email address", placeholder="you@apmh.in",
                               key="forgot_email")

        send_btn = st.button("Send reset code", type="primary",
                              use_container_width=True)

        if send_btn:
            if not email:
                st.error("Please enter your email address.")
            else:
                with st.spinner("Sending reset code…"):
                    ok, msg = request_password_reset(email)
                if ok:
                    st.session_state.reset_email = email.strip().lower()
                    st.session_state.auth_screen = "verify"
                    st.rerun()
                else:
                    st.error(msg)

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("← Back to sign in", use_container_width=True):
            st.session_state.auth_screen = "login"
            st.rerun()

    # ══════════════════════════════════════════════════════════════════
    # FORGOT PASSWORD — Step 2: Enter OTP + new password
    # ══════════════════════════════════════════════════════════════════
    elif screen == "verify":
        st.markdown("### Enter reset code")
        email = st.session_state.reset_email
        st.info(f"A 6-digit code was sent to **{email}**. Check your inbox.")

        otp      = st.text_input("6-digit code", placeholder="123456",
                                  max_chars=6, key="reset_otp")
        new_pass = st.text_input("New password", type="password",
                                  placeholder="Min 6 characters",
                                  key="reset_new_pass")
        confirm  = st.text_input("Confirm new password", type="password",
                                  placeholder="Repeat new password",
                                  key="reset_confirm")

        reset_btn = st.button("Reset password", type="primary",
                               use_container_width=True)

        if reset_btn:
            if not all([otp, new_pass, confirm]):
                st.error("Please fill in all fields.")
            elif new_pass != confirm:
                st.error("Passwords do not match.")
            else:
                with st.spinner("Resetting password…"):
                    ok, msg = reset_password(email, otp, new_pass)
                if ok:
                    st.success("✅ Password reset successfully! Please sign in with your new password.")
                    st.session_state.auth_screen  = "login"
                    st.session_state.reset_email  = ""
                    st.rerun()
                else:
                    st.error(msg)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Resend code", use_container_width=True):
                with st.spinner("Resending…"):
                    ok, msg = request_password_reset(email)
                if ok:
                    st.success("New code sent!")
                else:
                    st.error(msg)
        with col2:
            if st.button("← Back", use_container_width=True):
                st.session_state.auth_screen = "forgot"
                st.rerun()

    return False


def render_user_menu():
    """
    Renders a small logout button in the top-right of the main app.
    Call this at the top of app.py after authentication passes.
    """
    user = st.session_state.get("user", {})
    name = user.get("name", "User")

    with st.sidebar:
        st.markdown(f"### 👤 {name}")
        st.caption(user.get("email", ""))
        st.divider()
        if st.button("🚪 Sign out", use_container_width=True):
            for key in ["authenticated", "user", "auth_screen",
                        "reset_email", "clients_loaded",
                        "save_msg", "run_msg"]:
                st.session_state.pop(key, None)
            st.rerun()
