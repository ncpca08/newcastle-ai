import re
import math
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle AI Analyzer", page_icon="🏠", layout="wide")

REALIE_BASE = "https://app.realie.ai"
PROPERTY_SEARCH_PATH = "/api/public/property/search/"
PREMIUM_COMPS_PATH = "/api/public/premium/comparables/"

CITY_COUNTY_MAP = {
    "stockton": "San Joaquin",
    "san jose": "Santa Clara",
    "modesto": "Stanislaus",
    "fresno": "Fresno",
    "bakersfield": "Kern",
    "sacramento": "Sacramento",
    "manteca": "San Joaquin",
    "lodi": "San Joaquin",
    "turlock": "Stanislaus",
    "merced": "Merced",
    "visalia": "Tulare",
    "tulare": "Tulare",
    "madera": "Madera",
}


# ----------------------------- helpers -----------------------------
def money(v: Any) -> str:
    try:
        if v is None or v == "":
            return "—"
        return f"${float(v):,.0f}"
    except Exception:
        return "—"

def num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "": return None
        return float(v)
    except Exception:
        return None

def fmt_date(raw: Any) -> str:
    if not raw:
        return "—"
    s = str(raw)
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").strftime("%m/%d/%Y")
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        pass
    return s

def days_ago(raw: Any) -> Optional[int]:
    if not raw: return None
    try:
        s = str(raw)
        if len(s) == 8 and s.isdigit():
            d = datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - d).days)
    except Exception:
        return None

def clean_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits

def contact_links(phone: str, sms_body: str) -> Tuple[str, str]:
    d = clean_phone(phone)
    if not d:
        return "#", "#"
    tel = f"tel:+1{d}"
    body = urllib.parse.quote(sms_body or "")
    sms = f"sms:+1{d}&body={body}"
    return tel, sms

def parse_address(full: str) -> Dict[str, str]:
    """Flexible parser for common seller-lead address formats.
    Handles comma and non-comma formats like:
    1342 Branham Ln #1, San Jose, CA 95118
    1409 San Juan Ave Stockton, CA 95203
    """
    original = (full or "").strip()
    text = re.sub(r"\s+", " ", original).strip(" ,")
    text = re.sub(r"\s*#\s*", " #", text)

    zip_code = ""
    mzip = re.search(r"\b(\d{5})(?:-\d{4})?\b", text)
    if mzip:
        zip_code = mzip.group(1)
        text = text[:mzip.start()] + text[mzip.end():]
        text = text.strip(" ,")

    state = "CA"
    mstate = re.search(r",?\s*([A-Za-z]{2})\s*$", text)
    if mstate:
        state = mstate.group(1).upper()
        text = text[:mstate.start()].strip(" ,")

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        street_part = parts[0]
        city = parts[1]
    else:
        street_part = text
        city = ""
        low = text.lower()
        # If no comma before city, pull a known city from the end.
        for cname in sorted(CITY_COUNTY_MAP.keys(), key=len, reverse=True):
            if low.endswith(" " + cname) or low == cname:
                city = cname.title()
                street_part = text[: len(text) - len(cname)].strip(" ,")
                break

    unit = ""
    patterns = [
        r"(?i)\s+(?:unit|apt|apartment|suite|ste)\s*#?\s*([A-Za-z0-9-]+)\b",
        r"\s+#\s*([A-Za-z0-9-]+)\b",
    ]
    for pat in patterns:
        m = re.search(pat, street_part)
        if m:
            unit = m.group(1)
            street_part = re.sub(pat, "", street_part).strip()
            break

    county = CITY_COUNTY_MAP.get(city.lower().strip(), "") if city else ""
    return {"street": street_part.strip(), "unit": unit.strip(), "city": city.strip(), "state": state, "zip": zip_code, "county": county}

