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
    """Lightweight parser built for common US property strings.
    Accepts: 1342 Branham Ln #1, San Jose, CA 95118
    Returns street/unit/city/state/zip. User can override in expander.
    """
    text = (full or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?i)\b(lane)\b", "Ln", text)
    text = re.sub(r"(?i)\b(avenue)\b", "Ave", text)
    text = re.sub(r"(?i)\b(drive)\b", "Dr", text)
    text = re.sub(r"(?i)\b(road)\b", "Rd", text)
    text = re.sub(r"(?i)\b(street)\b", "St", text)
    text = re.sub(r"(?i)\b(court)\b", "Ct", text)
    text = re.sub(r"(?i)\b(place)\b", "Pl", text)
    text = re.sub(r"(?i)\b(circle)\b", "Cir", text)
    text = re.sub(r"(?i)\b(way)\b", "Way", text)
    text = re.sub(r"\s*#\s*", " #", text)

    zip_code = ""
    mzip = re.search(r"\b(\d{5})(?:-\d{4})?\b", text)
    if mzip:
        zip_code = mzip.group(1)
        text = text.replace(mzip.group(0), "").strip(" ,")

    state = ""
    mstate = re.search(r",?\s*([A-Z]{2})\s*$", text)
    if mstate:
        state = mstate.group(1)
        text = text[:mstate.start()].strip(" ,")

    parts = [p.strip() for p in text.split(",") if p.strip()]
    street_part = parts[0] if parts else text
    city = parts[1] if len(parts) > 1 else ""

    unit = ""
    patterns = [r"(?i)\s+(?:unit|apt|apartment|suite|ste)\s*#?\s*([A-Za-z0-9-]+)\b", r"\s+#\s*([A-Za-z0-9-]+)\b"]
    for pat in patterns:
        m = re.search(pat, street_part)
        if m:
            unit = m.group(1)
            street_part = re.sub(pat, "", street_part).strip()
            break

    return {"street": street_part.strip(), "unit": unit.strip(), "city": city.strip(), "state": state or "CA", "zip": zip_code}

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

def sale_price(c: Dict[str, Any]) -> Optional[float]:
    for k in ["transferPrice", "salePriceLastTransfer", "pastPriceTransfer", "pastPriceSale", "assessorSalePrice"]:
        v = num(c.get(k))
        if v and v > 10000:
            return v
    transfers = c.get("transfers") or []
    for tr in transfers:
        v = num(tr.get("transferPrice"))
        if v and v > 10000:
            return v
    return None

def sale_date(c: Dict[str, Any]) -> Any:
    for k in ["transferDateObject", "transferDate", "purchaseSaleDate", "recordingDate", "purchaseRecordingDate"]:
        if c.get(k):
            return c.get(k)
    transfers = c.get("transfers") or []
    for tr in transfers:
        if tr.get("transferDateObject") or tr.get("transferDate"):
            return tr.get("transferDateObject") or tr.get("transferDate")
    return None

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
        price = sale_price(c)
        sqft = num(c.get("livingArea") or c.get("buildingArea"))
        if not price or not sqft:
            continue
        if c.get("parcelId") == subj_parcel:
            continue
        # strict type lock
        if subject_type == "condo" and c.get("condo") is not True:
            continue
        if subject_type == "house" and c.get("condo") is True:
            continue
        key = (c.get("parcelId"), price, sale_date(c))
        if key in seen:
            continue
        seen.add(key)
        dist = haversine(subject.get("latitude"), subject.get("longitude"), c.get("latitude"), c.get("longitude"))
        score = comp_score(c, subject, subject_type)
        rows.append({
            "raw": c,
            "Address": c.get("addressFull") or c.get("addressUnit") or c.get("address") or c.get("addressRaw") or "—",
            "Sold Date": fmt_date(sale_date(c)),
            "Sold Price": price,
            "$/SF": price / sqft if sqft else None,
            "SqFt": int(sqft) if sqft else None,
            "Beds": c.get("totalBedrooms"),
            "Baths": c.get("totalBathrooms"),
            "Distance": dist,
            "Buyer / Current Owner": buyer_name(c),
            "Match": score,
        })
    rows.sort(key=lambda r: (-r["Match"], r["Distance"] if r["Distance"] is not None else 99))
    return rows

def weighted_arv(rows: List[Dict[str, Any]], subject: Dict[str, Any]) -> Optional[float]:
    if not rows: return None
    sqft = num(subject.get("livingArea") or subject.get("buildingArea"))
    if not sqft: return None
    top = rows[:6]
    weights = []
    vals = []
    for r in top:
        ppsf = r.get("$/SF") or 0
        w = max(1, r.get("Match", 0))
        vals.append(ppsf * sqft * w)
        weights.append(w)
    return sum(vals) / sum(weights) if weights else None

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

