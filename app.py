import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle AI Analyzer", page_icon="🏠", layout="wide")

# -------------------------
# CONFIG / SECRETS
# -------------------------
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

REALIE_API_KEY = get_secret("REALIE_API_KEY")
RENTCAST_API_KEY = get_secret("RENTCAST_API_KEY")  # backup only

REALIE_BASE = "https://app.realie.ai/api/public"

# -------------------------
# STYLE
# -------------------------
st.markdown(
    """
    <style>
    .main {background-color: #0f172a; color: #e5e7eb;}
    .block-container {padding-top: 1.5rem;}
    div[data-testid="stMetric"] {background:#111827;border:1px solid #243044;border-radius:16px;padding:16px;}
    .card {background:#111827;border:1px solid #243044;border-radius:18px;padding:18px;margin:10px 0;}
    .small {font-size: 13px;color:#9ca3af;}
    .good {color:#22c55e;font-weight:700;}
    .warn {color:#f59e0b;font-weight:700;}
    .bad {color:#ef4444;font-weight:700;}
    a {color:#93c5fd !important; text-decoration:none;}
    .pill {display:inline-block;padding:4px 9px;border-radius:999px;background:#1f2937;border:1px solid #334155;margin-right:6px;margin-bottom:6px;font-size:12px;}
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# HELPERS
# -------------------------
def money(value):
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return "—"
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return "—"


def number(value):
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return "—"
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return "—"


def normalize_address(address: str) -> str:
    return re.sub(r"\s+", " ", (address or "").strip())


def expand_street_suffix(address: str) -> str:
    # Realie matched better when Ln became Lane on this test property.
    replacements = {
        r"\bLn\b": "Lane",
        r"\bDr\b": "Drive",
        r"\bSt\b": "Street",
        r"\bAve\b": "Avenue",
        r"\bRd\b": "Road",
        r"\bBlvd\b": "Boulevard",
        r"\bCt\b": "Court",
        r"\bPl\b": "Place",
        r"\bWay\b": "Way",
    }
    out = address
    for pat, repl in replacements.items():
        out = re.sub(pat, repl, out, flags=re.I)
    return out


def fmt_date(value):
    dt = parse_date(value)
    return dt.strftime("%m/%d/%Y") if dt else "—"


def parse_date(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            value = str(int(value))
        s = str(value).strip()
        if re.fullmatch(r"\d{8}", s):
            return datetime.strptime(s, "%Y%m%d")
        dt = pd.to_datetime(s, errors="coerce")
        if pd.isna(dt):
            return None
        if getattr(dt, "tzinfo", None):
            dt = dt.tz_convert(None) if hasattr(dt, "tz_convert") else dt.replace(tzinfo=None)
        return dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt
    except Exception:
        return None


def haversine_miles(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    except Exception:
        return None
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def maps_links(address: str):
    q = quote_plus(address)
    return {
        "Redfin": f"https://www.redfin.com/search#search_location={q}",
        "Zillow": f"https://www.zillow.com/homes/{q}_rb/",
        "Google Maps": f"https://www.google.com/maps/search/?api=1&query={q}",
        "Street View": f"https://www.google.com/maps/search/?api=1&query={q}",
        "Satellite": f"https://www.google.com/maps/search/?api=1&query={q}&basemap=satellite",
    }


def clean_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def call_link(phone: str):
    digits = clean_phone(phone)
    return f"tel:+1{digits}" if digits else ""


def sms_link(phone: str, body: str = ""):
    digits = clean_phone(phone)
    if not digits:
        return ""
    if body:
        return f"sms:+1{digits}&body={quote_plus(body)}"
    return f"sms:+1{digits}"


def is_likely_investor(name: str) -> bool:
    if not name:
        return False
    keywords = ["LLC", "INC", "CORP", "TRUST", "HOLDINGS", "INVEST", "CAPITAL", "PROPERTIES", "HOMES", "REALTY", "PARTNERS", "LP", "L.P."]
    upper = name.upper()
    return any(k in upper for k in keywords)


def realie_headers():
    return {"Authorization": REALIE_API_KEY, "Accept": "application/json"}


def realie_get(path: str, params: dict):
    if not REALIE_API_KEY:
        return None, "Missing REALIE_API_KEY in Streamlit Secrets."
    url = f"{REALIE_BASE}{path}"
    r = requests.get(url, headers=realie_headers(), params=params, timeout=45)
    if r.status_code != 200:
        return None, f"Realie API error {r.status_code}: {r.text[:500]}"
    try:
        return r.json(), None
    except Exception:
        return None, f"Realie returned non-JSON response: {r.text[:500]}"


def parse_input_address(full_address: str):
    """Small parser for normal US address input. User can override in sidebar if needed."""
    s = normalize_address(full_address)
    unit = ""
    # Extract #1, Unit 1, Apt 1, Ste 1
    m = re.search(r"(?:#|\bunit\b|\bapt\b|\bapartment\b|\bste\b|\bsuite\b)\s*([A-Za-z0-9-]+)", s, flags=re.I)
    if m:
        unit = m.group(1).strip()
        s = (s[:m.start()] + s[m.end():]).strip(" ,")
    parts = [p.strip() for p in s.split(",")]
    street = parts[0] if parts else s
    city = parts[1] if len(parts) > 1 else ""
    state = ""
    zip_code = ""
    if len(parts) > 2:
        m2 = re.search(r"\b([A-Z]{2})\b\s*(\d{5})?", parts[2].upper())
        if m2:
            state = m2.group(1)
            zip_code = m2.group(2) or ""
    return {"street": street, "unit": unit, "city": city, "state": state, "zip": zip_code}


def realie_property_search(state, address, county="", city="", unit="", limit=10):
    # Try both original and expanded street suffix. Unit sometimes prevents matching, so try without unit too.
    attempts = []
    for addr in [address, expand_street_suffix(address)]:
        if not addr:
            continue
        base = {"state": state, "address": addr, "residential": "true", "limit": limit}
        if county:
            base["county"] = county
        if city:
            base["city"] = city
        if unit:
            with_unit = dict(base)
            with_unit["unitNumberStripped"] = unit
            attempts.append(with_unit)
        attempts.append(base)

    last_error = None
    for params in attempts:
        data, err = realie_get("/property/search/", params)
        if err:
            last_error = err
            continue
        props = (data or {}).get("properties") or []
        if props:
            return props, params, None
        last_error = "No properties found."
    return [], attempts[-1] if attempts else {}, last_error


def subject_property_type(prop: dict):
    if not prop:
        return "unknown", "any"
    if prop.get("condo") is True:
        return "Condo", "condo"
    # Realie premium docs accept any/condo/house. Treat residential non-condo as house.
    if prop.get("residential") is True:
        return "House / SFR", "house"
    return "Unknown", "any"


def choose_subject_property(properties, requested_unit=""):
    if not properties:
        return None
    if requested_unit:
        unit_clean = str(requested_unit).strip().lower()
        for p in properties:
            if str(p.get("unitNumberStripped", "")).strip().lower() == unit_clean:
                return p
            if f"unit {unit_clean}" in str(p.get("legalDesc", "")).lower():
                return p
    # Prefer record with modelValue and coordinates.
    scored = []
    for p in properties:
        score = 0
        if p.get("modelValue"):
            score += 10
        if p.get("latitude") and p.get("longitude"):
            score += 5
        if p.get("transferPrice"):
            score += 2
        scored.append((score, p))
    return sorted(scored, key=lambda x: x[0], reverse=True)[0][1]


def get_sale_price_date(comp):
    """Pick a verified-looking non-zero transfer/sale pair from Realie."""
    candidates = []
    # Prefer explicit top-level transfer price/date.
    for price_key, date_key in [("transferPrice", "transferDate"), ("salePriceLastTransfer", "transferDate"), ("pastPriceTransfer", "pastSaleDateTransfer"), ("pastPriceSale", "priorSalesDate")]:
        price = comp.get(price_key)
        date = comp.get(date_key)
        if price and float(price or 0) > 0 and date:
            candidates.append((parse_date(date), float(price), price_key, date_key))
    # Also inspect transfers array.
    for t in comp.get("transfers") or []:
        price = t.get("transferPrice")
        date = t.get("transferDate") or t.get("transferDateObject")
        doc = str(t.get("transferDocType") or "").upper()
        if price and float(price or 0) > 0 and date:
            bonus = 1 if doc == "GD" else 0
            candidates.append((parse_date(date), float(price), f"transfer:{doc}", "transfers"))
    candidates = [c for c in candidates if c[0] is not None]
    if not candidates:
        return None, None, ""
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    dt, price, source, _ = candidates[0]
    return price, dt, source


def realie_premium_comps(lat, lon, subject, radius, months, property_type, sqft_tolerance=300, exact_beds=True, exact_baths=True):
    sqft = subject.get("livingArea") or subject.get("buildingArea")
    beds = subject.get("totalBedrooms")
    baths = subject.get("totalBathrooms")
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "radius": radius,
        "timeFrame": months,
        "maxResults": 50,
        "propertyType": property_type,
    }
    if sqft:
        params["sqftMin"] = max(0, int(float(sqft) - sqft_tolerance))
        params["sqftMax"] = int(float(sqft) + sqft_tolerance)
    if exact_beds and beds is not None:
        params["bedsMin"] = int(float(beds))
        params["bedsMax"] = int(float(beds))
    if exact_baths and baths is not None:
        params["bathsMin"] = int(float(baths))
        params["bathsMax"] = int(float(baths))

    data, err = realie_get("/premium/comparables/", params)
    if err:
        # 404 No comparable properties found is not fatal.
        if "No comparable" in err or "404" in err:
            return [], params, None
        return [], params, err
    return (data or {}).get("comparables") or [], params, None


def validate_and_score_comps(raw_comps, subject, property_type_label, max_months, max_radius, sqft_tolerance=300):
    now = datetime.now()
    cutoff = now - timedelta(days=max_months * 30)
    s_lat, s_lon = subject.get("latitude"), subject.get("longitude")
    s_sqft = subject.get("livingArea") or subject.get("buildingArea")
    s_beds = subject.get("totalBedrooms")
    s_baths = subject.get("totalBathrooms")
    s_year = subject.get("yearBuilt")
    s_sub = str(subject.get("subdivision") or "").upper()
    s_parcel = subject.get("parcelId")

    cleaned = []
    seen = set()
    for c in raw_comps:
        price, sale_date, source = get_sale_price_date(c)
        if not price or not sale_date:
            continue
        if sale_date < cutoff:
            continue
        if c.get("parcelId") == s_parcel:
            continue
        c_sqft = c.get("livingArea") or c.get("buildingArea")
        if s_sqft and c_sqft and abs(float(c_sqft) - float(s_sqft)) > sqft_tolerance:
            continue
        if s_beds is not None and c.get("totalBedrooms") is not None and int(float(c.get("totalBedrooms"))) != int(float(s_beds)):
            continue
        if s_baths is not None and c.get("totalBathrooms") is not None and float(c.get("totalBathrooms")) != float(s_baths):
            continue
        if property_type_label == "Condo" and c.get("condo") is not True:
            continue
        if property_type_label != "Condo" and property_type_label != "Unknown" and c.get("condo") is True:
            continue
        dist = haversine_miles(s_lat, s_lon, c.get("latitude"), c.get("longitude"))
        if dist is not None and dist > max_radius:
            continue
        key = (c.get("parcelId"), int(price), sale_date.strftime("%Y%m%d"))
        if key in seen:
            continue
        seen.add(key)

        score = 0
        # Same property type
        score += 30
        # Same subdivision / complex
        if s_sub and str(c.get("subdivision") or "").upper() == s_sub:
            score += 25
        # Distance
        if dist is not None:
            score += max(0, 20 - (dist / max(max_radius, 0.01) * 20))
        # Sqft similarity
        if s_sqft and c_sqft:
            diff = abs(float(c_sqft) - float(s_sqft))
            score += max(0, 20 - (diff / sqft_tolerance * 20))
        # Recency
        days_old = (now - sale_date).days
        score += max(0, 20 - (days_old / max(max_months * 30, 1) * 20))
        # Year built
        if s_year and c.get("yearBuilt"):
            score += max(0, 10 - (abs(float(c.get("yearBuilt")) - float(s_year)) / 20 * 10))
        # Exact bed/bath already filtered; reward
        score += 20

        c2 = dict(c)
        c2["_sale_price"] = price
        c2["_sale_date"] = sale_date
        c2["_sale_source"] = source
        c2["_distance"] = dist
        c2["_score"] = round(score, 1)
        c2["_price_per_sqft"] = price / float(c_sqft) if c_sqft else None
        cleaned.append(c2)

    cleaned.sort(key=lambda x: (x["_score"], x["_sale_date"]), reverse=True)
    return cleaned


def progressive_comp_search(subject, property_type_api, property_type_label, min_comps):
    lat, lon = subject.get("latitude"), subject.get("longitude")
    if not lat or not lon:
        return [], [], "Missing subject latitude/longitude."

    # Starts strict: same type, 0.5 miles, 6 months, ±300 sqft, exact beds/baths.
    rounds = [
        {"radius": 0.5, "months": 6, "sqft_tol": 300, "label": "Strict: 0.5 mi / 6 months / ±300 sqft"},
        {"radius": 0.5, "months": 12, "sqft_tol": 300, "label": "Fallback: 0.5 mi / 12 months / ±300 sqft"},
        {"radius": 1.0, "months": 12, "sqft_tol": 300, "label": "Fallback: 1.0 mi / 12 months / ±300 sqft"},
        {"radius": 1.0, "months": 18, "sqft_tol": 300, "label": "Fallback: 1.0 mi / 18 months / ±300 sqft"},
    ]
    diagnostics = []
    best = []
    for rule in rounds:
        raw, params, err = realie_premium_comps(
            lat, lon, subject,
            radius=rule["radius"],
            months=rule["months"],
            property_type=property_type_api,
            sqft_tolerance=rule["sqft_tol"],
            exact_beds=True,
            exact_baths=True,
        )
        valid = validate_and_score_comps(raw, subject, property_type_label, rule["months"], rule["radius"], rule["sqft_tol"])
        diagnostics.append({"round": rule["label"], "raw_returned": len(raw), "valid_after_filter": len(valid), "params": params, "error": err})
        if len(valid) > len(best):
            best = valid
        if len(valid) >= min_comps:
            return valid, diagnostics, None
    return best, diagnostics, None


def weighted_arv(comps, subject_sqft, top_n=5):
    if not comps:
        return None, None, None
    usable = [c for c in comps[:top_n] if c.get("_price_per_sqft") and c.get("_score")]
    if not usable:
        prices = [c.get("_sale_price") for c in comps[:top_n] if c.get("_sale_price")]
        return (sum(prices) / len(prices) if prices else None), None, None
    total_weight = sum(max(c["_score"], 1) for c in usable)
    weighted_psf = sum(c["_price_per_sqft"] * max(c["_score"], 1) for c in usable) / total_weight
    arv = weighted_psf * float(subject_sqft) if subject_sqft else None
    median_price = float(pd.Series([c["_sale_price"] for c in usable]).median())
    return arv, weighted_psf, median_price


def confidence_score(comps):
    if not comps:
        return 0
    n = min(len(comps), 5)
    avg_score = sum(c.get("_score", 0) for c in comps[:5]) / n
    count_bonus = min(15, len(comps) * 3)
    return int(min(99, max(0, avg_score * 0.55 + count_bonus)))


def render_contact_actions(phone, label="Contact", sms_body=""):
    tel = call_link(phone)
    sms = sms_link(phone, sms_body)
    if not tel:
        st.caption(f"No phone loaded for {label} yet.")
        return
    st.markdown(f"[📞 Call {label}]({tel}) &nbsp;&nbsp; [💬 SMS {label}]({sms})", unsafe_allow_html=True)

# -------------------------
# UI
# -------------------------
st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("Realie-powered V2: accurate property type lock → verified comps → weighted ARV → MAO")

with st.sidebar:
    st.header("Settings")
    st.success("Primary Data Source: Realie")
    st.caption("RentCast is no longer used for final comps.")
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before fallback", min_value=1, max_value=10, value=3)
    show_debug = st.toggle("Show API diagnostics", value=False)
    st.divider()
    st.subheader("Contact Actions")
    st.caption("Call/SMS links open your Mac/iPhone default calling or Messages app.")

col1, col2 = st.columns([2, 1])
with col1:
    address = st.text_input("Property Address", value="1342 Branham Ln #1, San Jose, CA 95118")
    parsed = parse_input_address(address)
    with st.expander("Address details / override", expanded=False):
        street = st.text_input("Street only", value=parsed["street"])
        unit = st.text_input("Unit only", value=parsed["unit"])
        city = st.text_input("City", value=parsed["city"] or "San Jose")
        county = st.text_input("County", value="Santa Clara")
        state = st.text_input("State", value=parsed["state"] or "CA")
    seller_phone = st.text_input("Seller Phone (optional, for Call/SMS buttons)", placeholder="8182534663")
    photo_link = st.text_input("Dropbox / Google Drive Photo Link", placeholder="Paste photo folder link here")
with col2:
    uploaded_photos = st.file_uploader("Upload Property Photos", accept_multiple_files=True, type=["png", "jpg", "jpeg", "webp"])

if uploaded_photos:
    st.write("Photo preview")
    cols = st.columns(min(4, len(uploaded_photos)))
    for i, file in enumerate(uploaded_photos[:4]):
        cols[i % len(cols)].image(file, use_container_width=True)

analyze = st.button("Analyze Property", type="primary", use_container_width=True)

if analyze:
    if not REALIE_API_KEY:
        st.error("Missing REALIE_API_KEY in Streamlit Secrets. Add it before using Analyze Property.")
        st.stop()

    with st.spinner("Searching Realie property data..."):
        props, params_used, err = realie_property_search(state, street, county=county, city=city, unit=unit, limit=10)

    if err and not props:
        st.error(err)
        st.info("Try spelling out the street suffix, for example Lane instead of Ln.")
        st.stop()

    subject = choose_subject_property(props, requested_unit=unit)
    if not subject:
        st.error("No subject property found.")
        st.stop()

    property_type_label, property_type_api = subject_property_type(subject)
    subject_sqft = subject.get("livingArea") or subject.get("buildingArea")
    subject_addr = subject.get("addressFull") or address

    with st.spinner("Finding strict same-property-type sold comps..."):
        comps, diagnostics, comp_err = progressive_comp_search(subject, property_type_api, property_type_label, min_comps)

    # Header / status
    st.subheader("Subject Property")
    st.markdown(
        f"""
        <div class="card">
        <b>{subject_addr}</b><br>
        <span class="pill">Property Type Locked: {property_type_label}</span>
        <span class="pill">Parcel: {subject.get('parcelId') or '—'}</span>
        <span class="pill">Subdivision: {subject.get('subdivision') or '—'}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Beds", subject.get("totalBedrooms") or "—")
    c2.metric("Baths", subject.get("totalBathrooms") or "—")
    c3.metric("House SqFt", number(subject_sqft))
    c4.metric("Lot SqFt", number(subject.get("lotSizeArea") or subject.get("landArea")))
    c5.metric("Year Built", subject.get("yearBuilt") or "—")

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Owner", subject.get("ownerName") or "—")
    o2.metric("Est. Equity", money(subject.get("equityCurrentEstBal")))
    o3.metric("Lien Balance", money(subject.get("totalLienBalance")))
    o4.metric("Realie AVM", money(subject.get("modelValue")))

    if seller_phone:
        st.markdown("**Seller Call/SMS**")
        render_contact_actions(seller_phone, "Seller", f"Hi, this is Marco with Newcastle Partners. I wanted to follow up about {subject_addr}.")

    if comp_err:
        st.error(comp_err)

    # ARV
    arv, weighted_psf, median_price = weighted_arv(comps, subject_sqft, top_n=5)
    conf = confidence_score(comps)

    st.subheader("Weighted ARV + Offer Matrix")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Recommended ARV", money(arv))
    a2.metric("Weighted $/SqFt", money(weighted_psf) if weighted_psf else "—")
    a3.metric("Median Top-Comp Price", money(median_price))
    a4.metric("ARV Confidence", f"{conf}%" if conf else "—")

    tiers = [("75% ARV", 0.75), ("70% ARV", 0.70), ("65% ARV", 0.65), ("60% ARV", 0.60)]
    tier_cols = st.columns(4)
    for col, (label, pct) in zip(tier_cols, tiers):
        gross = (arv * pct) if arv else None
        net = (gross - repair_estimate) if gross else None
        col.metric(label, money(gross))
        col.caption(f"After repairs: {money(net)}")

    st.subheader("Verified Sold Comparable Sales")
    st.caption("Rules: same property type only, starts with 0.5 mi / 6 months / ±300 sqft, then falls back only if fewer than your minimum comp count are found.")

    if not comps:
        st.warning("No comps passed the strict Realie filters. Check diagnostics or manually widen criteria.")
    else:
        rows = []
        for c in comps:
            comp_addr = c.get("addressFull") or c.get("addressUnit") or c.get("address") or c.get("addressRaw") or "Unknown"
            links = maps_links(comp_addr)
            buyer = c.get("grantee") or c.get("ownerName") or "—"
            rows.append({
                "Match Score": c.get("_score"),
                "Sold Date": fmt_date(c.get("_sale_date")),
                "Address": comp_addr,
                "Distance": f"{c.get('_distance'):.2f} mi" if c.get("_distance") is not None else "—",
                "Beds": c.get("totalBedrooms") or "—",
                "Baths": c.get("totalBathrooms") or "—",
                "House SqFt": number(c.get("livingArea") or c.get("buildingArea")),
                "Lot SqFt": number(c.get("lotSizeArea") or c.get("landArea")),
                "Recorded Sale Price": money(c.get("_sale_price")),
                "$/SqFt": money(c.get("_price_per_sqft")),
                "Buyer / Grantee": buyer,
                "Investor?": "YES" if is_likely_investor(str(buyer)) else "NO",
                "Sale Source": c.get("_sale_source"),
                "Redfin": links["Redfin"],
                "Zillow": links["Zillow"],
                "Map": links["Google Maps"],
                "Street View": links["Street View"],
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            column_config={
                "Redfin": st.column_config.LinkColumn("Redfin", display_text="Open"),
                "Zillow": st.column_config.LinkColumn("Zillow", display_text="Open"),
                "Map": st.column_config.LinkColumn("Map", display_text="Map"),
                "Street View": st.column_config.LinkColumn("Street", display_text="Street"),
            },
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("AI Acquisition Notes")
    if arv and comps:
        top = comps[:min(5, len(comps))]
        avg_dist = sum([c.get("_distance") or 0 for c in top]) / len(top)
        st.markdown(f"""
        <div class="card">
        <b>Recommendation:</b> Review top {len(top)} Realie comps before making the final offer.<br><br>
        <b>Property type lock:</b> {property_type_label} comps only. No silent mixing with other property types.<br><br>
        <b>Comp quality:</b> {len(comps)} verified sales passed filters. Average top-comp distance: {avg_dist:.2f} miles. Confidence: {conf}%.<br><br>
        <b>Starting offer guidance:</b> Use the 70% ARV tier after repairs as your normal wholesale starting point, then adjust based on seller motivation and condition/photos.<br><br>
        <b>Next feature:</b> Buyer phone/email enrichment can populate the Call/SMS buttons directly in the buyer table once we connect a supported contact provider.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Need valid Realie comps to generate full AI notes.")

    if show_debug:
        st.subheader("API Diagnostics")
        st.json({"property_search_params_used": params_used, "chosen_subject": subject, "comp_rounds": diagnostics})

# Basic standalone contact launcher section
st.divider()
st.subheader("Quick Call / SMS Launcher")
st.caption("Enter any phone number and click Call or SMS. This opens the default phone or Messages app on your Mac/iPhone.")
quick_cols = st.columns([2, 3])
with quick_cols[0]:
    quick_phone = st.text_input("Phone number", key="quick_phone", placeholder="8182534663")
with quick_cols[1]:
    quick_msg = st.text_input("SMS message", key="quick_msg", placeholder="Hi, this is Marco with Newcastle Partners...")
if clean_phone(quick_phone):
    st.markdown(f"[📞 Call](tel:+1{clean_phone(quick_phone)}) &nbsp;&nbsp; [💬 SMS](sms:+1{clean_phone(quick_phone)}&body={quote_plus(quick_msg)})", unsafe_allow_html=True)
