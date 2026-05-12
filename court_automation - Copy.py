#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combined Court Cause List Automation (FIXED)
=============================================
Courts covered:
  • NCLT   — National Company Law Tribunal (Mumbai, Bengaluru)
  • NCLAT  — National Company Law Appellate Tribunal
  • SCI    — Supreme Court of India
  • BHC    — Bombay High Court
"""

import argparse, asyncio, re, smtplib, sys, io, json, logging, base64, requests
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import pdfplumber
from bs4 import BeautifulSoup

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# ==============================================================
# CLIENT LIST — loaded from clients.json
# ==============================================================

def _load_clients(json_path: str = None):
    if json_path is None:
        json_path = Path(__file__).parent / "clients.json"
    p = Path(json_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            client_list  = raw.get("client_list", [])
            extra_names  = raw.get("extra_names", [])
            client_names = [c["name"] for c in client_list] + extra_names
            print(f"[CONFIG] Loaded {len(client_list)} NCLT + {len(extra_names)} extra clients from {json_path}")
            return client_list, client_names
        except Exception as e:
            print(f"[WARN] Could not read {json_path}: {e} — using hardcoded defaults")

    print(f"[CONFIG] {json_path} not found — using hardcoded client list")
    client_list = [
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
    ]
    extra_names = [
        "DEEPAK BAGRA",
        "POOJA RAMESH SINGH",
        "JAMMU AND KASHMIR BANK",
        "J. C. FLOWERS ASSET RECONSTRUCTION PRIVATE LIMITED",
        "S.J.THANAWALLA and ORS",
    ]
    client_names = [c["name"] for c in client_list] + extra_names
    return client_list, client_names


CLIENT_LIST, CLIENT_NAMES = _load_clients("clients.json")


# ==============================================================
# CLI ARGUMENT PARSER
# ==============================================================

def _parse_cli():
    parser = argparse.ArgumentParser(description="Court Cause List Automation")
    parser.add_argument("--date",      type=str, default=None)
    parser.add_argument("--nclt",      action="store_true")
    parser.add_argument("--nclat",     action="store_true")
    parser.add_argument("--sci",       action="store_true")
    parser.add_argument("--bhc",       action="store_true")
    parser.add_argument("--all",       action="store_true", dest="all_courts")
    parser.add_argument("--no-prompt", action="store_true", dest="no_prompt")

    args, _ = parser.parse_known_args()

    if not any([args.date, args.nclt, args.nclat, args.sci, args.bhc,
                args.all_courts, args.no_prompt]):
        return None

    if args.all_courts:
        args.nclt = args.nclat = args.sci = args.bhc = True

    date_obj = None
    if args.date:
        d = args.date.strip().lower()
        if d == "today":
            date_obj = datetime.today()
        elif d == "yesterday":
            date_obj = datetime.today() - timedelta(days=1)
        else:
            try:
                date_obj = datetime.strptime(d, "%d-%m-%Y")
            except ValueError:
                print(f"[ERROR] Invalid date '{args.date}' — use DD-MM-YYYY, 'today', or 'yesterday'")
                sys.exit(1)

    return argparse.Namespace(
        date      = date_obj,
        run_nclt  = args.nclt,
        run_nclat = args.nclat,
        run_sci   = args.sci,
        run_bhc   = args.bhc,
        no_prompt = args.no_prompt,
    )


_CLI = _parse_cli()


# ==============================================================
# SHARED EMAIL CONFIG
# ==============================================================
CONFIG = {
    "client_names": [c["name"] for c in CLIENT_LIST],
    "email": {
        "enabled": True,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "sender_email": "vedika@apmh.in",
        "sender_password": "Vedrsat@2526#",
        "recipient_emails": ["tejashree@apmh.in"],
    },
    "log_file": "court_automation.log",
    "headless": True,  # FIX: Set True for server mode; False for debugging
}

SENDER_EMAIL    = CONFIG["email"]["sender_email"]
SENDER_PASSWORD = CONFIG["email"]["sender_password"]
MANAGER_EMAIL   = CONFIG["email"]["recipient_emails"][0]

NCLT_SEARCH_DATE = datetime.now()


# ── Logging ────────────────────────────────────────────────────────────────────
def setup_logging():
    handlers = []
    fh = logging.FileHandler(CONFIG["log_file"], encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handlers.append(fh)
    stream = (io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
              if sys.platform == "win32" else sys.stdout)
    sh = logging.StreamHandler(stream)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handlers.append(sh)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    return logging.getLogger(__name__)

log = setup_logging()


# ══════════════════════════════════════════════════════════════════════════════
#   SECTION 1 — NCLT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_ROMAN = {"I":1,"II":2,"III":3,"IV":4,"V":5,"VI":6,"VII":7,"VIII":8,"IX":9,"X":10}
_INT_TO_ROMAN = {v:k for k,v in _ROMAN.items()}

def _roman_to_int(s: str):
    return _ROMAN.get(s.upper())

def _int_to_roman(n: int):
    return _INT_TO_ROMAN.get(n)

def _court_number(text: str):
    m = re.search(r'(?:COURT[-\s]*)([IVXLC]+)\b', text.upper())
    if m:
        n = _roman_to_int(m.group(1))
        if n:
            return n
    m = re.search(r'(?:COURT[-\s]*)(\d+)', text.upper())
    if m:
        return int(m.group(1))
    return None

def _court_city(text: str):
    text = text.upper()
    for city in ("BENGALURU", "BANGALORE", "MUMBAI", "DELHI", "CHENNAI",
                  "KOLKATA", "AHMEDABAD", "HYDERABAD", "ALLAHABAD", "CHANDIGARH"):
        if city in text:
            return city
    return ""

def courts_match(client_court: str, pdf_court: str) -> bool:
    cc, pc = client_court.upper().strip(), pdf_court.upper().strip()
    cc_city = _court_city(cc).replace("BANGALORE", "BENGALURU")
    pc_city = _court_city(pc).replace("BANGALORE", "BENGALURU")
    if cc_city and pc_city and cc_city != pc_city:
        return False
    cc_num = _court_number(cc)
    pc_num = _court_number(pc)
    if cc_num is not None and pc_num is not None:
        return cc_num == pc_num
    return cc in pc or pc in cc


# ── CAPTCHA ────────────────────────────────────────────────────────────────────
def solve_math_captcha(text: str):
    for pat in [
        r"(\d+)\s*([\+\-\*x×])\s*(\d+)\s*=",
        r"Math question\s*\*?\s*(\d+)\s*([\+\-\*x×])\s*(\d+)",
        r"(\d+)\s*([\+\-\*x×])\s*(\d+)",
    ]:
        m = re.search(pat, text)
        if m:
            a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
            result = a + b if op in ("+",) else a - b if op == "-" else a * b
            log.info(f"CAPTCHA solved: {a} {op} {b} = {result}")
            return str(result)
    return None


# ── NCLT name matching helpers ─────────────────────────────────────────────────
_HONORIFICS = {'MR', 'MRS', 'MS', 'DR', 'SH', 'SMT', 'ADV', 'SHRI', 'KUM', 'MISS'}

def _make_name_words(name: str) -> list:
    parts = [w for w in re.split(r'[\s&\.\,]+', name.upper()) if len(w) >= 2]
    if parts and parts[0] in _HONORIFICS:
        parts = parts[1:]
    return parts

def _name_in_text(nwords: list, text: str, require_proximity: bool = True) -> bool:
    t = text.upper()
    if not nwords:
        return False
    if len(nwords) == 1:
        return nwords[0] in t
    if not require_proximity:
        return all(w in t for w in nwords)

    # Window = 2× the character length of the name itself + small buffer.
    # This prevents "KAMLESH CORPORATION" matching text where "Kamlesh"
    # and "Corporation" appear in different parties hundreds of chars apart.
    name_chars = sum(len(w) for w in nwords) + len(nwords) - 1
    win_size   = max(name_chars * 2 + 20, 60)

    def _scan_anchor(anchor, rest):
        idx = 0
        while True:
            pos = t.find(anchor, idx)
            if pos == -1:
                return False
            window = t[pos: pos + win_size]
            if all(w in window for w in rest):
                return True
            idx = pos + 1

    if _scan_anchor(nwords[0], nwords[1:]):
        return True
    if len(nwords) > 1 and _scan_anchor(nwords[-1], nwords[:-1]):
        return True
    return False

def _name_in_cell(nwords: list, cell_text: str) -> bool:
    """
    Check whether all name-words appear close together in a cell.
    Uses the same tight proximity window as _name_in_text so a single
    cell that contains two different party names (e.g. a merged row)
    does not produce false positives.
    """
    t = cell_text.upper()
    if not nwords:
        return False
    if not all(w in t for w in nwords):
        return False          # fast-fail: all words must be present at all
    if len(nwords) == 1:
        return True
    name_chars = sum(len(w) for w in nwords) + len(nwords) - 1
    win_size   = max(name_chars * 2 + 20, 60)
    anchor     = nwords[0]
    rest       = nwords[1:]
    idx = 0
    while True:
        pos = t.find(anchor, idx)
        if pos == -1:
            break
        window = t[pos: pos + win_size]
        if all(w in window for w in rest):
            return True
        idx = pos + 1
    # Also try anchoring on last word (handles "CORPORATION KAMLESH" order)
    anchor2 = nwords[-1]
    rest2   = nwords[:-1]
    idx = 0
    while True:
        pos = t.find(anchor2, idx)
        if pos == -1:
            break
        window = t[pos: pos + win_size]
        if all(w in window for w in rest2):
            return True
        idx = pos + 1
    return False

def _trim_to_entry(text: str) -> str:
    m = re.search(r'\s+\d{1,3}\.?\s+C\.?\s*P\.?\s*\(', text)
    if m:
        text = text[:m.start()].strip()
    return text


# ── NCLT PDF extraction (unchanged — works correctly) ──────────────────────────
def extract_cause_list_entries(pdf_bytes: bytes, client_names: list) -> list:
    entries = []
    name_words_map = {name: _make_name_words(name) for name in client_names}

    for name, words in name_words_map.items():
        log.info(f"  Match words for {name!r}: {words}")

    # ── Level 1: table extraction ──────────────────────────────────────────────
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            log.info(f"PDF has {len(pdf.pages)} page(s) — trying table extraction")
            for page_num, page in enumerate(pdf.pages, 1):
                table_settings = {
                    "vertical_strategy":   "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance":       6,
                    "join_tolerance":       4,
                    "edge_min_length":      25,
                    "min_words_vertical":   1,
                    "min_words_horizontal": 1,
                }
                tables = page.extract_tables(table_settings)
                log.info(f"  Page {page_num}: {len(tables)} table(s) found")

                for table in tables:
                    if not table:
                        continue

                    def _default_cols(ncols):
                        if ncols >= 7:
                            return 0, 1, 2, 3, 4, 5, 6
                        elif ncols == 6:
                            return 0, 1, 2, 3, -1, 4, 5
                        elif ncols == 5:
                            return 0, 1, 2, -1, -1, 3, 4
                        else:
                            return 0, 1, -1, -1, -1, 2, 3

                    first_data_row = next(
                        (r for r in table if r and len([c for c in r if c]) >= 4),
                        table[0] if table else []
                    )
                    ncols_default = len(first_data_row) if first_data_row else 7
                    col_sr, col_cp, col_ia, col_pur, col_sec, col_par, col_cou = \
                        _default_cols(ncols_default)

                    HDR_KW = {
                        'sr':  ['SR.', 'SR ', 'S.NO', 'SNO', 'SL'],
                        'cp':  ['CP NO', 'C.P', 'CASE NO', 'COMP'],
                        'ia':  ['IA', 'CA', 'IA/CA', 'INTERLOCUTORY'],
                        'pur': ['PURPOSE', 'NATURE', 'HEARING'],
                        'sec': ['SECTION', 'U/S', 'U/SEC', 'PROVISION'],
                        'par': ['PARTIES', 'PARTY', 'NAME OF', 'PETITIONER', 'APPLICANT'],
                        'cou': ['COUNSEL', 'ADVOCATE', 'ADV.', 'AOR'],
                    }

                    for row_idx, row in enumerate(table):
                        cells = [re.sub(r'\s+', ' ', (c or '')).strip() for c in row]
                        row_text = ' '.join(cells).upper()

                        is_header = (
                            ('PARTIES' in row_text or 'PARTY' in row_text or 'NAME OF' in row_text)
                            and ('CP' in row_text or 'C.P' in row_text or 'CASE' in row_text)
                        )
                        if is_header:
                            idx_map = {}
                            for ci, cell in enumerate(cells):
                                cu = cell.upper()
                                for key, kws in HDR_KW.items():
                                    if key not in idx_map and any(cu.startswith(k) or k in cu for k in kws):
                                        idx_map[key] = ci
                            if len(idx_map) >= 3:
                                col_sr  = idx_map.get('sr',  col_sr)
                                col_cp  = idx_map.get('cp',  col_cp)
                                col_ia  = idx_map.get('ia',  col_ia)
                                col_pur = idx_map.get('pur', col_pur)
                                col_sec = idx_map.get('sec', col_sec)
                                col_par = idx_map.get('par', col_par)
                                col_cou = idx_map.get('cou', col_cou)
                            continue

                        if len(cells) < 4:
                            continue

                        parties_cell = ""
                        for offset in (0, -1, 1):
                            idx = col_par + offset
                            if 0 <= idx < len(cells):
                                parties_cell += " " + cells[idx]

                        for name, nwords in name_words_map.items():
                            matched = _name_in_cell(nwords, parties_cell)
                            if not matched:
                                matched = _name_in_cell(nwords, row_text)
                            if not matched:
                                continue

                            log.info(f"  [TABLE] MATCH '{name}' row {row_idx}")

                            def safe(idx):
                                return cells[idx] if 0 <= idx < len(cells) else ''

                            sr_no   = safe(col_sr)
                            cp_no   = safe(col_cp)
                            ia_ca   = safe(col_ia) if col_ia >= 0 else ''
                            purpose = safe(col_pur) if col_pur >= 0 else ''
                            section = safe(col_sec) if col_sec >= 0 else ''
                            parties = safe(col_par)
                            counsel = safe(col_cou) if col_cou >= 0 else ''

                            if not section:
                                secs = re.findall(
                                    r'U\s*/?[Ss]\.?\s*\d+[\w\(\)]*|[Ss]ec\.?\s*\d+[\w\(\)]*',
                                    ia_ca + " " + purpose, re.IGNORECASE)
                                section = ' | '.join(dict.fromkeys(secs))

                            entries.append({
                                'client_name': name,
                                'sr_no': sr_no, 'cp_no': cp_no, 'ia_ca_no': ia_ca,
                                'purpose': purpose, 'section': section,
                                'parties': parties, 'counsel': counsel,
                                'raw_block': row_text[:400],
                            })

    except Exception as exc:
        log.error(f"Table extraction error: {exc}")
        import traceback; log.error(traceback.format_exc())

    if entries:
        log.info(f"Level 1 found {len(entries)} match(es)")
        return entries

    # ── Level 2: word/X-position slicing ──────────────────────────────────────
    log.warning("Table extraction found nothing — trying word/X-position method")

    FIXED_COLS = [
        ('sr_no',   0.00, 0.05),
        ('cp_no',   0.05, 0.20),
        ('ia_ca',   0.20, 0.42),
        ('purpose', 0.42, 0.52),
        ('section', 0.52, 0.58),
        ('parties', 0.58, 0.80),
        ('counsel', 0.80, 1.00),
    ]

    def col_text_w(words, x0, x1):
        return ' '.join(
            w['text'] for w in words
            if x0 <= w['x0'] <= x1
        ).strip()

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                pw, ph = float(page.width), float(page.height)

                h_ys = set()
                for rect in page.rects:
                    if rect['x1'] - rect['x0'] > pw * 0.15:
                        h_ys.add(round(rect['y0'], 1))
                        h_ys.add(round(rect['y1'], 1))
                for line in page.lines:
                    if abs(line['y0'] - line['y1']) < 2 and line['x1'] - line['x0'] > pw * 0.15:
                        h_ys.add(round(line['y0'], 1))
                h_ys = sorted(h_ys)

                if len(h_ys) < 3:
                    continue

                learned = None
                for i in range(min(8, len(h_ys) - 1)):
                    y0, y1 = h_ys[i], h_ys[i + 1]
                    if y1 - y0 < 4:
                        continue
                    hw = page.crop((0, y0, pw, y1)).extract_words(x_tolerance=3, y_tolerance=3)
                    ht = ' '.join(w['text'] for w in hw).upper()
                    if not (('PARTIES' in ht or 'NAME OF' in ht) and ('CP' in ht or 'PURPOSE' in ht)):
                        continue

                    kw_cols = {
                        'sr_no':   ['SR', 'S.NO', 'SL'],
                        'cp_no':   ['CP', 'C.P', 'COMP'],
                        'ia_ca':   ['IA', 'IA/CA', 'INTERLOCUTORY'],
                        'purpose': ['PURPOSE', 'NATURE'],
                        'section': ['SECTION', 'U/S'],
                        'parties': ['PARTIES', 'NAME', 'PETITIONER'],
                        'counsel': ['COUNSEL', 'ADV', 'ADVOCATE'],
                    }
                    centers = {}
                    for w in hw:
                        wu = w['text'].upper()
                        for col, kws in kw_cols.items():
                            if col not in centers and any(wu.startswith(k) or k in wu for k in kws):
                                centers[col] = (w['x0'] + w['x1']) / 2

                    if len(centers) >= 5:
                        ordered = sorted(centers.items(), key=lambda x: x[1])
                        learned = []
                        for j, (col, cx) in enumerate(ordered):
                            x0 = (cx + ordered[j - 1][1]) / 2 if j > 0 else 0
                            x1 = (ordered[j + 1][1] + cx) / 2 if j < len(ordered) - 1 else pw
                            learned.append((col, x0, x1))
                    break

                col_layout = learned if learned else [(c, pw * a, pw * b) for c, a, b in FIXED_COLS]

                HEADER_ONLY_PHRASES = {
                    'NAME OF PARTIES', 'NAME OF PARTY',
                    'SR.NO', 'SR. NO',
                    'CP NO.', 'C.P. NO',
                    'IA/CA NO',
                    'CAUSE LIST',
                    'NATIONAL COMPANY LAW',
                }

                for i in range(len(h_ys) - 1):
                    y0, y1 = h_ys[i], h_ys[i + 1]
                    if y1 - y0 < 4:
                        continue
                    rw = page.crop((0, y0, pw, y1)).extract_words(x_tolerance=3, y_tolerance=3)
                    if not rw:
                        continue
                    rt = ' '.join(w['text'] for w in rw).upper()

                    if any(ph in rt for ph in HEADER_ONLY_PHRASES):
                        continue

                    row_data = {col: col_text_w(rw, x0, x1) for col, x0, x1 in col_layout}
                    parties = row_data.get('parties', '')

                    if not parties:
                        parties = col_text_w(rw, pw * 0.53, pw * 0.84)

                    for name, nwords in name_words_map.items():
                        matched = _name_in_cell(nwords, parties)
                        if not matched:
                            matched = _name_in_cell(nwords, rt)
                        if not matched:
                            continue

                        log.info(f"  [WORD] MATCH '{name}' at y={y0:.0f}–{y1:.0f}")
                        p = re.sub(r'^\d+\.?\s*', '', parties).strip()
                        p = _trim_to_entry(p)
                        ia_ca = row_data.get('ia_ca', '')
                        section = row_data.get('section', '')
                        if not section:
                            secs = re.findall(
                                r'U\s*/?[Ss]\.?\s*\d+[\w\(\)]*|[Ss]ec\.?\s*\d+[\w\(\)]*',
                                ia_ca, re.IGNORECASE)
                            section = ' | '.join(dict.fromkeys(secs))

                        entries.append({
                            'client_name': name,
                            'sr_no':    row_data.get('sr_no', ''),
                            'cp_no':    row_data.get('cp_no', ''),
                            'ia_ca_no': ia_ca,
                            'purpose':  row_data.get('purpose', ''),
                            'section':  section,
                            'parties':  p,
                            'counsel':  row_data.get('counsel', ''),
                            'raw_block': rt[:400],
                        })

    except Exception as exc:
        log.error(f"Word method error: {exc}")
        import traceback; log.error(traceback.format_exc())

    if entries:
        log.info(f"Level 2 found {len(entries)} match(es)")
        return entries

    # ── Level 3: plain-text fallback ──────────────────────────────────────────
    log.warning("Word method found nothing — falling back to plain-text line scan")
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = [p.extract_text() or '' for p in pdf.pages]

        for name, nwords in name_words_map.items():
            for page_text in pages_text:
                if not _name_in_text(nwords, page_text, require_proximity=False):
                    continue
                lines = page_text.splitlines()
                matched, capturing, capture_budget = [], False, 0
                for line in lines:
                    if _name_in_text(nwords, line, require_proximity=False):
                        capturing = True
                        capture_budget = 0
                    if capturing:
                        if matched and re.match(r'\s*\d{1,3}\.?\s+C\.?\s*P\.?\s*\(', line, re.I):
                            break
                        matched.append(line.strip())
                        capture_budget += 1
                        if capture_budget > 8:
                            break
                if matched:
                    ctx = _trim_to_entry(' '.join(matched).strip())
                    entries.append({
                        'client_name': name,
                        'sr_no': '', 'cp_no': '', 'ia_ca_no': '',
                        'purpose': '', 'section': '',
                        'parties': ctx, 'counsel': '',
                        'raw_block': ctx[:400],
                    })
                    break

    except Exception as e2:
        log.error(f"Plain-text fallback error: {e2}")

    log.info(f"Level 3 found {len(entries)} match(es)")
    return entries


# ── NCLT email helpers ─────────────────────────────────────────────────────────
def _td(val, bold=False, center=False, color=""):
    s = "padding:9px 11px;border:1px solid #ddd;vertical-align:top;font-size:12px;"
    if bold:   s += "font-weight:bold;"
    if center: s += "text-align:center;"
    if color:  s += f"color:{color};"
    return f"<td style='{s}'>{(val or '-').replace(chr(10), '<br>')}</td>"

_HS = ("padding:10px 12px;background:#2c3e50;color:white;text-align:left;"
       "font-size:12px;border:1px solid #1a252f;")


def nclt_send_combined_email(all_entries, not_found_clients, search_date):
    cfg = CONFIG["email"]
    if not cfg["enabled"]:
        log.info("Email disabled."); return

    found_names_str = ", ".join(dict.fromkeys(e["client_name"] for e in all_entries))
    subject = f"[NCLT Alert] {found_names_str} | {search_date.strftime('%d/%m/%Y')}"

    headers = ["Sr.No", "CP No.", "IA / CA No.", "Purpose",
               "Section", "Name of Parties", "Counsel", "Court", "Date"]
    headers_html = "".join(f"<th style='{_HS}'>{h}</th>" for h in headers)

    by_court = defaultdict(list)
    for e in all_entries:
        by_court[e.get("_pdf_court", "Unknown")].append(e)

    rows_html = ""
    for court, court_entries in by_court.items():
        rows_html += (f"<tr><td colspan='9' style='background:#eaf2ff;padding:8px 12px;"
                      f"font-weight:bold;font-size:12px;color:#1a5276;border:1px solid #c9dff5'>"
                      f"📋 {court}</td></tr>")
        for e in court_entries:
            rows_html += (
                "<tr>"
                + _td(e.get("sr_no") or "-", center=True)
                + _td(e.get("cp_no", ""), bold=True, color="#1a5276")
                + _td(e.get("ia_ca_no", ""))
                + _td(e.get("purpose", ""))
                + _td(e.get("section", ""))
                + _td(e.get("parties", "") or e.get("raw_block", "")[:200], bold=True, color="#c0392b")
                + _td(e.get("counsel", ""))
                + _td(e.get("_pdf_court", ""))
                + _td(e.get("_pdf_date", ""))
                + "</tr>"
            )

    not_found_html = ""
    if not_found_clients:
        nf_rows = "".join(
            f"<tr><td style='padding:8px 12px;border:1px solid #f5c6cb;font-size:12px;"
            f"color:#721c24'>{n}</td>"
            f"<td style='padding:8px 12px;border:1px solid #f5c6cb;font-size:12px;"
            f"text-align:center'><span style='background:#f8d7da;color:#721c24;"
            f"padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold'>"
            f"NOT FOUND</span></td></tr>"
            for n in not_found_clients
        )
        not_found_html = f"""
  <div style='padding:0 28px 22px'>
    <h3 style='margin:0 0 10px;color:#c0392b;font-size:14px'>Clients NOT Found</h3>
    <table style='border-collapse:collapse;width:100%'>
      <tr style='background:#721c24;color:white'>
        <th style='padding:10px 12px;text-align:left;font-size:12px'>Client Name</th>
        <th style='padding:10px 12px;text-align:center;font-size:12px'>Status</th>
      </tr>{nf_rows}
    </table>
  </div>"""

    body = f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;margin:0;padding:20px;background:#f4f6f8'>
<div style='max-width:1100px;margin:auto;background:white;border-radius:8px;
     box-shadow:0 2px 8px rgba(0,0,0,0.12);overflow:hidden'>
  <div style='background:#1a5276;color:white;padding:22px 28px'>
    <div style='font-size:11px;opacity:0.75;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px'>NCLT Cause List Monitor</div>
    <h2 style='margin:0;font-size:20px'>Daily Summary — Clients Found</h2>
    <p style='margin:6px 0 0;font-size:13px;opacity:0.85'>
      {len(all_entries)} match(es) across {len(by_court)} court(s) &nbsp;|&nbsp;
      Date: {search_date.strftime('%d %b %Y')}
    </p>
  </div>
  <div style='background:#eaf2ff;padding:12px 28px;border-bottom:1px solid #c9dff5;font-size:13px'>
    <b>Clients Found:</b>
    <span style='color:#c0392b;font-weight:bold'>&nbsp;{found_names_str}</span>
  </div>
  <div style='padding:22px 28px;overflow-x:auto'>
    <h3 style='margin:0 0 14px;color:#1a5276;font-size:14px'>Cause List Entries</h3>
    <table style='border-collapse:collapse;width:100%;font-size:12px;min-width:950px'>
      <thead><tr>{headers_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
  {not_found_html}
  <div style='background:#f8f9fa;border-top:1px solid #eee;padding:12px 28px;font-size:11px;color:#888'>
    Generated: {datetime.now().strftime('%d %b %Y %I:%M %p')} &nbsp;|&nbsp;
    Source: <a href='https://nclt.gov.in/all-couse-list' style='color:#3498db'>nclt.gov.in</a>
  </div>
</div></body></html>"""

    _send_raw(subject, body, cfg)