with st.sidebar:
    st.title("Settings")
    st.markdown('<div class="good-pill">Primary Data Source: Realie</div>', unsafe_allow_html=True)
    st.caption("RentCast is no longer used for final comps.")
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before fallback", min_value=1, max_value=10, value=3, step=1)
    show_diag = st.toggle("Show API diagnostics", value=False)
    st.divider()
    st.subheader("Contact Actions")
    st.caption("Call/SMS opens your Mac/iPhone default calling or Messages app.")

st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("Realie-powered V6: smart address parsing → verified comps → weighted ARV → MAO")

col1, col2 = st.columns([2.2, 1])
with col1:
    address_input = st.text_input("Property Address", value="1342 Branham Ln #1, San Jose, CA 95118")
    parsed_default = parse_address(address_input)
    with st.expander("Address details / override", expanded=False):
        c1, c2 = st.columns([2, 1])
        with c1:
            street = st.text_input("Street address only", value=parsed_default["street"])
        with c2:
            unit = st.text_input("Unit only", value=parsed_default["unit"])
        c3, c4, c5 = st.columns([1, .5, 1])
        with c3:
            city = st.text_input("City", value=parsed_default["city"] or "San Jose")
        with c4:
            state = st.text_input("State", value=parsed_default["state"] or "CA")
        with c5:
            county = st.text_input("County", value="Santa Clara")
    seller_phone = st.text_input("Seller Phone (optional, for Call/SMS buttons)", value="")
    photo_link = st.text_input("Dropbox / Google Drive Photo Link", value="")
with col2:
    st.write("Upload Property Photos")
    st.file_uploader("Upload", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, label_visibility="collapsed")
    st.caption("200MB per file • PNG, JPG, WEBP")

sms_msg = st.text_input("SMS message", value="Hi, this is Marco with Newcastle Partners...")
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
            st.error("Realie did not return a subject property. Try spelling out Lane, Avenue, Drive, etc., or remove the unit and retry.")
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
            {"label": "Ideal: 0.5 mi / 6 mo / same type / same bed-bath / ±300 sqft", "radius": 0.5, "months": 6, "sqft": True, "beds": True, "baths": True, "propertyType": subject_type},
            {"label": "Fallback: 1.0 mi / 12 mo / same type / same bed-bath / ±300 sqft", "radius": 1.0, "months": 12, "sqft": True, "beds": True, "baths": True, "propertyType": subject_type},
            {"label": "Fallback: 1.0 mi / 18 mo / same type / same bed-bath / ±300 sqft", "radius": 1.0, "months": 18, "sqft": True, "beds": True, "baths": True, "propertyType": subject_type},
            {"label": "Closest available: 1.0 mi / 18 mo / any property type, filtered back to same type", "radius": 1.0, "months": 18, "sqft": False, "beds": True, "baths": True, "propertyType": "any"},
        ]
        best_rows, best_resp, rule_used = [], None, None
        for rule in rules:
            resp = comp_search(lat, lon, rule, subject_type, subject)
            if show_diag:
                st.write(rule["label"]); st.json(resp)
            comps = (((resp.get("json") or {}).get("comparables")) or []) if resp.get("ok") else []
            rows = build_comp_rows(comps, subject, subject_type)
            if rows and (len(rows) >= int(min_comps) or not best_rows):
                best_rows, best_resp, rule_used = rows, resp, rule
            if len(rows) >= int(min_comps):
                break
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

    arv = weighted_arv(best_rows, subject) or num(subject.get("modelValue"))
    confidence = min(99, max(55, round((sum(r["Match"] for r in best_rows[:6]) / max(1, len(best_rows[:6])) / 150) * 100))) if best_rows else 55
    st.subheader("ARV + Offer Matrix")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recommended ARV", money(arv))
    c2.metric("Confidence", f"{confidence}%")
    c3.metric("Comps Used", str(min(len(best_rows), 6)))
    c4.metric("Comp Rule Used", rule_used["label"] if rule_used else "—")

    oc = st.columns(4)
    for col, pct in zip(oc, [.75, .70, .65, .60]):
        before = (arv or 0) * pct
        after = before - repair_estimate
        col.metric(f"{int(pct*100)}% ARV", money(before), help="Before repairs")
        col.caption(f"After repairs: {money(after)}")

    st.subheader("Comparable Sales")
    st.caption("Same property type locked. Buyer/current owner uses grantee first, then ownerName fallback.")
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
                dc2.write(f"Date: {fmt_date(sale_date(c))}")
                dc2.write(f"Price: {money(sale_price(c))}")
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