def api_get(path: str, params: Dict[str, Any], label: str) -> Dict[str, Any]:
    key = st.secrets.get("REALIE_API_KEY", "")
    if not key:
        return {"ok": False, "error": "Missing REALIE_API_KEY in Streamlit Secrets."}
    headers = {"Authorization": key, "Accept": "application/json"}
    url = REALIE_BASE + path
    clean_params = {k: v for k, v in params.items() if v not in [None, "", [], {}]}
    try:
        r = requests.get(url, headers=headers, params=clean_params, timeout=30)
        ct = r.headers.get("content-type", "")
        try:
            js = r.json()
        except Exception:
            js = None
        return {
            "ok": r.ok,
            "status_code": r.status_code,
            "label": label,
            "url_tested": url,
            "params": clean_params,
            "content_type": ct,
            "body_preview": r.text[:1500],
            "json": js,
        }
    except Exception as e:
        return {"ok": False, "label": label, "url_tested": url, "params": clean_params, "error": str(e)}

def identify_property_type(p: Dict[str, Any]) -> str:
    if p.get("condo") is True:
        return "condo"
    # Realie premium endpoint supports any / condo / house. Treat non-condo residential as house for comp search.
    if p.get("residential") is True:
        return "house"
    return "any"

def property_label(t: str) -> str:
    return {"condo": "Condo", "house": "Single Family / House", "any": "Any"}.get(t, t.title())

def find_subject(properties: List[Dict[str, Any]], unit: str = "") -> Optional[Dict[str, Any]]:
    if not properties:
        return None
    unit_clean = re.sub(r"\D", "", unit or "")
    if unit_clean:
        for p in properties:
            legal = str(p.get("legalDesc", ""))
            u = str(p.get("unitNumberStripped", ""))
            if u == unit_clean or re.search(rf"\bUNIT\s+{re.escape(unit_clean)}\b", legal, flags=re.I):
                return p
    # Prefer property with modelValue and full data
    with_avm = [p for p in properties if p.get("modelValue")]
    return with_avm[0] if with_avm else properties[0]

def subject_search(parsed: Dict[str, str], county: str) -> Dict[str, Any]:
    # For this API, unit can over-restrict; search without unit and match unit locally.
    params = {
        "state": parsed.get("state") or "CA",
        "address": parsed.get("street"),
        "residential": "true",
        "limit": 10,
        "county": county or None,
    }
    if parsed.get("city"):
        params["city"] = parsed["city"]
    return api_get(PROPERTY_SEARCH_PATH, params, "Property Search")

def comp_search(lat: float, lon: float, rule: Dict[str, Any], subject_type: str, subject: Dict[str, Any]) -> Dict[str, Any]:
    sqft = int(num(subject.get("livingArea") or subject.get("buildingArea")) or 0)
    beds = int(num(subject.get("totalBedrooms")) or 0)
    baths = int(num(subject.get("totalBathrooms")) or 0)
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "radius": rule["radius"],
        "timeFrame": rule["months"],
        "maxResults": 25,
        "propertyType": rule.get("propertyType", subject_type),
    }
    if rule.get("sqft") and sqft:
        params["sqftMin"] = max(0, sqft - 300)
        params["sqftMax"] = sqft + 300
    if rule.get("beds") and beds:
        params["bedsMin"] = beds
        params["bedsMax"] = beds
    if rule.get("baths") and baths:
        params["bathsMin"] = baths
        params["bathsMax"] = baths
    return api_get(PREMIUM_COMPS_PATH, params, f"Comps: {rule['label']}")