def nclt_send_no_match_email(today, search_date):
    cfg = CONFIG["email"]
    if not cfg["enabled"]:
        return

    subject = f"[NCLT Daily Report] No matches found | {search_date.strftime('%d %b %Y')}"
    client_rows = "".join(
        f"<tr><td style='padding:8px 12px;border:1px solid #ddd;font-size:12px'>{c['name']}</td>"
        f"<td style='padding:8px 12px;border:1px solid #ddd;font-size:12px;color:#666'>{c['court']}</td></tr>"
        for c in CLIENT_LIST
    )
    body = f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;margin:0;padding:20px;background:#f4f6f8'>
<div style='max-width:800px;margin:auto;background:white;border-radius:8px;
     box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden'>
  <div style='background:#1a5276;color:white;padding:22px 28px'>
    <div style='font-size:11px;opacity:0.75;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px'>NCLT Cause List Monitor</div>
    <h2 style='margin:0;font-size:20px'>Daily Report — No Matches Found</h2>
    <p style='margin:8px 0 0;font-size:13px;opacity:0.85'>Automated daily check completed</p>
  </div>
  <div style='background:#eafaf1;padding:14px 28px;border-bottom:1px solid #a9dfbf;font-size:13px'>
    <span style='color:#117a65;font-weight:bold'>All Clear</span> &nbsp;|&nbsp;
    <b>Date Checked:</b> {search_date.strftime('%d %b %Y')} &nbsp;|&nbsp;
    <b>Clients Monitored:</b> {len(CLIENT_LIST)}
  </div>
  <div style='padding:22px 28px'>
    <table style='border-collapse:collapse;width:100%'>
      <tr style='background:#2c3e50;color:white'>
        <th style='padding:10px 12px;text-align:left;font-size:12px'>Client Name</th>
        <th style='padding:10px 12px;text-align:left;font-size:12px'>Court</th>
      </tr>{client_rows}
    </table>
  </div>
  <div style='background:#f8f9fa;border-top:1px solid #eee;padding:12px 28px;font-size:11px;color:#888'>
    Generated: {today.strftime('%d %b %Y %I:%M %p')} &nbsp;|&nbsp;
    Source: <a href='https://nclt.gov.in/all-couse-list' style='color:#3498db'>nclt.gov.in</a>
  </div>
