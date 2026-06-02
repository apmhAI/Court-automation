"""
Court Cause List Automation — Streamlit UI
==========================================
Hosted on Streamlit Cloud.
Scraping runs on your local Windows PC via watcher.py.
"""

import streamlit as st
import json, time, base64, requests as req
from datetime import datetime, timedelta, timezone
from login_page import render_login_page, render_user_menu

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Court Cause List",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
.stTabs [data-baseweb="tab"] { font-size: 14px; font-weight: 500; }
</style>
""", unsafe_allow_html=True)

# ── AUTH GATE — show login page until authenticated ───────────────────────────
if not render_login_page():
    st.stop()   # stop here — don't render anything below until logged in

# ── Show user menu in sidebar ──────────────────────────────────────────────────
render_user_menu()

# ── GitHub config from secrets ────────────────────────────────────────────────
def _token():  return st.secrets.get("GITHUB_TOKEN", "")
def _repo():   return st.secrets.get("GITHUB_REPO", "")
def _branch(): return st.secrets.get("GITHUB_BRANCH", "main")

API_BASE = "https://api.github.com"

def _gh_headers():
    return {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

# ── GitHub read/write ──────────────────────────────────────────────────────────
def gh_get(path):
    """Returns (data_dict, sha) or (None, None)."""
    url = f"{API_BASE}/repos/{_repo()}/contents/{path}?ref={_branch()}"
    try:
        r = req.get(url, headers=_gh_headers(), timeout=10)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        raw = base64.b64decode(r.json()["content"]).decode("utf-8")
        return json.loads(raw), r.json()["sha"]
    except Exception as e:
        st.error(f"GitHub read error ({path}): {e}")
        return None, None

def gh_put(path, data, sha=None, msg="update"):
    """Returns (True, "ok") or (False, error_msg)."""
    encoded = base64.b64encode(
        json.dumps(data, indent=2, ensure_ascii=False).encode()
    ).decode()
    payload = {"message": msg, "content": encoded, "branch": _branch()}
    if sha:
        payload["sha"] = sha
    url = f"{API_BASE}/repos/{_repo()}/contents/{path}"
    try:
        r = req.put(url, headers=_gh_headers(), json=payload, timeout=15)
        if r.ok:
            return True, "ok"
        return False, f"GitHub {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)

# ── Session state ──────────────────────────────────────────────────────────────
if "clients_loaded" not in st.session_state:
    data, _ = gh_get("clients.json")
    if data:
        st.session_state.client_list  = data.get("client_list", [])
        st.session_state.extra_names  = data.get("extra_names", [])
    else:
        st.session_state.client_list  = []
        st.session_state.extra_names  = []
    st.session_state.clients_loaded = True

if "save_msg"   not in st.session_state: st.session_state.save_msg  = None
if "run_msg"    not in st.session_state: st.session_state.run_msg   = None

COURT_BENCH_MAP = {
    "MUMBAI BENCH COURT-I":    "MB-I",
    "MUMBAI BENCH COURT-II":   "MB-II",
    "MUMBAI BENCH COURT-III":  "MB-III",
    "MUMBAI BENCH COURT-IV":   "MB-IV",
    "MUMBAI BENCH COURT-V":    "MB-V",
    "MUMBAI BENCH COURT-VI":   "MB-VI",
    "BENGALURU BENCH COURT-I": "BLR-I",
}
DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

def all_clients():
    lst = [{"name": c["name"], "court": c["court"], "type": "nclt"}
           for c in st.session_state.client_list]
    lst += [{"name": n, "court": "", "type": "other"}
            for n in st.session_state.extra_names]
    return lst

def save_clients_to_github():
    payload = {
        "client_list": st.session_state.client_list,
        "extra_names":  st.session_state.extra_names,
    }
    _, sha = gh_get("clients.json")
    ok, msg = gh_put("clients.json", payload, sha, "Update clients from Streamlit UI")
    st.session_state.save_msg = ("ok" if ok else "err", msg)

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    st.markdown("## ⚖️ Court Cause List Automation")
    st.caption("NCLT &nbsp;·&nbsp; NCLAT &nbsp;·&nbsp; Supreme Court of India &nbsp;·&nbsp; Bombay High Court")
with col_h2:
    rr, rr_hdr_sha = gh_get("run_request.json")
    if rr and rr.get("status") == "pending":
        st.warning("⏳ Run pending…")
        if st.button("🚫 Reset", key="hdr_reset"):
            gh_put("run_request.json", {"status": "idle"}, rr_hdr_sha, "Reset stuck run")
            st.rerun()
    elif rr and rr.get("status") == "running":
        st.info("⚙️ Running…")
        if st.button("🚫 Reset", key="hdr_reset2"):
            gh_put("run_request.json", {"status": "idle"}, rr_hdr_sha, "Reset stuck run")
            st.rerun()
    else:
        st.success("✓ Ready")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════
tab_run, tab_results, tab_clients = st.tabs(["▶  Run", "📋  Results", "👤  Clients"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — RUN
# ══════════════════════════════════════════════════════════════════════════════
with tab_run:
    col_l, col_r = st.columns(2, gap="large")

    with col_l:
        st.subheader("📅 Date")
        preset = st.radio("", ["Today", "Yesterday", "Custom date"],
                          horizontal=True, label_visibility="collapsed")
        if preset == "Today":
            run_date = datetime.today()
        elif preset == "Yesterday":
            run_date = datetime.today() - timedelta(days=1)
        else:
            picked = st.date_input("Pick a date", value=datetime.today())
            run_date = datetime.combine(picked, datetime.min.time())

        weekday      = DAYS[run_date.weekday()]
        date_display = run_date.strftime("%d %B %Y")
        date_arg     = run_date.strftime("%d-%m-%Y")

        if run_date.weekday() >= 5:
            st.warning(f"⚠️ {weekday} — courts may not be sitting.")
        else:
            st.success(f"✓ {date_display} ({weekday})")

    with col_r:
        st.subheader("🏛️ Courts")
        run_nclt  = st.checkbox("NCLT",  value=True)
        run_nclat = st.checkbox("NCLAT", value=True)
        run_sci   = st.checkbox("Supreme Court of India")
        run_bhc   = st.checkbox("Bombay High Court")

    st.divider()

    # Clients preview
    clients_preview = all_clients()
    with st.expander(f"👤 {len(clients_preview)} clients will be searched"):
        for c in clients_preview:
            bench = COURT_BENCH_MAP.get(c["court"], "NCLAT/SCI/BHC")
            st.markdown(f"- **{c['name']}** &nbsp; `{bench}`")

    st.divider()

    courts_sel = [c for c, f in [("NCLT", run_nclt), ("NCLAT", run_nclat),
                                   ("SCI", run_sci),   ("BHC", run_bhc)] if f]

    if not courts_sel:
        st.warning("Select at least one court to continue.")
    else:
        btn = st.button(
            f"▶ Run — {date_display}  ·  {', '.join(courts_sel)}",
            type="primary", use_container_width=True
        )
        if btn:
            requested_at = datetime.now().strftime("%d %b %Y %I:%M %p")

            # 1. Write pending status to run_request.json
            payload = {
                "status":       "pending",
                "date":         date_arg,
                "courts":       courts_sel,
                "requested_at": requested_at,
            }
            _, sha = gh_get("run_request.json")
            ok, err = gh_put("run_request.json", payload, sha, "Run request from Streamlit UI")

            if ok:
                # 2. Trigger GitHub Actions workflow (runs on cloud — no PC needed)
                dispatch_url = f"https://api.github.com/repos/{_repo()}/actions/workflows/run_automation.yml/dispatches"
                dispatch_payload = {
                    "ref": _branch(),
                    "inputs": {
                        "date":         date_arg,
                        "courts":       ",".join(courts_sel),
                        "requested_at": requested_at,
                    }
                }
                dr = req.post(dispatch_url, headers=_gh_headers(), json=dispatch_payload, timeout=15)
                if dr.status_code == 204:
                    st.session_state.run_msg = ("ok",
                        f"✅ Run started for **{date_display}** · "
                        f"Courts: **{', '.join(courts_sel)}**\n\n"
                        "GitHub Actions is now running the automation in the cloud (~5–10 min). "
                        "Switch to the **Results** tab to see progress.")
                else:
                    st.session_state.run_msg = ("warn",
                        f"⚠️ Run queued but GitHub Actions dispatch failed ({dr.status_code}). "
                        "Make sure the token in Streamlit secrets has the **workflow** scope. "
                        "The run request is saved — watcher.py can still pick it up if running.")
            else:
                st.session_state.run_msg = ("err",
                    f"❌ Could not write run request to GitHub: {err}")
            st.rerun()

    if st.session_state.run_msg:
        kind, msg = st.session_state.run_msg
        if kind == "ok":   st.success(msg)
        elif kind == "warn": st.warning(msg)
        else:              st.error(msg)

    # Show pending status
    rr2, rr_sha = gh_get("run_request.json")
    if rr2 and rr2.get("status") in ("pending", "running"):
        st.info(
            f"⏳ **{'Pending' if rr2['status']=='pending' else 'Running'}** &nbsp;·&nbsp; "
            f"Date: `{rr2.get('date','')}` &nbsp;·&nbsp; "
            f"Courts: {', '.join(rr2.get('courts',[]))} &nbsp;·&nbsp; "
            f"Requested: {rr2.get('requested_at','')}"
        )
        if st.button("🚫 Cancel run"):
            gh_put("run_request.json", {"status": "idle"}, rr_sha, "Cancel run")
            st.session_state.run_msg = None
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — RESULTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_results:
    import pandas as pd

    col_r1, col_r2 = st.columns([4, 1])
    with col_r1:
        st.subheader("📋 Scraped results")
    with col_r2:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    results, _ = gh_get("last_results.json")
    entries = (results or {}).get("entries", [])

    # Warn if results are from a different date than the last run request
    rr_check, _ = gh_get("run_request.json")
    if rr_check:
        last_req_date = rr_check.get("date", "")          # format: DD-MM-YYYY
        results_date  = (results or {}).get("date", "")   # format: DD-MM-YYYY
        if last_req_date and results_date and last_req_date != results_date:
            # Convert for display
            try:
                d = datetime.strptime(last_req_date, "%d-%m-%Y").strftime("%d %B %Y")
            except Exception:
                d = last_req_date
            st.warning(
                f"⚠️ **These results are from a previous run** — showing **{results_date}**, "
                f"but the last request was for **{d}**. "
                "The new run is still in progress or has not started yet."
            )

    if not entries:
        st.info(" NO RESULT FOUND ...")
    else:
        run_date_r  = (results or {}).get("date", "")
        generated_r = (results or {}).get("generated", "")

        st.caption(
            f"**{len(entries)} match(es)** for **{run_date_r}** &nbsp;·&nbsp; "
            f"Generated {generated_r}"
        )

        # Summary metrics
        counts = {}
        clients_found = set()
        for e in entries:
            src = e.get("source", "?")
            counts[src] = counts.get(src, 0) + 1
            clients_found.add(e.get("client", ""))

        mcols = st.columns(len(counts) + 2)
        mcols[0].metric("Total", len(entries))
        for i, (src, cnt) in enumerate(counts.items(), 1):
            mcols[i].metric(src, cnt)
        mcols[-1].metric("Unique clients", len(clients_found))

        st.caption(
            "**Clients found:** " + ", ".join(f"`{c}`" for c in sorted(clients_found))
        )
        st.divider()

        # Filters
        fc1, fc2, fc3 = st.columns([1, 1, 2])
        with fc1:
            src_filter = st.selectbox("Filter by court", ["All"] + list(counts.keys()))
        with fc2:
            cl_filter = st.selectbox("Filter by client", ["All"] + sorted(clients_found))
        with fc3:
            sq = st.text_input("Search", placeholder="Case no., parties, counsel…")

        filtered = entries
        if src_filter != "All":
            filtered = [e for e in filtered if e.get("source") == src_filter]
        if cl_filter != "All":
            filtered = [e for e in filtered if e.get("client") == cl_filter]
        if sq:
            q = sq.upper()
            filtered = [e for e in filtered
                        if q in (e.get("case_no")  or "").upper()
                        or q in (e.get("parties")   or "").upper()
                        or q in (e.get("client")    or "").upper()
                        or q in (e.get("counsel")   or "").upper()]

        st.caption(f"Showing {len(filtered)} of {len(entries)} entries")

        rows = [{
            "Court":       e.get("source",""),
            "Client":      e.get("client",""),
            "Case No.":    e.get("case_no","") or "—",
            "Bench/Coram": e.get("court","")   or "—",
            "Parties":     (e.get("parties","") or "—")[:250],
            "Counsel":     (e.get("counsel","") or "—")[:150],
            "Date":        e.get("date",""),
        } for e in filtered]

        df = pd.DataFrame(rows)
        st.dataframe(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Court":       st.column_config.TextColumn(width="small"),
                "Client":      st.column_config.TextColumn(width="medium"),
                "Case No.":    st.column_config.TextColumn(width="medium"),
                "Bench/Coram": st.column_config.TextColumn(width="medium"),
                "Parties":     st.column_config.TextColumn(width="large"),
                "Counsel":     st.column_config.TextColumn(width="medium"),
                "Date":        st.column_config.TextColumn(width="small"),
            }
        )

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇ Download CSV",
            data=csv,
            file_name=f"court_results_{run_date_r.replace(' ','_').replace('/','_')}.csv",
            mime="text/csv",
        )

    # Auto-refresh while pending/running
    rr3, rr3_sha = gh_get("run_request.json")
    if rr3 and rr3.get("status") in ("pending", "running"):
        # Check if watcher has gone silent (requested_at more than 5 min ago and still pending)
        requested_at_str = rr3.get("requested_at", "")
        watcher_silent = False
        if rr3.get("status") == "pending" and requested_at_str:
            try:
                req_time = datetime.strptime(requested_at_str, "%d %b %Y %I:%M %p")
                watcher_silent = (datetime.now() - req_time).total_seconds() > 300
            except Exception:
                pass

        if watcher_silent:
            st.warning(
                "⚠️ **Watcher not responding** — the automation PC may be offline or "
                "watcher.py is not running. Please contact the administrator.\n\n"
                "You can reset the status below and try again later."
            )
            if st.button("🔄 Reset run status", key="results_reset"):
                gh_put("run_request.json", {"status": "idle"}, rr3_sha, "Reset stuck run")
                st.rerun()
        else:
            st.info("⏳ Run is in progress — auto-refreshing in 20 seconds…")
            time.sleep(20)
            st.rerun()
    elif rr3 and rr3.get("status") == "done":
        completed = rr3.get("completed_at", "")
        if completed:
            st.success(f"✅ Last run completed at {completed}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CLIENTS
# ══════════════════════════════════════════════════════════════════════════════
with tab_clients:

    # Save message
    if st.session_state.save_msg:
        kind, msg = st.session_state.save_msg
        if kind == "ok":
            st.success(f"✅ Saved to GitHub — {msg}")
        else:
            st.error(f"❌ Save failed: {msg}")
        st.session_state.save_msg = None

    # Summary
    nclt_count  = len(st.session_state.client_list)
    extra_count = len(st.session_state.extra_names)
    m1, m2, m3 = st.columns(3)
    m1.metric("NCLT clients",  nclt_count,  help="Have a specific NCLT bench assigned")
    m2.metric("Other clients", extra_count, help="Searched in NCLAT / SCI / BHC")
    m3.metric("Total",         nclt_count + extra_count)

    st.divider()
    st.subheader("Current client list")

    search_c = st.text_input("", placeholder="🔍 Search clients…",
                              label_visibility="collapsed")

    clients_all = all_clients()
    if search_c:
        clients_all = [c for c in clients_all
                       if search_c.upper() in c["name"]
                       or search_c.upper() in c.get("court","").upper()]

    if not clients_all:
        st.info("No clients match your search.")
    else:
        hdr = st.columns([0.4, 3.5, 2.5, 1.5, 0.6])
        hdr[0].markdown("**#**")
        hdr[1].markdown("**Client Name**")
        hdr[2].markdown("**Bench**")
        hdr[3].markdown("**Scope**")
        hdr[4].markdown("**Del**")
        st.markdown("<hr style='margin:4px 0 6px'>", unsafe_allow_html=True)

        for i, c in enumerate(clients_all, 1):
            bench = COURT_BENCH_MAP.get(c["court"], c["court"]) if c["court"] else "—"
            scope = "NCLT" if c["type"] == "nclt" else "NCLAT/SCI/BHC"
            row = st.columns([0.4, 3.5, 2.5, 1.5, 0.6])
            row[0].markdown(
                f"<span style='color:#9b9b99;font-size:12px'>{i}</span>",
                unsafe_allow_html=True)
            row[1].markdown(f"**{c['name']}**")
            row[2].markdown(f"`{bench}`" if c["court"] else "—")
            row[3].markdown(
                f"<span style='font-size:11px;background:#E6F1FB;color:#0C447C;"
                f"padding:2px 8px;border-radius:10px'>{scope}</span>"
                if c["type"] == "nclt" else
                f"<span style='font-size:11px;background:#f1f1f0;color:#6b6b69;"
                f"padding:2px 8px;border-radius:10px'>{scope}</span>",
                unsafe_allow_html=True)
            if row[4].button("✕", key=f"del_{i}_{c['name'][:8]}",
                             help=f"Remove {c['name']}"):
                if c["type"] == "nclt":
                    st.session_state.client_list = [
                        x for x in st.session_state.client_list
                        if x["name"] != c["name"]]
                else:
                    st.session_state.extra_names = [
                        x for x in st.session_state.extra_names
                        if x != c["name"]]
                save_clients_to_github()
                st.rerun()

    st.divider()
    st.subheader("Add new client")

    a1, a2, a3 = st.columns([3, 2.5, 1])
    with a1:
        new_name = st.text_input("Client name *",
                                  placeholder="e.g. ABC PRIVATE LIMITED")
    with a2:
        new_court = st.selectbox("NCLT bench",
            ["— None (NCLAT / SCI / BHC only) —",
             "MUMBAI BENCH COURT-I",   "MUMBAI BENCH COURT-II",
             "MUMBAI BENCH COURT-III", "MUMBAI BENCH COURT-IV",
             "MUMBAI BENCH COURT-V",   "MUMBAI BENCH COURT-VI",
             "BENGALURU BENCH COURT-I"])
    with a3:
        st.markdown("<br>", unsafe_allow_html=True)
        add_btn = st.button("➕ Add", type="primary", use_container_width=True)

    if add_btn:
        name_clean = new_name.strip().upper()
        if not name_clean:
            st.error("Please enter a client name.")
        elif any(c["name"] == name_clean for c in all_clients()):
            st.error(f'"{name_clean}" already exists.')
        else:
            court_val = "" if new_court.startswith("—") else new_court
            if court_val:
                st.session_state.client_list.append(
                    {"name": name_clean, "court": court_val})
            else:
                st.session_state.extra_names.append(name_clean)
            save_clients_to_github()
            st.rerun()

    st.divider()
    st.markdown("""
    <div style='background:#EBF3FC;border:1px solid #b8d4ef;border-radius:8px;
         padding:12px 16px;font-size:13px;color:#0C447C;line-height:1.8'>
    <strong>ℹ️ How sync works</strong><br>
    Every change (add / remove) is saved instantly to <code>clients.json</code>
    on GitHub.<br>
    <code>watcher.py</code> on your PC pulls the latest list before every run —
    so additions here are used automatically on the next run.<br>
    <strong>NCLT clients</strong> need a bench so the scraper knows which PDF to
    open. <strong>Other clients</strong> are searched in NCLAT, SCI and BHC.
    </div>
    """, unsafe_allow_html=True)