def parse_dt(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = str(raw)
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return None

def best_sale(c: Dict[str, Any]) -> Dict[str, Any]:
    """Return the best actual sale event from Realie.
    Preference is recorded purchase sale date + transfer price, then grant deed transfers.
    This avoids using non-sale/intra-family transfer dates as the comp sold date when a true sale date exists.
    """
    candidates = []

    def add(price, date, doc_type="", source="", verified=False):
        p = num(price)
        d = parse_dt(date)
        if not p or p < 10000 or not d:
            return
        candidates.append({
            "price": p,
            "date_raw": date,
            "date_obj": d,
            "doc_type": doc_type or "—",
            "source": source,
            "verified": verified,
        })

    # Best source when available: purchaseSaleDate is the contract/sale date; transferPrice is the recorded sale price.
    add(c.get("transferPrice"), c.get("purchaseSaleDate"), c.get("saleDocumentTypeLastSale") or c.get("transferDocType"), "purchaseSaleDate + transferPrice", True)
    add(c.get("salePriceLastTransfer"), c.get("purchaseSaleDate"), c.get("saleDocumentTypeLastSale"), "purchaseSaleDate + salePriceLastTransfer", True)

    # Next: recorded grant deed style transfers. Avoid making IT/QC transfers primary unless no better sale exists.
    for tr in c.get("transfers") or []:
        doc = str(tr.get("transferDocType") or c.get("transferDocType") or "").upper()
        is_sale_doc = doc in {"GD", "WD", "DEED", "GRANT DEED"}
        add(tr.get("transferPrice"), tr.get("transferDateObject") or tr.get("transferDate"), doc, "transfers[]", is_sale_doc)

    add(c.get("transferPrice"), c.get("transferDateObject") or c.get("transferDate"), c.get("transferDocType"), "top-level transfer", False)
    add(c.get("pastPriceSale"), c.get("priorSalesDate") or c.get("pastRecoDateSale"), c.get("pastDocumentTypeSale"), "prior sale", True)

    if not candidates:
        return {"price": None, "date_raw": None, "date_obj": None, "doc_type": "—", "source": "none", "verified": False}

    # Prefer verified sale events, then newest.
    candidates.sort(key=lambda x: (1 if x["verified"] else 0, x["date_obj"]), reverse=True)
    return candidates[0]

def sale_price(c: Dict[str, Any]) -> Optional[float]:
    return best_sale(c).get("price")

def sale_date(c: Dict[str, Any]) -> Any:
    return best_sale(c).get("date_raw")

def buyer_name(c: Dict[str, Any]) -> str:
    return c.get("grantee") or c.get("ownerName") or "—"

def haversine(lat1, lon1, lat2, lon2) -> Optional[float]:
    try:
        R = 3958.8
        phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dl = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    except Exception:
        return None

def comp_score(c: Dict[str, Any], subj: Dict[str, Any], subject_type: str) -> int:
    score = 0
    # property type lock / match
    if subject_type == "condo" and c.get("condo") is True:
        score += 30
    elif subject_type == "house" and c.get("condo") is not True:
        score += 30
    # subdivision/tract
    if c.get("subdivision") and c.get("subdivision") == subj.get("subdivision"):
        score += 25
    elif c.get("tractNum") and c.get("tractNum") == subj.get("tractNum"):
        score += 15
    # beds baths
    if c.get("totalBedrooms") == subj.get("totalBedrooms"):
        score += 15
    if c.get("totalBathrooms") == subj.get("totalBathrooms"):
        score += 10
    # sqft similarity
    s = num(subj.get("livingArea") or subj.get("buildingArea")) or 0
    cs = num(c.get("livingArea") or c.get("buildingArea")) or 0
    if s and cs:
        diff = abs(s - cs)
        if diff <= 50: score += 20
        elif diff <= 150: score += 15
        elif diff <= 300: score += 10
        elif diff <= 500: score += 5
    # recency
    da = days_ago(sale_date(c))
    if da is not None:
        if da <= 180: score += 20
        elif da <= 365: score += 15
        elif da <= 545: score += 10
        else: score += 5
    # distance
    d = haversine(subj.get("latitude"), subj.get("longitude"), c.get("latitude"), c.get("longitude"))
    if d is not None:
        if d <= .25: score += 20
        elif d <= .5: score += 15
        elif d <= 1: score += 10
        elif d <= 2: score += 5
    # year built
    y = num(subj.get("yearBuilt")); cy = num(c.get("yearBuilt"))
    if y and cy:
        if abs(y - cy) <= 5: score += 10
        elif abs(y - cy) <= 15: score += 5
    return min(score, 150)

def build_comp_rows(comps: List[Dict[str, Any]], subject: Dict[str, Any], subject_type: str) -> List[Dict[str, Any]]:
    rows = []
    seen = set()
    subj_parcel = subject.get("parcelId")
    for c in comps:
        sale = best_sale(c)
        price = sale.get("price")
        sqft = num(c.get("livingArea") or c.get("buildingArea"))
        if not price or not sqft:
            continue
        if c.get("parcelId") == subj_parcel:
            continue
        # strict type lock: no condo/SFR mixing
        if subject_type == "condo" and c.get("condo") is not True:
            continue
        if subject_type == "house" and c.get("condo") is True:
            continue
        key = (c.get("parcelId"), round(price), fmt_date(sale.get("date_raw")))
        if key in seen:
            continue
        seen.add(key)
        dist = haversine(subject.get("latitude"), subject.get("longitude"), c.get("latitude"), c.get("longitude"))
        score = comp_score(c, subject, subject_type)
        rows.append({
            "raw": c,
            "sale": sale,
            "Address": c.get("addressFull") or c.get("addressUnit") or c.get("address") or c.get("addressRaw") or "—",
            "Sold Date": fmt_date(sale.get("date_raw")),
            "Sold Date Obj": sale.get("date_obj"),
            "Sold Price": price,
            "$/SF": price / sqft if sqft else None,
            "SqFt": int(sqft) if sqft else None,
            "Beds": c.get("totalBedrooms"),
            "Baths": c.get("totalBathrooms"),
            "Distance": dist,
            "Buyer / Current Owner": buyer_name(c),
            "Sale Source": sale.get("source"),
            "Verified Sale": "Yes" if sale.get("verified") else "Review",
            "Match": score,
        })
    # Display order should be newest sold comp first. ARV uses a separate score-weighted calculation.
    rows.sort(key=lambda r: (r["Sold Date Obj"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return rows

def sort_for_arv(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (-r["Match"], r["Distance"] if r["Distance"] is not None else 99))

def arv_tiers(rows: List[Dict[str, Any]], subject: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    sqft = num(subject.get("livingArea") or subject.get("buildingArea"))
    if not sqft:
        mv = num(subject.get("modelValue"))
        return (mv * .95 if mv else None, mv, mv * 1.05 if mv else None)
    vals = []
    for r in sort_for_arv(rows)[:10]:
        ppsf = r.get("$/SF")
        if ppsf:
            vals.append(ppsf * sqft)
    if not vals:
        mv = num(subject.get("modelValue"))
        return (mv * .95 if mv else None, mv, mv * 1.05 if mv else None)
    vals = sorted(vals)
    def pct(p):
        idx = int(round((len(vals)-1) * p))
        return vals[idx]
    expected = weighted_arv(rows, subject) or sum(vals)/len(vals)
    return pct(.25), expected, pct(.75)

def weighted_arv(rows: List[Dict[str, Any]], subject: Dict[str, Any]) -> Optional[float]:
    if not rows: return None
    sqft = num(subject.get("livingArea") or subject.get("buildingArea"))
    if not sqft: return None
    top = sort_for_arv(rows)[:6]
    weights = []
    vals = []
    for r in top:
        ppsf = r.get("$/SF") or 0
        w = max(1, r.get("Match", 0))
        vals.append(ppsf * sqft * w)
        weights.append(w)
    return sum(vals) / sum(weights) if weights else None


# ----------------------------- auth + lead queue -----------------------------
def get_users() -> Dict[str, Any]:
    users = st.secrets.get("users", {})
    try:
        return dict(users)
    except Exception:
        return {}

def login_gate() -> Tuple[str, str]:
    users = get_users()
    if "auth_user" in st.session_state:
        return st.session_state["auth_user"], st.session_state.get("auth_role", "admin")

    # Do not lock the owner out before Secrets are configured.
    if not users:
        st.session_state["auth_user"] = "Marco"
        st.session_state["auth_role"] = "admin"
        return "Marco", "admin"

    st.title("🏠 Newcastle AI Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Log in", type="primary"):
        ukey = username.strip().lower()
        rec = users.get(ukey)
        if rec and str(rec.get("password", "")) == password:
            st.session_state["auth_user"] = rec.get("name", username.strip().title())
            st.session_state["auth_role"] = rec.get("role", "va")
            st.rerun()
        else:
            st.error("Invalid login.")
    st.stop()

def seed_leads():
    if "leads" not in st.session_state:
        st.session_state["leads"] = []

def add_lead(name: str, phone: str, address: str, source: str, assigned: str = "Unassigned"):
    seed_leads()
    st.session_state["leads"].append({
        "id": f"L{len(st.session_state['leads'])+1:04d}",
        "name": name or "Unknown Seller",
        "phone": clean_phone(phone),
        "address": address,
        "source": source or "Manual",
        "status": "New",
        "assigned": assigned,
        "locked_by": "",
        "notes": "",
        "created": datetime.now().strftime("%m/%d/%Y %I:%M %p"),
    })

def status_icon(status: str) -> str:
    return {"New":"🔴", "In Progress":"🟠", "Follow Up":"🟡", "Hot":"🟢", "Offer Made":"🔵", "Dead":"⚫"}.get(status, "⚪")

def render_lead_queue(current_user: str, role: str):
    seed_leads()
    st.header("Lead Queue")
    st.caption("Temporary in-app queue. Next phase: sync this with Zoho Bigin so it becomes shared and persistent.")
    with st.expander("Add lead manually", expanded=False):
        c1, c2 = st.columns(2)
        name = c1.text_input("Seller name", key="new_lead_name")
        phone = c2.text_input("Phone", key="new_lead_phone")
        addr = st.text_input("Property address", key="new_lead_addr")
        c3, c4 = st.columns(2)
        source = c3.selectbox("Source", ["SMS", "Email", "Website", "Referral", "Manual"], key="new_lead_source")
        assigned = c4.selectbox("Assigned to", ["Unassigned", "Marco", "Doreen"], key="new_lead_assigned")
        if st.button("Add to Queue"):
            add_lead(name, phone, addr, source, assigned)
            st.rerun()

    leads = st.session_state["leads"]
    if not leads:
        st.info("No leads in the queue yet.")
        return
    counts = {s: sum(1 for l in leads if l["status"] == s) for s in ["New", "In Progress", "Follow Up", "Hot", "Offer Made", "Dead"]}
    cols = st.columns(6)
    for col, sname in zip(cols, counts):
        col.metric(f"{status_icon(sname)} {sname}", counts[sname])

    for i, lead in enumerate(leads):
        title = f"{status_icon(lead['status'])} {lead['name']} — {lead['address']} — {lead['status']}"
        with st.expander(title, expanded=lead["status"] in ["New", "Hot"]):
            c1, c2, c3 = st.columns([1,1,1])
            c1.write(f"**Source:** {lead['source']}")
            c1.write(f"**Created:** {lead['created']}")
            c2.write(f"**Assigned:** {lead['assigned']}")
            c2.write(f"**Worked by:** {lead['locked_by'] or '—'}")
            c3.write(f"**Phone:** {lead['phone'] or '—'}")
            c3.write(f"**Lead ID:** {lead['id']}")

            cc1, cc2, cc3 = st.columns(3)
            new_status = cc1.selectbox("Status", ["New", "In Progress", "Follow Up", "Hot", "Offer Made", "Dead"], index=["New", "In Progress", "Follow Up", "Hot", "Offer Made", "Dead"].index(lead["status"]), key=f"status_{i}")
            new_assigned = cc2.selectbox("Assigned", ["Unassigned", "Marco", "Doreen"], index=["Unassigned", "Marco", "Doreen"].index(lead["assigned"]) if lead["assigned"] in ["Unassigned", "Marco", "Doreen"] else 0, key=f"assigned_{i}")
            if cc3.button("I'm working this", key=f"lock_{i}"):
                lead["locked_by"] = current_user
                lead["status"] = "In Progress"
                st.rerun()
            lead["status"] = new_status
            lead["assigned"] = new_assigned
            lead["notes"] = st.text_area("Notes", value=lead.get("notes", ""), key=f"notes_{i}")
            d = clean_phone(lead.get("phone", ""))
            msg = urllib.parse.quote(f"Hi {lead['name']}, this is Marco with Newcastle Partners. I was reaching out about {lead['address']}. Are you still open to selling?")
            if d:
                st.markdown(f"[📞 Call](tel:+1{d}) &nbsp;&nbsp; [💬 SMS](sms:+1{d}&body={msg})", unsafe_allow_html=True)
            if st.button("Load in Analyzer", key=f"load_{i}"):
                st.session_state["address_input"] = lead["address"]
                st.session_state["seller_phone"] = lead["phone"]
                st.session_state["seller_name"] = lead["name"]
                st.rerun()

# ----------------------------- UI -----------------------------
st.markdown("""
<style>
.stApp { background: #0d1117; }
[data-testid="stSidebar"] { background: #2b2c35; }
.metric-card {background:#111827; border:1px solid #22314a; border-radius:16px; padding:18px; min-height:105px;}
.metric-label {font-size:13px; color:#cbd5e1; font-weight:600;}
.metric-value {font-size:30px; color:#f8fafc; font-weight:700; margin-top:8px;}
.good-pill {background:#153b2a; color:#65d68b; border-radius:10px; padding:10px 14px; font-weight:700;}
.note-box {background:#111827; border:1px solid #22314a; border-radius:16px; padding:18px;}
</style>
""", unsafe_allow_html=True)

current_user, current_role = login_gate()

with st.sidebar:
    st.title("Settings")
    st.caption(f"Logged in as: {current_user} ({current_role})")
    if st.button("Log out"):
        for k in ["auth_user", "auth_role"]:
            st.session_state.pop(k, None)
        st.rerun()
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before fallback", min_value=1, max_value=10, value=3, step=1)
    show_diag = st.toggle("Show API diagnostics", value=False) if current_role == "admin" else False
    st.divider()
    st.subheader("Contact Actions")
    st.caption("Marco can use Apple/Mac tel/sms links now. Doreen's calling provider can be connected later.")

st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("Realie-powered V7: login + lead queue + smart parsing + verified comps + weighted ARV")

tab_analyzer, tab_queue = st.tabs(["Property Analyzer", "Lead Queue"])

with tab_queue:
    render_lead_queue(current_user, current_role)

with tab_analyzer:
    col1, col2 = st.columns([2.2, 1])
    with col1:
        address_input = st.text_input("Property Address", value=st.session_state.get("address_input", "1342 Branham Ln #1, San Jose, CA 95118"), key="address_input_box")
        parsed_default = parse_address(address_input)
        with st.expander("Address details / override", expanded=False):
            c1, c2 = st.columns([2, 1])
            with c1:
                street = st.text_input("Street address only", value=parsed_default["street"])
            with c2:
                unit = st.text_input("Unit only", value=parsed_default["unit"])
            c3, c4, c5 = st.columns([1, .5, 1])
            with c3:
                city = st.text_input("City", value=parsed_default["city"])
            with c4:
                state = st.text_input("State", value=parsed_default["state"] or "CA")
            with c5:
                county = st.text_input("County", value=parsed_default.get("county", ""))
        seller_name = st.text_input("Seller Name", value=st.session_state.get("seller_name", ""))
        seller_phone = st.text_input("Seller Phone (optional, for Call/SMS buttons)", value=st.session_state.get("seller_phone", ""))
        photo_link = st.text_input("Dropbox / Google Drive Photo Link", value="")
    with col2:
        st.write("Upload Property Photos")
        st.file_uploader("Upload", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, label_visibility="collapsed")
        st.caption("200MB per file • PNG, JPG, WEBP")

    street_for_msg = street or parsed_default.get("street", "the property")
    sms_template = "Hi {seller_name}, this is Marco with Newcastle Partners. I was reaching out about {street_address}. Are you still open to selling?"
    sms_msg = st.text_input("SMS message", value=sms_template.format(seller_name=seller_name or "there", street_address=street_for_msg))
    tel_link, sms_link = contact_links(seller_phone, sms_msg)
    if clean_phone(seller_phone):
        st.markdown(f"[📞 Call Seller]({tel_link}) &nbsp;&nbsp; [💬 SMS Seller]({sms_link})", unsafe_allow_html=True)

    analyze = st.button("Analyze Property", type="primary", use_container_width=True)

if analyze:
    parsed = {"street": street.strip(), "unit": unit.strip(), "city": city.strip(), "state": state.strip() or "CA", "zip": parsed_default.get("zip", "")}
    with st.status("Analyzing property with Realie...", expanded=True) as status:
        st.write("Searching subject property...")
        prop_resp = subject_search(parsed, county.strip())
        if show_diag:
            st.json(prop_resp)
        props = (((prop_resp.get("json") or {}).get("properties")) or []) if prop_resp.get("ok") else []
        subject = find_subject(props, parsed.get("unit"))
        if not subject:
            status.update(label="Property not found", state="error")
            st.error("Property not found. Check the parsed Street/City/County fields under Address details. County can be left blank if unsure.")
            st.stop()
        st.write("Property found.")
        subject_type = identify_property_type(subject)
        lat = num(subject.get("latitude")); lon = num(subject.get("longitude"))
        if lat is None or lon is None:
            status.update(label="Missing coordinates", state="error")
            st.error("Subject found, but no latitude/longitude returned.")
            st.stop()
        st.write("Finding comparable sales...")
        rules = [
            {"label": "Ideal: 0.5 mi / 6 mo / same type / same bed-bath / ±300 sqft", "radius": 0.5, "months": 6, "sqft": True, "beds": True, "baths": True, "propertyType": "any"},
            {"label": "Strong: 1.0 mi / 6 mo / same type / same bed-bath / ±300 sqft", "radius": 1.0, "months": 6, "sqft": True, "beds": True, "baths": True, "propertyType": "any"},
            {"label": "Standard: 1.0 mi / 12 mo / same type / same bed-bath / ±300 sqft", "radius": 1.0, "months": 12, "sqft": True, "beds": True, "baths": True, "propertyType": "any"},
            {"label": "Backup: 1.0 mi / 12 mo / same type / ±300 sqft", "radius": 1.0, "months": 12, "sqft": True, "beds": False, "baths": False, "propertyType": "any"},
        ]
        all_comps = []
        successful_labels = []
        for rule in rules:
            resp = comp_search(lat, lon, rule, subject_type, subject)
            if show_diag:
                st.write(rule["label"]); st.json(resp)
            comps = (((resp.get("json") or {}).get("comparables")) or []) if resp.get("ok") else []
            if comps:
                all_comps.extend(comps)
                successful_labels.append(rule["label"])

        best_rows = build_comp_rows(all_comps, subject, subject_type)
        rule_used = {"label": "Verified sweep: newest sales first; same property type locked; ±300 sqft preferred; 6 mo first, 12 mo backup"}
        status.update(label="Analysis complete", state="complete")

    # Subject Summary
    st.subheader("Subject Property")
    cols = st.columns(6)
    data = [
        ("Property Type", property_label(subject_type)),
        ("Beds", subject.get("totalBedrooms") or "—"),
        ("Baths", subject.get("totalBathrooms") or "—"),
        ("House SqFt", subject.get("livingArea") or subject.get("buildingArea") or "—"),
        ("Lot SqFt", subject.get("lotSizeArea") or "—"),
        ("Year Built", subject.get("yearBuilt") or "—"),
    ]
    for c, (lab, val) in zip(cols, data):
        c.markdown(f'<div class="metric-card"><div class="metric-label">{lab}</div><div class="metric-value">{val}</div></div>', unsafe_allow_html=True)
    st.caption(f"Owner: {subject.get('ownerName','—')} | Parcel: {subject.get('parcelId','—')} | Legal: {subject.get('legalDesc','—')}")

    conservative_arv, expected_arv, aggressive_arv = arv_tiers(best_rows, subject)
    arv = conservative_arv or expected_arv or num(subject.get("modelValue"))
    arv_ranked_rows = sort_for_arv(best_rows)
    confidence = min(99, max(55, round((sum(r["Match"] for r in arv_ranked_rows[:6]) / max(1, len(arv_ranked_rows[:6])) / 150) * 100))) if best_rows else 55
    st.subheader("ARV + Offer Matrix")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conservative ARV", money(conservative_arv))
    c2.metric("Expected ARV", money(expected_arv))
    c3.metric("Aggressive ARV", money(aggressive_arv))
    c4.metric("Confidence", f"{confidence}%")
    st.caption(f"Comps used for ARV: {min(len(arv_ranked_rows), 6)} | Total verified comps shown: {len(best_rows)} | Rule: {rule_used['label'] if rule_used else '—'}")

    st.markdown("#### Offer Matrix uses Conservative ARV")
    oc = st.columns(4)
    for col, pct in zip(oc, [.75, .70, .65, .60]):
        before = (arv or 0) * pct
        after = before - repair_estimate
        col.metric(f"{int(pct*100)}% ARV", money(before), help="Before repairs")
        col.caption(f"After repairs: {money(after)}")

    st.subheader("Comparable Sales")
    st.caption("Same property type locked. Sorted newest sold first. Buyer/current owner uses grantee first, then ownerName fallback. ARV is calculated from the highest-scoring comps, not simply the first rows shown.")
    if not best_rows:
        st.warning("No comps matched after filtering. Try enabling diagnostics or widening the criteria.")
    else:
        display = []
        for r in best_rows[:12]:
            display.append({
                "Address": r["Address"],
                "Sold Date": r["Sold Date"],
                "Sold Price": money(r["Sold Price"]),
                "$/SF": money(r["$/SF"]),
                "SqFt": r["SqFt"],
                "Beds": r["Beds"],
                "Baths": r["Baths"],
                "Distance": f"{r['Distance']:.2f} mi" if r["Distance"] is not None else "—",
                "Buyer / Current Owner": r["Buyer / Current Owner"],
                "Verified": r["Verified Sale"],
                "Match": f"{round((r['Match']/150)*100)}%",
            })
        st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)
        st.markdown("### Clickable Comp Details")
        for i, r in enumerate(best_rows[:8], 1):
            c = r["raw"]
            with st.expander(f"{i}. {r['Address']} — {money(r['Sold Price'])} — Match {round((r['Match']/150)*100)}%"):
                dc1, dc2, dc3 = st.columns(3)
                dc1.write("**Buyer / Owner**")
                dc1.write(buyer_name(c))
                dc1.write("**Mailing Address**")
                dc1.write(c.get("ownerAddressFull") or "—")
                dc2.write("**Sale / Transfer**")
                dc2.write(f"Date: {r['Sold Date']}")
                dc2.write(f"Price: {money(r['Sold Price'])}")
                dc2.write(f"Source: {r.get('Sale Source', '—')}")
                dc2.write(f"Verified: {r.get('Verified Sale', '—')}")
                dc2.write(f"Doc: {c.get('transferDocType','—')} / {c.get('transferDocNum','—')}")
                dc3.write("**Property Details**")
                dc3.write(f"Parcel: {c.get('parcelId','—')}")
                dc3.write(f"Lender: {c.get('lenderName','—')}")
                dc3.write(f"Lien Balance: {money(c.get('totalLienBalance'))}")
                dc3.write(f"Equity Est: {money(c.get('equityCurrentEstBal'))}")
                maps = urllib.parse.quote(c.get("addressFull") or r["Address"])
                st.markdown(f"[Map](https://www.google.com/maps/search/?api=1&query={maps})", unsafe_allow_html=True)

    st.subheader("AI Acquisition Notes")
    st.markdown(f"""
<div class="note-box">
<b>Property type logic:</b> Subject classified as <b>{property_label(subject_type)}</b>. The comp engine locks to the same property type and filters out mismatches.<br><br>
<b>Comp rule used:</b> {rule_used['label'] if rule_used else 'No comp rule succeeded.'}<br><br>
<b>Recommended starting point:</b> Use the 70% ARV tier unless buyer demand is extremely strong.<br><br>
<b>Next step:</b> Review photos, Street View, condition outliers, and buyer demand before making the final offer.
</div>
""", unsafe_allow_html=True)

else:
    st.divider()
    st.subheader("Quick Call / SMS Launcher")
    st.caption("Enter any phone number and click Call or SMS. This opens the default phone or Messages app on your Mac/iPhone.")
    qc1, qc2 = st.columns([1, 2])
    with qc1:
        quick_phone = st.text_input("Phone number", value=seller_phone)
    with qc2:
        quick_msg = st.text_input("SMS message ", value=sms_msg)
    qtel, qsms = contact_links(quick_phone, quick_msg)
    if clean_phone(quick_phone):
        st.markdown(f"[📞 Call]({qtel}) &nbsp;&nbsp; [💬 SMS]({qsms})", unsafe_allow_html=True)