</div></body></html>"""
    _send_raw(subject, body, cfg)


def _send_raw(subject, body, cfg):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["sender_email"]
        msg["To"]      = ", ".join(cfg["recipient_emails"])
        msg.attach(MIMEText(body, "html", "utf-8"))
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as srv:  # FIX: 60s timeout
            srv.ehlo(); srv.starttls(); srv.ehlo()
            srv.login(cfg["sender_email"], cfg["sender_password"])
            srv.sendmail(cfg["sender_email"], cfg["recipient_emails"], msg.as_string())
        log.info(f"Email sent → {cfg['recipient_emails']}")
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"SMTP auth failed — check credentials: {e}")
        # FIX: Save HTML locally when email fails
        fname = f"nclt_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(body)
        log.info(f"Report saved locally as {fname}")
    except Exception as e:
        log.error(f"Email error: {e}")
        fname = f"nclt_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(body)
        log.info(f"Report saved locally as {fname}")


# ── NCLT calendar selector (FIXED) ────────────────────────────────────────────
async def select_date_from_calendar(page, field_id, target_date):
    log.info(f"Selecting {target_date.strftime('%d/%m/%Y')} in #{field_id}")
    await page.click(f"#{field_id}")
    await page.wait_for_timeout(1000)  # FIX: longer wait for calendar to render

    month_map = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    }

    for _ in range(36):  # FIX: increased from 24 for wider date range
        try:
            header = await page.inner_text(".ui-datepicker-title")
        except Exception:
            try:
                header = await page.evaluate(
                    "() => { const c=document.querySelector('.ui-datepicker-title'); return c?c.innerText:''; }")
            except Exception:
                header = ""

        log.info(f"Calendar header: '{header}'")
        cal_month, cal_year = None, None
        for mname, mnum in month_map.items():
            if mname in header.lower():
                cal_month = mnum; break
        ym = re.search(r"\d{4}", header)
        if ym:
            cal_year = int(ym.group())

        if cal_month == target_date.month and cal_year == target_date.year:
            break

        # FIX: Determine direction — go forward or backward
        if cal_year is not None and cal_month is not None:
            current_ym = cal_year * 12 + cal_month
            target_ym  = target_date.year * 12 + target_date.month
            go_prev = target_ym < current_ym
        else:
            go_prev = True  # default: go backward

        nav_sel = ".ui-datepicker-prev" if go_prev else ".ui-datepicker-next"
        alt_sels = [
            f"[data-handler='{'prev' if go_prev else 'next'}']",
            f"a.{'prev' if go_prev else 'next'}",
        ]

        clicked = False
        for sel in [nav_sel] + alt_sels:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await page.wait_for_timeout(500)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            log.warning("Could not navigate calendar — falling back to JS")
            break

    # Click the target day
    day_clicked = False
    try:
        for link in await page.query_selector_all("table.ui-datepicker-calendar td a"):
            if (await link.inner_text()).strip() == str(target_date.day):
                await link.click()
                log.info(f"Clicked day {target_date.day}")
                day_clicked = True
                break
    except Exception as e:
        log.warning(f"Day click error: {e}")

    if not day_clicked:
        # FIX: Use MM/DD/YYYY format which is what NCLT's datepicker expects
        ds = target_date.strftime("%m/%d/%Y")
        await page.evaluate(
            f"var el=document.getElementById('{field_id}');"
            f"if(el){{el.value='{ds}';el.dispatchEvent(new Event('change',{{bubbles:true}}));}}")
        await page.keyboard.press("Escape")
        log.info(f"Date set via JS: {ds}")

    await page.wait_for_timeout(500)


# ── NCLT main automation (FIXED) ───────────────────────────────────────────────
async def run_nclt_automation(search_date: datetime = None):
    if not PLAYWRIGHT_AVAILABLE:
        log.error("NCLT requires playwright. Install: pip install playwright && playwright install chromium")
        return []

    today       = datetime.now()
    search_date = search_date or NCLT_SEARCH_DATE

    log.info("=" * 60)
    log.info("NCLT Automation starting")
    log.info(f"Search date: {search_date.strftime('%d/%m/%Y')}")
    log.info(f"Clients: {CONFIG['client_names']}")
    log.info("=" * 60)

    all_matches = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=CONFIG["headless"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"))
        page = await ctx.new_page()

        # FIX: Add retry for initial page load
        for attempt in range(3):
            try:
                log.info(f"Opening NCLT cause list page (attempt {attempt+1})…")
                await page.goto("https://nclt.gov.in/all-couse-list",
                                wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                log.warning(f"Page load attempt {attempt+1} failed: {e}")
                if attempt == 2:
                    log.error("NCLT page failed to load after 3 attempts")
                    await browser.close()
                    return []
                await page.wait_for_timeout(3000)

        await page.wait_for_timeout(3000)  # FIX: longer wait for CAPTCHA to render

        await select_date_from_calendar(page, "edit-field-cause-date-value",   search_date)
        await select_date_from_calendar(page, "edit-field-cause-date-value-1", search_date)

        # FIX: Wait for CAPTCHA to be present, then solve
        log.info("Solving CAPTCHA …")
        await page.wait_for_timeout(1000)
        page_text = await page.inner_text("body")
        answer = solve_math_captcha(page_text)
        if answer:
            # FIX: Clear field first, then fill
            captcha_field = await page.query_selector("#edit-captcha-response")
            if captcha_field:
                await captcha_field.fill("")
                await captcha_field.fill(answer)
                log.info(f"CAPTCHA answer: {answer}")
            else:
                log.warning("CAPTCHA input field not found!")
        else:
            log.warning("Could not find/solve CAPTCHA!")

        await page.wait_for_timeout(500)
        log.info("Clicking Search …")
        for sel in ["#edit-submit-all-couse-list", "input[type='submit']", "button[type='submit']"]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click(timeout=5000)
                    log.info(f"Clicked search via {sel}")
                    break
            except Exception:
                continue

        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(4000)  # FIX: longer wait for results
        log.info(f"Results URL: {page.url}")

        # FIX: Check if CAPTCHA failed (page shows error or no results)
        body_text = await page.inner_text("body")
        if "not valid" in body_text.lower() or "captcha" in body_text.lower()[:500]:
            log.error("CAPTCHA validation failed — retrying once")
            # Retry CAPTCHA
            await page.wait_for_timeout(1000)
            page_text2 = await page.inner_text("body")
            answer2 = solve_math_captcha(page_text2)
            if answer2:
                captcha_field2 = await page.query_selector("#edit-captcha-response")
                if captcha_field2:
                    await captcha_field2.fill("")
                    await captcha_field2.fill(answer2)
                for sel in ["#edit-submit-all-couse-list", "input[type='submit']"]:
                    try:
                        await page.click(sel, timeout=5000); break
                    except Exception:
                        continue
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(4000)

        # ── Collect PDF links (ALL paginated pages) ───────────────────
        # FIX: More flexible city matching
        cities_needed = set()
        for c in CLIENT_LIST:
            city = _court_city(c["court"]).lower()
            if city:
                cities_needed.add(city)
                # Add alternate spellings
                if city == "bengaluru":
                    cities_needed.add("bangalore")
                elif city == "mumbai":
                    cities_needed.add("bombay")
        log.info(f"Cities we need PDFs for: {cities_needed}")

        async def _scrape_pdf_links_from_current_page():
            found = []
            try:
                rows = await page.query_selector_all("table tr")
                log.info(f"  Result rows on this page: {len(rows)}")
                for row in rows:
                    row_text_lower = (await row.inner_text()).lower()

                    # FIX: Also accept rows if no city filter matches (some rows
                    # may have different formatting)
                    city_match = any(city in row_text_lower for city in cities_needed if city)
                    if not city_match and cities_needed:
                        # Check if it's a bench row at all
                        if "bench" not in row_text_lower:
                            continue

                    for anchor in await row.query_selector_all("a"):
                        href = (await anchor.get_attribute("href") or "").strip()
                        if not href.lower().endswith(".pdf"):
                            continue
                        if not href.startswith("http"):
                            href = "https://nclt.gov.in" + ("" if href.startswith("/") else "/") + href
                        link_text = (await anchor.inner_text()).strip()
                        cells = await row.query_selector_all("td")
                        court_name = "Unknown"
                        cause_date = today.strftime("%d/%m/%Y")
                        if len(cells) >= 2:
                            court_name = (await cells[1].inner_text()).strip()
                        if len(cells) >= 6:
                            cause_date = (await cells[5].inner_text()).strip()
                        elif len(cells) >= 5:
                            cause_date = (await cells[4].inner_text()).strip()

                        # FIX: Verify the PDF date matches our search date
                        date_ok = True
                        for date_text in re.findall(r'\d{2}[./]\d{2}[./]\d{4}', cause_date):
                            try:
                                pdf_dt = datetime.strptime(date_text.replace("/", "."), "%d.%m.%Y")
                                if pdf_dt.date() != search_date.date():
                                    date_ok = False
                            except ValueError:
                                pass
                        if not date_ok:
                            log.info(f"    Skipping PDF (wrong date): {link_text} — {cause_date}")
                            continue

                        found.append({
                            "url": href,
                            "name": link_text or Path(href).name,
                            "court": court_name,
                            "date": cause_date,
                        })
                        log.info(f"    PDF: {link_text} | {court_name} | {cause_date}")
            except Exception as e:
                log.error(f"Table scan error: {e}")
            return found

        pdf_links = []

        log.info("Scraping results page 1 …")
        pdf_links.extend(await _scrape_pdf_links_from_current_page())

        pagination_page = 1
        while True:
            next_btn = None
            for sel in [
                "li.pager__item--next a",
                "li.pager-next a",
                "a[title='Go to next page']",
                ".pager .next a",
                "ul.pager li:last-child a",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        next_btn = el
                        break
                except Exception:
                    continue

            if not next_btn:
                log.info(f"No more pagination pages found after page {pagination_page}.")
                break

            pagination_page += 1
            log.info(f"Navigating to results page {pagination_page} …")
            try:
                await next_btn.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(3000)
                pdf_links.extend(await _scrape_pdf_links_from_current_page())
            except Exception as e:
                log.error(f"Pagination error on page {pagination_page}: {e}")
                break

        log.info(f"Total PDFs found across {pagination_page} result page(s): {len(pdf_links)}")
        if not pdf_links:
            log.warning("No PDFs found!")
            log.warning("Dumping result table rows for debugging:")
            try:
                rows = await page.query_selector_all("table tr")
                for i, row in enumerate(rows[:20]):
                    txt = (await row.inner_text()).replace("\n", " | ").strip()[:200]
                    log.warning(f"  Row {i}: {txt}")
            except Exception as de:
                log.warning(f"  Could not dump table: {de}")
            # FIX: Take screenshot for debugging
            try:
                await page.screenshot(path="nclt_no_results.png")
                log.info("Debug screenshot saved: nclt_no_results.png")
            except Exception:
                pass

        # ── Process each PDF ──────────────────────────────────────────
        for pdf_info in pdf_links:
            log.info(f"\nProcessing: {pdf_info['name']} ({pdf_info['court']})")
            pdf_court_upper = pdf_info["court"].upper().strip()

            relevant_clients = [
                c["name"] for c in CLIENT_LIST
                if courts_match(c["court"], pdf_court_upper)
            ]
            relevant_clients += [c["name"] for c in CLIENT_LIST if not c["court"].strip()]

            if not relevant_clients:
                log.info(f"  No clients mapped to '{pdf_info['court']}' — skipping")
                continue

            log.info(f"  Checking {len(relevant_clients)} client(s): {relevant_clients}")

            try:
                resp = await ctx.request.get(pdf_info["url"], timeout=30000)
                if not resp.ok:
                    log.warning(f"  HTTP {resp.status}"); continue
                pdf_bytes = await resp.body()
                log.info(f"  Fetched {len(pdf_bytes) // 1024} KB")
            except Exception as e:
                log.error(f"  Fetch error: {e}"); continue

            # FIX: Verify it's actually a PDF
            if b"%PDF" not in pdf_bytes[:16]:
                log.warning(f"  Not a valid PDF (starts with: {pdf_bytes[:20]})")
                continue

            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as _pdf:
                    _p = _pdf.pages[0]
                    _words = _p.extract_words(x_tolerance=3, y_tolerance=3)
                    _hlines = [l for l in _p.lines
                               if abs(l['y0']-l['y1']) < 2 and l['x1']-l['x0'] > _p.width*0.3]
                    _vlines = [l for l in _p.lines
                               if abs(l['x0']-l['x1']) < 2 and l['y1']-l['y0'] > _p.height*0.05]
                    log.info(f"  Page size: {_p.width:.0f}×{_p.height:.0f}  "
                             f"Words:{len(_words)}  H-lines:{len(_hlines)}  V-lines:{len(_vlines)}")
            except Exception as _e:
                log.warning(f"  Debug dump failed: {_e}")

            entries = extract_cause_list_entries(pdf_bytes, relevant_clients)

            found_names = set(e["client_name"] for e in entries)
            not_found_here = [n for n in relevant_clients if n not in found_names]
            if not_found_here:
                log.info(f"  NOT FOUND in {pdf_info['name']}: {not_found_here}")

            if entries:
                log.info(f"  *** {len(entries)} MATCH(ES) ***")
                for e in entries:
                    e["_pdf_name"]  = pdf_info["name"]
                    e["_pdf_court"] = pdf_info["court"]
                    e["_pdf_date"]  = pdf_info["date"]
                all_matches.extend(entries)
            else:
                log.info(f"  No matches in: {pdf_info['name']}")

        await browser.close()

    log.info("=" * 60)
    found_names   = set(e["client_name"] for e in all_matches)
    final_not_found = [c["name"] for c in CLIENT_LIST if c["name"] not in found_names]
    log.info(f"FOUND:     {list(found_names)}")
    log.info(f"NOT FOUND: {final_not_found}")

    if all_matches:
        log.info(f"DONE — {len(all_matches)} match(es). Sending combined alert email …")
        nclt_send_combined_email(all_matches, final_not_found, search_date)
    else:
        log.info("DONE — No matches. Sending no-match summary email …")
        nclt_send_no_match_email(today, search_date)

    log.info("=" * 60)
    return all_matches


# ══════════════════════════════════════════════════════════════════════════════
#   SECTION 2 — NCLAT / SCI / BHC HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_SALUTATIONS = re.compile(r'\b(MR|MRS|MS|DR)\b\.?\s*', re.IGNORECASE)
_NON_WORD    = re.compile(r'[^\w\s&]')
_SPACES      = re.compile(r'\s+')
_GENERIC     = {"LTD", "PVT", "LLP", "AND", "THE", "OF", "&", "MR", "MRS", "MS", "DR", "P"}
_BHC_SEP     = re.compile(
    r'V\s*/\s*S|Vs?\s*\.|VERSUS|PETITIONER|RESPONDENT|APPELLANT|APPLICANT',
    re.IGNORECASE
)


def _clean(name: str) -> str:
    name = _SALUTATIONS.sub(' ', name)
    name = _NON_WORD.sub(' ', name)
    return _SPACES.sub(' ', name).strip().upper()


def _core_tokens(name: str) -> list:
    tokens = [t for t in _clean(name).split() if len(t) > 1 and t not in _GENERIC]
    return tokens or _clean(name).split()


def _is_person(name: str) -> bool:
    return bool(re.match(r'\s*(MR|MRS|MS|DR)\b', name.strip(), re.IGNORECASE))


def _wb(token: str, text: str) -> bool:
    return bool(re.search(r'\b' + re.escape(token) + r'\b', text))


def _wb_positions(token: str, text: str) -> list:
    return [m.start() for m in re.finditer(r'\b' + re.escape(token) + r'\b', text)]


def name_matches(client_name: str, text: str, bhc_mode: bool = False) -> bool:
    if bhc_mode:
        text = _BHC_SEP.sub(' ', text)
        text = re.sub(r'[/\\|]', ' ', text)
    ut = text.upper()

    cleaned = _clean(client_name)
    if cleaned and cleaned in ut:
        return True
    raw_phrase = _SPACES.sub(' ', _NON_WORD.sub(' ', client_name.upper().strip())).strip()
    if raw_phrase in ut:
        return True

    tokens = _core_tokens(client_name)

    if _is_person(client_name):
        if len(tokens) < 2:
            return False
        first, surname, middle = tokens[0], tokens[-1], tokens[1:-1]
        # For 2-token names (e.g. "BHARAT MEHTA") use a tight window —
        # both words are common standalone, so they must be close together.
        # For 3+ token names the full sequence is distinctive enough to
        # allow a wider window across line breaks.
        nl = sum(len(t) for t in tokens) + len(tokens) - 1
        if len(tokens) == 2:
            window_size = max(nl * 2 + 15, 50)   # very tight for 2-token
        elif bhc_mode:
            window_size = max(nl * 3 + 30, 80)
        else:
            window_size = max(nl * 3 + 20, 70)
        if not _wb(first, ut) or not _wb(surname, ut):
            return False
        for pos in _wb_positions(surname, ut):
            win = ut[max(0, pos - window_size): pos + window_size]
            if not _wb(first, win):
                continue
            if all(_wb(m, win) for m in middle):
                return True
            if middle and all(
                _wb(m[0], win) or bool(re.search(r'\b' + m[0] + r'\.', win))
                for m in middle
            ):
                return True
        return False

    if len(tokens) < 2:
        return False
    if not all(_wb(t, ut) for t in tokens):
        return False

    anchor  = tokens[0]
    rest    = tokens[1:]

    # ── Tight proximity window ────────────────────────────────────────
    # Window = 2× the character length of the name itself.
    # This prevents "KAMLESH CORPORATION" matching a merged NCLAT row
    # where "Kamlesh Patel" and "Life Insurance Corporation" are 200+
    # chars apart.  BHC mode gets 3× because party + counsel columns
    # are sometimes merged in that text extraction.
    name_len = sum(len(t) for t in tokens) + len(tokens) - 1
    if bhc_mode:
        win_size = max(name_len * 3, 120)   # generous for BHC merged cols
    else:
        win_size = max(name_len * 2 + 20, 60)   # tight for NCLAT / SCI

    for pos in _wb_positions(anchor, ut):
        win = ut[max(0, pos - 10): pos + win_size]
        if all(_wb(t, win) for t in rest):
            return True
    return False


# ── Deduplication ──────────────────────────────────────────────────────────────
_CASE_RE = re.compile(r'\b\d{2,6}/\d{4}\b')


def _row_key(client: str, row_text: str) -> tuple:
    nums = frozenset(_CASE_RE.findall(row_text))
    if nums:
        return (client, nums)
    return (client, re.sub(r'\s+', ' ', row_text).strip().upper()[:100])


def deduplicate(results: list) -> list:
    seen, deduped = set(), []
    for r in results:
        unique_rows, unique_entries = [], []
        for row in r.get("table_rows", []):
            k = _row_key(r["client"], " ".join(str(c) for c in row))
            if k not in seen:
                seen.add(k); unique_rows.append(row)
        for block in r.get("entries", []):
            k = _row_key(r["client"], " ".join(block))
            if k not in seen:
                seen.add(k); unique_entries.append(block)
        if unique_rows or unique_entries:
            rc = dict(r)
            rc["table_rows"] = unique_rows
            rc["entries"]    = unique_entries
            deduped.append(rc)
    return deduped


# ── NCLAT (FIXED date matching) ───────────────────────────────────────────────
def get_nclat_pdfs(date_str: str, session) -> list:
    dt       = datetime.strptime(date_str, "%d-%m-%Y")
    dot_date = dt.strftime("%d.%m.%Y")
    slash_date = dt.strftime("%d/%m/%Y")  # FIX: also check slash format
    log.info("NCLAT: fetching for %s", dot_date)
    pdfs, seen_urls = [], set()

    for page_num in range(21):
        params = {"title": "", "field_court_name_target_id": "All",
                  "field_final_date_value": "", "field_final_date_value_1": "",
                  "page": page_num}
        try:
            resp = session.get("https://nclat.nic.in/daily-cause-list",
                               params=params, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            log.warning("NCLAT page %d failed: %s", page_num+1, e); break

        soup   = BeautifulSoup(resp.text, "html.parser")
        oldest = None

        for row in soup.find_all("tr"):
            cells    = row.find_all("td")
            if len(cells) < 3: continue
            row_text = " ".join(td.get_text(strip=True) for td in cells)
            for dm in re.findall(r'\d{2}[./]\d{2}[./]\d{4}', row_text):
                try:
                    d2 = datetime.strptime(dm.replace("/", "."), "%d.%m.%Y")
                    if oldest is None or d2 < oldest: oldest = d2
                except ValueError: pass

            # FIX: Check both dot and slash date formats
            if dot_date not in row_text and slash_date not in row_text:
                continue

            link = row.find("a", href=lambda h: h and ".pdf" in h.lower())
            if not link: continue
            href = link["href"]
            if not href.startswith("http"): href = "https://nclat.nic.in" + href
            if href in seen_urls: continue
            seen_urls.add(href)
            court = "NCLAT"
            for name in ["Chairperson Court","Court-II","Court-III","Court-IV",
                         "Registrar Court","Chennai Bench"]:
                if name.lower() in row_text.lower(): court = name; break
            desc = cells[2].get_text(strip=True) if len(cells) > 2 else "Cause List"
            pdfs.append({"court": court, "description": desc, "url": href, "source": "NCLAT"})
            log.info("  ✓ NCLAT: %s", desc)

        if oldest and oldest < dt - timedelta(days=1): break
        if not (soup.find("a", title="Go to next page") or
                soup.find("li", class_="pager__item--next") or
                any(f"page={page_num+1}" in (a.get("href") or "")
                    for a in soup.find_all("a", href=True))):
            break

    log.info("NCLAT: %d PDF(s) found", len(pdfs))
    return pdfs


# ── SCI ────────────────────────────────────────────────────────────────────────
SCI_PAGE_URL = "https://www.sci.gov.in/cause-list/"
SCI_API_BASE = "https://api.sci.gov.in/jonew/cl"


def get_sci_pdfs(date_str: str, session) -> list:
    dt           = datetime.strptime(date_str, "%d-%m-%Y")
    date_display = dt.strftime("%d-%m-%Y")
    api_date     = dt.strftime("%Y-%m-%d")
    log.info("SCI: fetching for %s", date_display)
    pdfs, seen_urls = [], set()

    try:
        resp = session.get(SCI_PAGE_URL, timeout=30); resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = (soup.find("table", class_="causelist-glance") or
                 soup.select_one(".cause_list_future_published table"))
        if table:
            for tr in table.find_all("tr"):
                cells = tr.find_all("td")
                if not cells: continue
                if date_display not in " ".join(td.get_text(strip=True) for td in cells): continue
                suppl_idx = 3 if len(cells) >= 15 else 2
                for col_idx, desc in [(suppl_idx, "SCI Misc Supplementary"),
                                      (suppl_idx-1, "SCI Misc Main")]:
                    if not (0 <= col_idx < len(cells)): continue
                    a = cells[col_idx].find("a", href=True)
                    if not a or ".pdf" not in a["href"].lower(): continue
                    href = a["href"]
                    if not href.startswith("http"): href = "https://api.sci.gov.in" + href
                    if href in seen_urls: continue
                    seen_urls.add(href)
                    pdfs.append({"court": "Supreme Court of India",
                                 "description": f"{desc} — {date_display}",
                                 "url": href, "source": "SCI"})
                    log.info("  ✓ SCI: %s", desc)
    except Exception as e:
        log.warning("SCI scrape failed: %s", e)

    if not pdfs:
        for url, desc in [
            (f"{SCI_API_BASE}/{api_date}/M_J_2.pdf", "SCI Misc Supplementary"),
            (f"{SCI_API_BASE}/{api_date}/M_J_3.pdf", "SCI Misc Supplementary (Batch 2)"),
            (f"{SCI_API_BASE}/{api_date}/M_J_1.pdf", "SCI Misc Main"),
            (f"{SCI_API_BASE}/{api_date}/M_R_1.pdf", "SCI Registrar Main"),
            (f"{SCI_API_BASE}/advance/{api_date}/M_J.pdf", "SCI Misc Advance"),
        ]:
            try:
                r = session.head(url, timeout=10, allow_redirects=True)
                if r.status_code == 200 and url not in seen_urls:
                    seen_urls.add(url)
                    pdfs.append({"court": "Supreme Court of India",
                                 "description": f"{desc} — {date_display}",
                                 "url": url, "source": "SCI"})
                    log.info("  ✓ SCI (direct): %s", desc)
            except Exception:
                pass

    log.info("SCI: %d PDF(s) found", len(pdfs))
    return pdfs


# ── BHC (FIXED date format) ───────────────────────────────────────────────────
BHC_CAUSELIST_URL = "https://bombayhighcourt.gov.in/bhc/causelistFinal"
BHC_BASE_URL      = "https://bombayhighcourt.gov.in"


async def _bhc_async(date_str: str) -> list:
    """date_str is DD-MM-YYYY, but BHC datepicker needs DD/MM/YYYY"""
    pdf_entries = []

    # FIX: Convert DD-MM-YYYY to DD/MM/YYYY for BHC datepicker
    bhc_date = date_str.replace("-", "/")
    log.info("BHC: using date format: %s", bhc_date)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()

        try:
            await page.goto(BHC_CAUSELIST_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            log.error("BHC: causelistFinal load failed: %s", e)
            await browser.close()
            return []
        await page.wait_for_timeout(3_000)
        log.info("BHC: causelistFinal loaded — %s", page.url)

        filled = False

        # FIX: Use the correct slash-separated date format
        result = await page.evaluate(f"""
            () => {{
                try {{
                    const inp = document.querySelector('#demo1');
                    if (!inp) return 'no_input';
                    jQuery(inp).datepicker('setDate', '{bhc_date}');
                    ['input','change','blur'].forEach(ev =>
                        inp.dispatchEvent(new Event(ev, {{bubbles:true}})));
                    return inp.value;
                }} catch(e) {{ return 'error:' + e.message; }}
            }}
        """)
        log.info("BHC: jQuery setDate result: %s", result)
        if result and bhc_date in str(result):
            filled = True
            log.info("BHC: date set via jQuery datepicker")

        if not filled:
            try:
                loc = page.locator("#demo1").first
                await loc.click()
                await page.wait_for_timeout(200)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.keyboard.type(bhc_date, delay=60)  # FIX: use slash format
                await page.wait_for_timeout(400)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)
                val = await loc.input_value()
                log.info("BHC: keyboard type → value=%s", val)
                if bhc_date in val:
                    filled = True
            except Exception as e:
                log.warning("BHC: keyboard type failed: %s", e)

        if not filled:
            await page.evaluate(f"""
                () => {{
                    const inp = document.querySelector('#demo1') ||
                                document.querySelector('[name="m_causedt"]');
                    if (inp) {{
                        inp.value = '{bhc_date}';
                        if (typeof jQuery !== 'undefined') {{
                            jQuery(inp).trigger('change').trigger('blur');
                        }}
                        ['input','change','blur'].forEach(ev =>
                            inp.dispatchEvent(new Event(ev, {{bubbles:true}})));
                    }}
                }}
            """)
            await page.wait_for_timeout(300)
            log.warning("BHC: used last-resort JS fill")

        search_clicked = False
        for sel in [
            "button.sendbtn",
            "button.sendbtn.w-100",
            "form button[type='submit']",
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click()
                    search_clicked = True
                    log.info("BHC: search clicked via '%s'", sel)
                    break
            except Exception:
                continue

        if not search_clicked:
            clicked = await page.evaluate("""
                () => {
                    const btn = document.querySelector('button.sendbtn') ||
                                document.querySelector('form button[type="submit"]');
                    if (btn) { btn.click(); return true; }
                    return false;
                }
            """)
            if clicked:
                log.info("BHC: search clicked via JS")
                search_clicked = True

        if not search_clicked:
            await page.keyboard.press("Enter")
            log.warning("BHC: search via Enter (fallback)")

        log.info("BHC: waiting for AJAX table to load...")
        table_appeared = False
        for attempt in range(30):
            await page.wait_for_timeout(1_000)
            row_count_check = await page.evaluate(
                "() => document.querySelectorAll('table tbody tr').length"
            )
            still_loading = await page.evaluate("""
                () => {
                    const t = document.body.innerText;
                    return t.includes('Searching') || t.includes('Loading...');
                }
            """)
            log.info("BHC poll %d/30 — rows=%d loading=%s",
                     attempt+1, row_count_check, still_loading)
            if row_count_check > 0:
                table_appeared = True
                log.info("BHC: table loaded after %ds", attempt+1)
                break
            if not still_loading and attempt >= 5:
                break

        if not table_appeared:
            await page.wait_for_timeout(3_000)

        table_meta = await page.evaluate("""
            () => {
                const tables = document.querySelectorAll('table');
                if (!tables.length) return { rowCount: 0, entireColIdx: -1, headers: [] };
                const t = tables[0];
                let entireColIdx = -1;
                const headerRow = t.querySelector('thead tr, tr:first-child');
                const headers = [];
                if (headerRow) {
                    Array.from(headerRow.querySelectorAll('th, td')).forEach((c, i) => {
                        const txt = (c.innerText || '').trim().toLowerCase();
                        headers.push(txt);
                        if (txt.includes('entire')) entireColIdx = i;
                    });
                }
                if (entireColIdx < 0) {
                    Array.from(t.querySelectorAll('tr')).forEach(row => {
                        Array.from(row.querySelectorAll('th,td')).forEach((c,i) => {
                            if ((c.innerText||'').toLowerCase().includes('entire')) entireColIdx = i;
                        });
                    });
                }
                const bodyRows = t.querySelectorAll('tbody tr');
                return {
                    rowCount: bodyRows.length,
                    entireColIdx,
                    headers,
                    firstRowHtml: bodyRows.length > 0 ? bodyRows[0].outerHTML.slice(0,500) : ''
                };
            }
        """)

        row_count      = table_meta.get("rowCount", 0)
        entire_col_idx = table_meta.get("entireColIdx", -1)
        log.info("BHC: table — %d rows, Entire Causelist col = %d, headers = %s",
                 row_count, entire_col_idx, table_meta.get("headers", []))

        if row_count == 0:
            page_text = await page.evaluate("() => document.body.innerText.slice(0,600)")
            log.error("BHC: 0 rows — page text: %s", page_text)
            try:
                await page.screenshot(path="bhc_no_rows.png")
                log.error("BHC: screenshot saved as bhc_no_rows.png")
            except Exception:
                pass
            await browser.close()
            return []

        all_form_data = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('table tbody tr');
                const results = [];
                rows.forEach((row, rowIdx) => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (!cells.length) return;
                    const coram = cells[0].innerText.trim();
                    const lastCell = cells[cells.length - 1];
                    const form = lastCell.querySelector('form');
                    if (!form) return;
                    const fields = {};
                    Array.from(form.querySelectorAll('input')).forEach(inp => {
                        if (inp.name) fields[inp.name] = inp.value;
                    });
                    const allInputs = Array.from(form.querySelectorAll('input[name]'));
                    const multiFields = {};
                    allInputs.forEach(inp => {
                        if (!multiFields[inp.name]) multiFields[inp.name] = [];
                        multiFields[inp.name].push(inp.value);
                    });
                    results.push({ rowIdx, coram, action: form.action, fields, multiFields });
                });
                return results;
            }
        """)
        log.info("BHC: %d rows to download", len(all_form_data))

        for form_info in all_form_data:
            coram   = form_info.get("coram", "")
            action  = form_info.get("action", "")
            mfields = form_info.get("multiFields", {})
            row_idx = form_info.get("rowIdx", 0)

            if not coram or not action:
                continue
            if re.search(r'\b(officer|registrar|admin|staff|duty|superintendent)\b',
                         coram, re.IGNORECASE):
                continue

            log.info("  BHC row %d/%d: %s", row_idx+1, len(all_form_data), coram[:55])

            body_parts = []
            for key, vals in mfields.items():
                for v in vals:
                    body_parts.append(f"{key}={v}")
            body_str = "&".join(body_parts)

            try:
                pdf_b64 = await page.evaluate(f"""
                    async () => {{
                        const resp = await fetch('{action}', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'X-Requested-With': 'XMLHttpRequest',
                                'Accept': 'application/pdf,*/*',
                            }},
                            body: {repr(body_str)},
                            credentials: 'same-origin',
                        }});
                        if (!resp.ok) return 'ERROR:' + resp.status;
                        const ct = resp.headers.get('content-type') || '';
                        if (!ct.includes('pdf') && resp.status !== 200) return 'BAD_CT:' + ct;
                        const buf = await resp.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let bin = '';
                        bytes.forEach(b => bin += String.fromCharCode(b));
                        return btoa(bin);
                    }}
                """)

                if isinstance(pdf_b64, str) and pdf_b64.startswith("ERROR:"):
                    log.warning("  BHC row %d fetch error: %s", row_idx+1, pdf_b64)
                    continue
                if isinstance(pdf_b64, str) and pdf_b64.startswith("BAD_CT:"):
                    log.warning("  BHC row %d bad content-type: %s", row_idx+1, pdf_b64)
                    continue

                pdf_bytes = base64.b64decode(pdf_b64)

                if b"%PDF" not in pdf_bytes[:16]:
                    log.warning("  BHC row %d not a PDF (%d bytes)", row_idx+1, len(pdf_bytes))
                    continue

                log.info("  ✓ BHC row %d: %d bytes — %s", row_idx+1, len(pdf_bytes), coram[:40])

                pdf_entries.append({
                    "court":       coram,
                    "description": f"BHC Entire Causelist — {coram}",
                    "url":         action,
                    "source":      "BHC",
                    "pdf_bytes":   pdf_bytes,
                })

            except Exception as e:
                log.warning("  BHC row %d Playwright fetch failed: %s", row_idx+1, e)
                continue

        log.info("BHC: %d PDFs downloaded via Playwright", len(pdf_entries))
        await browser.close()

    log.info("BHC: %d unique PDF(s) collected", len(pdf_entries))
    return pdf_entries


def get_bhc_pdfs(date_str: str, session=None) -> list:
    if not PLAYWRIGHT_AVAILABLE:
        log.error("BHC: install playwright → pip install playwright && playwright install chromium")
        return []
    try:
        # FIX: Simplified async handling — no nest_asyncio needed
        return asyncio.run(_bhc_async(date_str))
    except RuntimeError as e:
        # Already in an async loop (shouldn't happen in CLI mode)
        if "already running" in str(e).lower():
            log.warning("BHC: nested event loop detected — trying thread pool")
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _bhc_async(date_str)).result(timeout=600)
            except Exception as e2:
                log.error("BHC thread pool failed: %s", e2)
                return []
        raise
    except Exception as e:
        log.error("BHC scraper failed: %s", e)
        import traceback; log.error(traceback.format_exc())
        return []


# ── PDF download ───────────────────────────────────────────────────────────────
def _download(entry: dict, session) -> bytes | None:
    if entry.get("source") == "BHC":
        pdf_bytes = entry.get("pdf_bytes")
        if pdf_bytes:
            return pdf_bytes
        log.warning("  BHC entry has no pre-fetched bytes")
        return None

    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
    # FIX: Add retry logic
    for attempt in range(3):
        try:
            r = session.get(entry["url"], headers=ua, timeout=60)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            if "html" in ct.lower() and b"%PDF" not in r.content[:512]:
                log.warning("  Skipping — got HTML not PDF: %s", entry["url"])
                return None
            return r.content
        except Exception as e:
            if attempt < 2:
                log.warning("  Download attempt %d failed: %s — retrying", attempt+1, e)
                import time; time.sleep(2)
            else:
                log.warning("  Download failed after 3 attempts: %s", entry["url"])
                return None


# ── PDF text extraction ────────────────────────────────────────────────────────
def _clean_cell(v) -> str:
    return re.sub(r'\s+', ' ', str(v)).strip() if v else ""


def _extract_table_rows(pdf_bytes: bytes) -> list:
    raw = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pg in pdf.pages:
                for tbl in pg.extract_tables():
                    for row in tbl:
                        cleaned = [_clean_cell(c) for c in row]
                        if any(cleaned): raw.append(cleaned)
    except Exception as e:
        log.warning("  Table extract error: %s", e)
        return []
    if not raw: return []

    width = Counter(len(r) for r in raw).most_common(1)[0][0]
    log.info("  Canonical table width: %d cols, %d raw rows", width, len(raw))

    def _norm(row):
        row = [_clean_cell(c) for c in row]; n = len(row)
        if n == 0: return ["","","",""]
        if n == 1: return [row[0],"","",""]
        if n == 2: return [row[0],row[1],"",""]
        if n == 3: return [row[0],row[1],row[2],""]
        if n == 4: return row[:4]
        rest = [c for c in row[2:] if c]; mid = max(1, len(rest)//2)
        return [row[0], row[1], " | ".join(rest[:mid]), " | ".join(rest[mid:])]

    SR_PAT = re.compile(r'^\d')
    merged, current = [], None
    for row in raw:
        first = (row[0] if row else "").strip()
        if first and SR_PAT.match(first):
            if current: merged.append(current)
            current = _norm(row)
        elif current:
            extra = _norm(row)
            for i in range(4):
                if extra[i]: current[i] = (current[i] + " " + extra[i]).strip()
    if current: merged.append(current)
    log.info("  %d logical rows after merge", len(merged))
    return merged


def _extract_text_lines(pdf_bytes: bytes) -> list:
    lines = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pg in pdf.pages:
                t = pg.extract_text()
                if t: lines.extend(t.splitlines())
    except Exception as e:
        log.warning("  Text extract error: %s", e)

    if not lines and OCR_AVAILABLE:
        try:
            for img in convert_from_bytes(pdf_bytes, dpi=200):
                t = pytesseract.image_to_string(img, lang="eng")
                if t: lines.extend(t.splitlines())
        except Exception as e:
            log.warning("  OCR failed: %s", e)
    return lines


# ── BHC item splitter ──────────────────────────────────────────────────────────
_BHC_ITEM_LINE = re.compile(r'^\s*\d{1,4}\s+[A-Z]{1,6}[/(]\d+/\d{4}\b', re.IGNORECASE)
_BHC_SKIP = re.compile(
    r'^\s*('
    r'BOMBAY\s+HIGHCOURT|HIGH\s+COURT\s+OF\s+BOMBAY'
    r'|DAILY\s+(MAIN|SUPPLEMENTARY|CHAMBER)'
    r'|WEEKLY\s+MAIN|PRODUCTION\s+CAUSELIST'
    r'|FOR\s+(WEDNESDAY|THURSDAY|MONDAY|TUESDAY|FRIDAY|SATURDAY|SUNDAY|THE\s+PERIOD)'
    r'|COURT\s+NO\.|HON\'?BLE|HEADER\s+NOTE'
    r'|Sr\.\s+CASE\s+NO|={3,}|====\s*SR\.NO\.'
    r'|AT\s+\d|\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}'
    r'|FOR\s+(FINAL\s+HEARING|FRAMING|CIRCULATION|MARKING|DISMISSAL)'
    r'|DUE\s+MATTERS|INTERIM\s+APPLICATION|SUPREME\s+COURT\s+EXPEDITED'
    r')',
    re.IGNORECASE
)


def _split_bhc_items(lines: list) -> list:
    items, current = [], []
    for raw in lines:
        line = raw.strip()
        if not line or _BHC_SKIP.match(line):
            continue
        if _BHC_ITEM_LINE.match(line):
            if current: items.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current: items.append(current)
    return items


def _split_item_into_subcases(item_lines: list) -> list:
    SUBCASE_HDR = re.compile(r'^\s*[A-Z]{1,8}[/(]\d+/\d{4}\b', re.IGNORECASE)
    WITH_LINE   = re.compile(r'^\s*WITH\s*$', re.IGNORECASE)

    sub_blocks, current = [], []
    for line in item_lines:
        stripped = line.strip()
        if WITH_LINE.match(stripped):
            if current: sub_blocks.append(current)
            current = []
        elif SUBCASE_HDR.match(stripped) and sub_blocks:
            if current: sub_blocks.append(current)
            current = [stripped]
        else:
            current.append(stripped)
    if current: sub_blocks.append(current)
    return sub_blocks if sub_blocks else [item_lines]


def _search_bhc_text(client: str, lines: list) -> list:
    matched = []
    for item_lines in _split_bhc_items(lines):
        for sub in _split_item_into_subcases(item_lines):
            sub_text = "\n".join(sub)
            if name_matches(client, sub_text, bhc_mode=True):
                matched.append([l for l in sub if l.strip()][:25])
    return matched


# ── SCI text search ────────────────────────────────────────────────────────────
_ITEM_START_SCI = re.compile(
    r'^\s*\d{1,4}\s*[.)]\s+'
    r'|^\s*\d{1,4}\s+(?:C\.A\.|W\.P\.|S\.L\.P\.|T\.P\.|I\.A\.|M\.A\.|Crl\.'
    r'|Civil|Criminal|Diary|Connected|Writ|Appeal|Petition|Arb\.|Cont\.|O\.A\.|R\.P\.|Comp\.)',
    re.IGNORECASE
)


def _search_sci_text(client: str, lines: list) -> list:
    blocks, cur = [], []
    for line in lines:
        if _ITEM_START_SCI.match(line):
            if cur: blocks.append(cur)
            cur = [line.strip()]
        elif cur:
            cur.append(line.strip())
    if cur: blocks.append(cur)

    matched = []
    for block in blocks:
        if name_matches(client, "\n".join(block)):
            matched.append([l for l in block if l.strip()][:20])
    if matched: return matched
    # Fallback: scan individual lines — use name_matches so the tight
    # proximity window still applies (prevents "KAMLESH" on one token
    # and "CORPORATION" elsewhere on the same very long line).
    tokens = _core_tokens(client)
    return [[l.strip()] for l in lines
            if all(t in l.upper() for t in tokens)
            and name_matches(client, l)]


# ── Search a single PDF entry ──────────────────────────────────────────────────
def search_pdf(entry: dict, session) -> list:
    log.info("Scanning: %s", entry["description"])
    data = _download(entry, session)
    if not data: return []

    source = entry.get("source", "")
    is_bhc = (source == "BHC")
    results = []

    if source == "NCLAT":
        rows = _extract_table_rows(data)
        log.info("  %d table rows", len(rows))
        if rows:
            for client in CLIENT_NAMES:
                matched = []
                for r in rows:
                    # ── Match against each cell individually, not the whole
                    # joined row. Joining all columns into one long string
                    # caused "KAMLESH CORPORATION" to match a row where
                    # "Kamlesh Patel" was in col 2 and "Corporation of India"
                    # was in col 3 — 200+ chars apart in the joined string.
                    # We check the parties column (col index 2, the widest)
                    # and the full joined string only as a tighter fallback.
                    parties_cell = r[2] if len(r) > 2 else ""
                    case_cell    = r[1] if len(r) > 1 else ""
                    # Primary: match against parties column only
                    hit = name_matches(client, parties_cell)
                    # Secondary: match against case+parties concatenated
                    # (short enough to stay within the proximity window)
                    if not hit:
                        hit = name_matches(client, (case_cell + " " + parties_cell).strip())
                    if hit:
                        matched.append(r)
                if matched:
                    seen_k, unique = set(), []
                    for row in matched:
                        k = _row_key(client, " ".join(row))
                        if k not in seen_k: seen_k.add(k); unique.append(row)
                    results.append({"client": client, "court": entry["court"],
                                    "description": entry["description"], "url": entry["url"],
                                    "source": source, "table_rows": unique, "entries": []})
                    log.info("  MATCH [NCLAT] %s — %d row(s)", client, len(unique))
            return results
        log.warning("  NCLAT: no table rows — text fallback")

    lines = _extract_text_lines(data)
    log.info("  %d text lines", len(lines))
    if not lines:
        log.warning("  No text in %s", entry["description"])
        return results

    for client in CLIENT_NAMES:
        blocks = _search_bhc_text(client, lines) if is_bhc else _search_sci_text(client, lines)
        if not blocks: continue
        seen_k, unique = set(), []
        for blk in blocks:
            k = _row_key(client, " ".join(blk))
            if k not in seen_k: seen_k.add(k); unique.append(blk)
        results.append({"client": client, "court": entry["court"],
                         "description": entry["description"], "url": entry["url"],
                         "source": source, "table_rows": [], "entries": unique})
        log.info("  MATCH [%s] %s — %d block(s)", source, client, len(unique))

    return results


# ── NCLAT/SCI/BHC email ────────────────────────────────────────────────────────
SOURCE_COLOURS = {"NCLAT": "#003366", "SCI": "#5c0099", "BHC": "#8B6914"}
_TD = 'style="vertical-align:top;padding:6px 10px;border:1px solid #ccc;font-size:13px"'
_TH = 'style="padding:8px 10px;background:#1a3a5c;color:#fff;font-size:13px;text-align:left"'


def _esc(s: str) -> str:
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")\
                 .replace("\n","<br>").strip()


def build_email(results: list, date_display: str, courts: list) -> str:
    label = " &amp; ".join(courts)
    if not results:
        return (f"<html><body style='font-family:Arial,sans-serif'><p>Dear Manager,</p>"
                f"<p>No client entries found in <b>{label}</b> cause lists for "
                f"<b>{date_display}</b>.</p></body></html>")

    rows_html = ""
    for r in results:
        colour = SOURCE_COLOURS.get(r["source"], "#555")
        badge  = (f'<span style="background:{colour};color:#fff;padding:2px 7px;'
                  f'border-radius:3px;font-size:11px">{r["source"]}</span>')
        if r["table_rows"]:
            for i, row in enumerate(r["table_rows"]):
                row4 = (list(row) + ["","","",""])[:4]
                if i == 0:
                    rows_html += (f"<tr><td {_TD}>{badge}</td><td {_TD}>{_esc(r['client'])}</td>"
                                  f"<td {_TD}>{_esc(r['court'])}</td>"
                                  f"<td {_TD}>{_esc(row4[0])}</td><td {_TD}>{_esc(row4[1])}</td>"
                                  f"<td {_TD}>{_esc(row4[2])}</td><td {_TD}>{_esc(row4[3])}</td></tr>\n")
                else:
                    rows_html += (f"<tr><td {_TD} colspan='3'></td>"
                                  f"<td {_TD}>{_esc(row4[0])}</td><td {_TD}>{_esc(row4[1])}</td>"
                                  f"<td {_TD}>{_esc(row4[2])}</td><td {_TD}>{_esc(row4[3])}</td></tr>\n")
        else:
            entry_html = "".join(
                f'<div style="margin-bottom:6px">{"<br>".join(_esc(ln) for ln in blk if ln)}</div>'
                for blk in r["entries"]
            )
            rows_html += (f"<tr><td {_TD}>{badge}</td><td {_TD}>{_esc(r['client'])}</td>"
                          f"<td {_TD}>{_esc(r['court'])}</td>"
                          f"<td {_TD} colspan='4'>{entry_html or '(see PDF)'}</td></tr>\n")

    return f"""<html>
<body style="font-family:Arial,sans-serif;max-width:1200px">
<p>Dear Manager,</p>
<p><b>{len(results)}</b> client match(es) in <b>{label}</b> cause lists for <b>{date_display}</b>.</p>
<table border="1" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%">
  <tr>
    <th {_TH}>Court</th><th {_TH}>Client Name</th><th {_TH}>Bench / Coram</th>
    <th {_TH}>Sr. No.</th><th {_TH}>Case No. / Type</th>
    <th {_TH}>Parties</th><th {_TH}>Counsel / Remarks</th>
  </tr>
{rows_html}
</table>
</body></html>"""


def send_email(subject: str, body: str, date_str: str):
    cfg = CONFIG["email"]
    recipients = cfg["recipient_emails"]
    msg = MIMEMultipart()
    msg["From"] = cfg["sender_email"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))
    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(cfg["sender_email"], cfg["sender_password"])
            s.sendmail(cfg["sender_email"], recipients, msg.as_string())
        log.info("✓ Email sent to %s", recipients)
    except Exception as e:
        log.error("Email failed: %s", e)
        fname = f"report_{date_str}.html"
        with open(fname, "w", encoding="utf-8") as f: f.write(body)
        log.info("Saved locally as %s", fname)


# ══════════════════════════════════════════════════════════════════════════════
#   SECTION 3 — COMBINED MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*60)
    print("  COMBINED COURT CAUSE LIST AUTOMATION")
    print(f"  Clients loaded: {len(CLIENT_LIST)} NCLT + "
          f"{len(CLIENT_NAMES) - len(CLIENT_LIST)} extra = {len(CLIENT_NAMES)} total")
    print("="*60)
    if not OCR_AVAILABLE:
        print("\n  ⚠  pytesseract/pdf2image not installed — scanned PDFs skipped")
    if not PLAYWRIGHT_AVAILABLE:
        print("\n  ⚠  playwright not installed — NCLT and BHC unavailable")
        print("     Fix: pip install playwright && playwright install chromium")

    if _CLI and _CLI.no_prompt:
        if not _CLI.date:
            print("\n  [ERROR] --no-prompt requires --date")
            sys.exit(1)
        if not any([_CLI.run_nclt, _CLI.run_nclat, _CLI.run_sci, _CLI.run_bhc]):
            print("\n  [ERROR] --no-prompt requires at least one court flag")
            sys.exit(1)

        d            = _CLI.date
        run_nclt     = _CLI.run_nclt
        run_nclat    = _CLI.run_nclat
        run_sci      = _CLI.run_sci
        run_bhc      = _CLI.run_bhc
        date_str     = d.strftime("%d-%m-%Y")
        date_display = d.strftime("%d %B %Y")
        nclt_date    = d
        weekday      = d.strftime("%A")

        print(f"\n  [CLI] Date  : {date_display} ({weekday})")
        print(f"  [CLI] Courts: " +
              ", ".join(c for c, f in [("NCLT", run_nclt), ("NCLAT", run_nclat),
                                        ("SCI", run_sci), ("BHC", run_bhc)] if f))
        if d.weekday() >= 5:
            print(f"\n  ⚠  {weekday} — courts may not be sitting.")

    else:
        print("\n  Select date:")
        print("  [1] Today")
        print("  [2] Yesterday")
        print("  [3] Enter custom date (DD-MM-YYYY)")

        if _CLI and _CLI.date:
            print(f"\n  (Pre-filled from --date: {_CLI.date.strftime('%d-%m-%Y')})")

        while True:
            dc = input("\n  Your choice (1/2/3): ").strip()
            if dc == "" and _CLI and _CLI.date:
                d = _CLI.date; break
            if dc == "1":   d = datetime.today(); break
            elif dc == "2": d = datetime.today() - timedelta(days=1); break
            elif dc == "3":
                while True:
                    raw = input("  Enter date (DD-MM-YYYY): ").strip()
                    try:    d = datetime.strptime(raw, "%d-%m-%Y"); break
                    except: print("  ✗ Invalid — use DD-MM-YYYY")
                break
            else: print("  ✗ Enter 1, 2 or 3.")

        date_str     = d.strftime("%d-%m-%Y")
        date_display = d.strftime("%d %B %Y")
        nclt_date    = d
        weekday      = d.strftime("%A")
        print(f"\n  ✓ Date selected: {date_display} ({weekday})")
        if d.weekday() >= 5:
            print(f"  ⚠  {weekday} — courts may not be sitting.")

        print("\n  Select court(s):")
        print("  [1] NCLT only")
        print("  [2] NCLAT only")
        print("  [3] Supreme Court of India only")
        print("  [4] Bombay High Court only")
        print("  [5] NCLT + NCLAT")
        print("  [6] NCLAT + SCI + BHC")
        print("  [7] All courts")
        while True:
            cc = input("\n  Your choice (1-7): ").strip()
            if cc in ("1","2","3","4","5","6","7"): break
            print("  ✗ Enter a number from 1 to 7.")

        run_nclt  = cc in ("1","5","7")
        run_nclat = cc in ("2","5","6","7")
        run_sci   = cc in ("3","6","7")
        run_bhc   = cc in ("4","6","7")

    if run_bhc and not PLAYWRIGHT_AVAILABLE:
        print("\n  ✗ BHC needs playwright.")
        run_bhc = False
    if run_nclt and not PLAYWRIGHT_AVAILABLE:
        print("\n  ✗ NCLT needs playwright.")
        run_nclt = False

    if run_nclt:
        print(f"\n  Running NCLT for {date_display} …\n")
        asyncio.run(run_nclt_automation(search_date=nclt_date))

    if run_nclat or run_sci or run_bhc:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        })

        all_pdfs, courts_checked = [], []

        if run_nclat:
            courts_checked.append("NCLAT")
            pdfs = get_nclat_pdfs(date_str, session)
            print(f"  NCLAT: {len(pdfs)} PDF(s)"); all_pdfs.extend(pdfs)

        if run_sci:
            courts_checked.append("Supreme Court of India")
            pdfs = get_sci_pdfs(date_str, session)
            print(f"  SCI:   {len(pdfs)} PDF(s)"); all_pdfs.extend(pdfs)

        if run_bhc:
            courts_checked.append("Bombay High Court")
            pdfs = get_bhc_pdfs(date_str, session)
            print(f"  BHC:   {len(pdfs)} PDF(s)"); all_pdfs.extend(pdfs)

        if not all_pdfs:
            print(f"\n  No PDFs found for {date_display}.")
            label = " &amp; ".join(courts_checked)
            subject = f"Court Report ({' & '.join(courts_checked)}) — {date_display}"
            body = (f"<html><body style='font-family:Arial,sans-serif'><p>Dear Manager,</p>"
                    f"<p>No cause list PDFs were found on <b>{label}</b> for <b>{date_display}</b>. "
                    f"Courts may not be sitting today (holiday/weekend) or cause lists have not been published yet.</p>"
                    f"</body></html>")
            send_email(subject, body, date_str)
        else:
            print(f"\n  Scanning {len(all_pdfs)} PDF(s) for {len(CLIENT_NAMES)} clients...\n")
            raw_results = []
            for entry in all_pdfs:
                raw_results.extend(search_pdf(entry, session))
            final = deduplicate(raw_results)

            print("\n" + "─"*55)
            if final:
                for r in final:
                    print(f"  ✓ [{r['source']}] {r['client']}  →  {r['description']}")
                    for blk in r["entries"]:
                        for line in blk[:3]: print(f"       {line}")
            else:
                print("  No matches found.")
            print("─"*55 + "\n")

            subject = f"Court Report ({' & '.join(courts_checked)}) — {date_display}"
            body    = build_email(final, date_display, courts_checked)
            send_email(subject, body, date_str)

    print("\n  Done.\n")


if __name__ == "__main__":
    main()