import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle AI Analyzer", layout="wide", page_icon="🏠")

REALIE_BASE = "https://app.realie.ai/api/public"

# Fillout field map placeholder. Replace values with exact Fillout field IDs/names later.
FILLOUT_FIELD_MAP = {
    "seller_name": "seller_name",
    "seller_phone": "seller_phone",
    "property_address": "property_address",
    "apn": "apn",
    "purchase_price": "purchase_price",
    "buyer_entity": "buyer_entity",
    "inspection_period": "inspection_period",
    "closing_period": "closing_period",
    "escrow_company": "escrow_company",
    "additional_terms": "additional_terms",
}

DEFAULT_ESCROW = {
    "CA": "Chicago Title",
    "TX": "Title company TBD",
    "NV": "Title company TBD",
    "TN": "Title company TBD",
}


def money(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        return f"${float(v):,.0f}"
    except Exception:
        return "—"


def fmt_date(v):
    if not v:
        return "—"
    s = str(v)
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").strftime("%m/%d/%Y")
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        pass
    return s


def parse_date(v):
    if not v:
        return None
    s = str(v)
    try:
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d")
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
    return None


def safe_float(v, default=None):
    try:
        if v in [None, "", "—"]:
            return default
        return float(v)
    except Exception:
        return default


def normalize_address(full):
    """Accept natural addresses and normalize to Realie-friendly fields.
    Handles: "1342 Branham Ln #1, San Jose, CA 95118" and
    "1409 San Juan Ave Stockton CA 95203".
    """
    raw = (full or "").strip()
    raw = re.sub(r"\s+", " ", raw)
    unit = ""
    m = re.search(r"(?:#|unit\s+|apt\s+|apartment\s+|ste\s+)([A-Za-z0-9-]+)", raw, flags=re.I)
    if m:
        unit = m.group(1).strip()
        raw = re.sub(r"\s*(?:#|unit\s+|apt\s+|apartment\s+|ste\s+)[A-Za-z0-9-]+", "", raw, flags=re.I)

    state = "CA"
    zip_code = ""
    city = ""
    street = raw

    # Best path: comma separated street, city, state zip
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 2:
        street = parts[0]
        city = parts[1]
        if len(parts) >= 3:
            m2 = re.search(r"\b([A-Z]{2})\b\s*(\d{5})?", parts[2], flags=re.I)
            if m2:
                state = m2.group(1).upper()
                zip_code = m2.group(2) or ""
    else:
        # No commas: try to find state + ZIP at the end, then split known cities.
        m2 = re.search(r"\b([A-Z]{2})\b\s*(\d{5})\s*$", raw, flags=re.I)
        working = raw
        if m2:
            state = m2.group(1).upper()
            zip_code = m2.group(2) or ""
            working = raw[:m2.start()].strip().rstrip(',')
        known_cities = [
            "san jose", "stockton", "modesto", "fresno", "bakersfield", "turlock", "manteca",
            "merced", "visalia", "tulare", "madera", "sacramento", "lodi", "woodland",
            "yuba city", "oakland", "san bruno", "south san francisco", "los angeles", "encino",
        ]
        lw = working.lower()
        for c in sorted(known_cities, key=len, reverse=True):
            token = " " + c
            if token in lw:
                idx = lw.rfind(token)
                street = working[:idx].strip().rstrip(',')
                city = working[idx:].strip().strip(',')
                break
        else:
            street = working

    # Realie seems to match better when common suffixes are spelled out.
    replacements = {
        r"\bLn\b": "Lane", r"\bAve\b": "Avenue", r"\bDr\b": "Drive", r"\bSt\b": "Street",
        r"\bRd\b": "Road", r"\bCt\b": "Court", r"\bPl\b": "Place", r"\bBlvd\b": "Boulevard",
        r"\bWay\b": "Way",
    }
    for pat, rep in replacements.items():
        street = re.sub(pat, rep, street, flags=re.I)
    return {"street": street.strip(), "unit": unit, "city": city.strip().title(), "state": state, "zip": zip_code}


def realie_headers():
    key = st.secrets.get("REALIE_API_KEY", "")
    return {"Authorization": key, "Content-Type": "application/json"}


def realie_get(path, params):
    url = f"{REALIE_BASE}{path}"
    r = requests.get(url, headers=realie_headers(), params=params, timeout=30)
    try:
        js = r.json()
    except Exception:
        js = {"raw": r.text[:2000]}
    return r.status_code, url, js


def find_subject(address_text, overrides=None):
    parsed = normalize_address(address_text)
    if overrides:
        parsed.update({k: v for k, v in overrides.items() if v})
    base_params = {
        "state": parsed.get("state") or "CA",
        "address": parsed.get("street"),
        "residential": "true",
        "limit": 25,
    }
    if parsed.get("unit"):
        base_params["unitNumberStripped"] = parsed["unit"]
    if parsed.get("city"):
        base_params["city"] = parsed["city"]
    if overrides and overrides.get("county"):
        base_params["county"] = overrides["county"]

    attempts = [base_params.copy()]
    # fallback without unit, then with city omitted
    p2 = base_params.copy(); p2.pop("unitNumberStripped", None); attempts.append(p2)
    p3 = p2.copy(); p3.pop("city", None); attempts.append(p3)

    last = None
    for params in attempts:
        code, url, js = realie_get("/property/search/", params)
        last = (code, url, params, js)
        props = js.get("properties") or js.get("data") or js.get("results") or []
        if isinstance(props, dict):
            props = [props]
        if props:
            # prefer unit match if requested
            unit = parsed.get("unit")
            if unit:
                for p in props:
                    if str(p.get("unitNumberStripped", "")).lower() == str(unit).lower() or f"UNIT {unit}" in str(p.get("legalDesc", "")).upper():
                        return p, last, parsed
            return props[0], last, parsed
    return None, last, parsed


def prop_type(prop):
    if prop.get("condo"):
        return "condo"
    # crude multifamily hints
    uc = str(prop.get("useCode", ""))
    legal = str(prop.get("legalDesc", "")).lower()
    if "duplex" in legal:
        return "duplex"
    return "house"


def best_sale(comp):
    """Return the most reliable arm's-length sale date/price.
    We avoid using internal/non-sale transfers such as IT/QC as comps.
    """
    sale_doc_types = {"GD", "WD", "SWD", "BARG", "GRANT", "DEED"}
    bad_doc_types = {"IT", "QC", "QCD", "TR", "TD", "MTG", "REL", "AFF"}
    transfers = comp.get("transfers") or []
    candidates = []
    for t in transfers:
        price = safe_float(t.get("transferPrice"), 0)
        dt = parse_date(t.get("transferDateObject") or t.get("transferDate"))
        doc = str(t.get("transferDocType", "")).upper()
        if price and price > 10000 and dt and doc not in bad_doc_types:
            # Prefer grant/warranty sale deeds; allow blank/unknown only if price exists.
            score = 2 if doc in sale_doc_types else 1
            candidates.append((score, dt, price, t))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][1], candidates[0][2], candidates[0][3]

    # Last-sale fields only when the last transfer doc appears sale-like.
    last_doc = str(comp.get("saleDocumentTypeLastSale") or comp.get("transferDocType") or "").upper()
    if last_doc not in bad_doc_types:
        for price_key, date_key in [
            ("salePriceLastTransfer", "ownershipStartDate"),
            ("transferPrice", "transferDateObject"),
            ("pastPriceSale", "priorSalesDate"),
        ]:
            price = safe_float(comp.get(price_key), 0)
            dt = parse_date(comp.get(date_key))
            if price and price > 10000 and dt:
                return dt, price, {}
    return None, None, {}


def months_ago(dt):
    if not dt:
        return 9999
    now = datetime.utcnow()
    return (now.year - dt.year) * 12 + (now.month - dt.month)


def distance_miles(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]:
        return None
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2-lat1)
    dlambda = math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))


def get_comps(subject, min_needed=3):
    """Pull comps using strict rules first.
    Business rule: do not show anything older than 6 months unless there are 3 or fewer valid comps.
    Same property type stays locked; no silent mixing with other property types.
    """
    lat = safe_float(subject.get("latitude"))
    lon = safe_float(subject.get("longitude"))
    sqft = safe_float(subject.get("livingArea") or subject.get("buildingArea"), 0)
    beds = int(safe_float(subject.get("totalBedrooms"), 0) or 0)
    baths = int(safe_float(subject.get("totalBathrooms"), 0) or 0)
    ptype = prop_type(subject)
    base = {
        "latitude": lat, "longitude": lon, "maxResults": 75,
        "propertyType": ptype,
        "sqftMin": max(0, sqft-300), "sqftMax": sqft+300,
        "bedsMin": beds, "bedsMax": beds,
        "bathsMin": baths, "bathsMax": baths,
    }
    # Pull enough data to verify, but final visible set is filtered below.
    params_list = [
        {**base, "radius": 0.5, "timeFrame": 6},
        {**base, "radius": 1.0, "timeFrame": 6},
        {**base, "radius": 1.0, "timeFrame": 12},
    ]
    all_comps = []
    debug = []
    for params in params_list:
        code, url, js = realie_get("/premium/comparables/", params)
        debug.append({"status": code, "url": url, "params": params, "preview": str(js)[:500]})
        comps = js.get("comparables") or []
        all_comps.extend(comps)

    dedup = {}
    for c in all_comps:
        dt, price, _ = best_sale(c)
        if not dt or not price:
            continue
        # Hard lock property type.
        if prop_type(c) != ptype:
            continue
        key = (c.get("parcelId"), dt.strftime("%Y%m%d"), int(price or 0))
        dedup[key] = c
    comps = list(dedup.values())
    return comps, "Strict: same property type / ±300 sqft / 6 months preferred / 12 months only if needed", debug


def score_comp(subject, comp):
    s_sqft = safe_float(subject.get("livingArea") or subject.get("buildingArea"), 0)
    c_sqft = safe_float(comp.get("livingArea") or comp.get("buildingArea"), 0)
    s_beds = safe_float(subject.get("totalBedrooms"), 0)
    c_beds = safe_float(comp.get("totalBedrooms"), 0)
    s_baths = safe_float(subject.get("totalBathrooms"), 0)
    c_baths = safe_float(comp.get("totalBathrooms"), 0)
    s_year = safe_float(subject.get("yearBuilt"), 0)
    c_year = safe_float(comp.get("yearBuilt"), 0)
    dist = distance_miles(safe_float(subject.get("latitude")), safe_float(subject.get("longitude")), safe_float(comp.get("latitude")), safe_float(comp.get("longitude"))) or 9
    dt, price, _ = best_sale(comp)
    age_m = months_ago(dt)
    score = 0
    if prop_type(subject) == prop_type(comp): score += 25
    if c_beds == s_beds: score += 15
    if c_baths == s_baths: score += 10
    if s_sqft and c_sqft:
        diff = abs(c_sqft - s_sqft)
        score += max(0, 20 - (diff / 15))
    if s_year and c_year:
        score += max(0, 10 - abs(c_year - s_year) / 2)
    score += max(0, 20 - dist * 15)
    score += max(0, 20 - age_m * 1.5)
    # same subdivision / legal tract boost
    if subject.get("subdivision") and subject.get("subdivision") == comp.get("subdivision"):
        score += 20
    return round(min(100, score), 0)


def comp_rows(subject, comps):
    rows = []
    for c in comps:
        dt, price, sale = best_sale(c)
        if not price or not dt:
            continue
        sqft = safe_float(c.get("livingArea") or c.get("buildingArea"), 0)
        dist = distance_miles(safe_float(subject.get("latitude")), safe_float(subject.get("longitude")), safe_float(c.get("latitude")), safe_float(c.get("longitude")))
        buyer = c.get("grantee") or c.get("ownerName") or "—"
        rows.append({
            "Address": c.get("addressFullUSPS") or c.get("addressFull") or c.get("addressRaw") or "—",
            "Sold Date": fmt_date(dt.isoformat()),
            "_sold_dt": dt,
            "Sold Price": price,
            "$/SF": price / sqft if sqft else None,
            "SqFt": sqft,
            "Beds": c.get("totalBedrooms"),
            "Baths": c.get("totalBathrooms"),
            "Distance": dist,
            "Buyer / Current Owner": buyer,
            "Match": score_comp(subject, c),
            "_raw": c,
        })
    rows.sort(key=lambda r: r["_sold_dt"], reverse=True)
    return rows


def arv_tiers(rows):
    if not rows:
        return None
    scored = sorted(rows, key=lambda r: r["Match"], reverse=True)
    top = scored[:min(6, len(scored))]
    prices = [r["Sold Price"] for r in top if r.get("Sold Price")]
    if not prices:
        return None
    expected = sum(prices) / len(prices)
    conservative = sorted(prices)[max(0, int(len(prices)*0.25)-1)] if len(prices) >= 4 else min(prices)
    aggressive = sorted(prices)[min(len(prices)-1, int(len(prices)*0.75))] if len(prices) >= 4 else max(prices)
    conf = int(min(98, 55 + len(top)*5 + sum([r["Match"] for r in top])/len(top)*0.15))
    return {"quick_sale": conservative, "market": expected, "premium": aggressive, "confidence": conf, "used": top}


def tel_link(phone):
    digits = re.sub(r"\D", "", phone or "")
    return f"tel:{digits}" if digits else "#"


def sms_link(phone, msg):
    digits = re.sub(r"\D", "", phone or "")
    return f"sms:{digits}&body={quote(msg or '')}" if digits else "#"


def require_login():
    users = st.secrets.get("users", {})
    if "auth" not in st.session_state:
        st.session_state.auth = None
    if st.session_state.auth:
        return st.session_state.auth
    st.title("🏠 Newcastle AI Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login", use_container_width=True):
        if username in users and password == users[username].get("password"):
            st.session_state.auth = {"username": username, "role": users[username].get("role", "va")}
            st.rerun()
        else:
            st.error("Invalid login")
    st.stop()


def seed_leads():
    if "leads" not in st.session_state:
        st.session_state.leads = [
            {"id": 1, "address": "1409 San Juan Ave, Stockton, CA 95203", "seller": "", "phone": "8183004227", "status": "New", "assigned": "Unassigned", "notes": "", "lat": None, "lon": None},
        ]


def lead_queue(user):
    seed_leads()
    st.header("📥 Lead Queue")
    status_colors = {"New": "🔴", "In Progress": "🟠", "Follow Up": "🟡", "Hot": "🟢", "Under Contract": "🔵", "Dead": "⚫"}
    with st.expander("Add Lead", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            addr = st.text_input("Lead address", key="new_lead_addr")
            seller = st.text_input("Seller name", key="new_lead_seller")
        with c2:
            phone = st.text_input("Phone", key="new_lead_phone")
            assigned = st.selectbox("Assign to", ["Unassigned", "Marco", "Doreen"], key="new_lead_assigned")
        if st.button("Add to queue"):
            st.session_state.leads.append({"id": len(st.session_state.leads)+1, "address": addr, "seller": seller, "phone": phone, "status": "New", "assigned": assigned, "notes": "", "lat": None, "lon": None})
            st.rerun()
    for lead in st.session_state.leads:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            c1.subheader(f"{status_colors.get(lead['status'],'⚪')} {lead['address']}")
            c1.caption(f"Seller: {lead.get('seller') or '—'} | Assigned: {lead.get('assigned')}")
            lead["status"] = c2.selectbox("Status", list(status_colors.keys()), index=list(status_colors.keys()).index(lead["status"]), key=f"status_{lead['id']}")
            lead["assigned"] = c3.selectbox("Assigned", ["Unassigned", "Marco", "Doreen"], index=["Unassigned", "Marco", "Doreen"].index(lead.get("assigned", "Unassigned")), key=f"assigned_{lead['id']}")
            if c4.button("Analyze", key=f"analyze_{lead['id']}"):
                st.session_state.property_address = lead["address"]
                st.session_state.seller_phone = lead.get("phone", "")
                st.session_state.seller_name = lead.get("seller", "")
                st.session_state.page = "Analyze Property"
                st.rerun()
            lead["notes"] = st.text_area("Notes", value=lead.get("notes", ""), key=f"notes_{lead['id']}")


def lead_map(subject=None, rows=None):
    st.header("🗺️ Map View")
    data = []
    if subject:
        data.append({"lat": subject.get("latitude"), "lon": subject.get("longitude"), "label": "Subject", "type": "Subject", "price": None})
    for r in rows or []:
        raw = r.get("_raw", {})
        data.append({"lat": raw.get("latitude"), "lon": raw.get("longitude"), "label": r.get("Address"), "type": "Comp", "price": r.get("Sold Price")})
    df = pd.DataFrame([d for d in data if d.get("lat") and d.get("lon")])
    if df.empty:
        st.info("Analyze a property first to view subject and comps on the map.")
        return
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=float(df["lat"].mean()), longitude=float(df["lon"].mean()), zoom=13, pitch=0),
        layers=[pdk.Layer("ScatterplotLayer", data=df, get_position="[lon, lat]", get_radius=75, pickable=True)],
        tooltip={"text": "{type}: {label}\n{price}"}
    ))


def offer_composer(subject, tiers, seller_name, seller_phone, address):
    st.header("📄 Compose Offer")
    if not subject or not tiers:
        st.info("Analyze a property first, then compose an offer.")
        return
    c1, c2 = st.columns(2)
    with c1:
        buyer_entity = st.text_input("Buyer entity", value="Newcastle Partners CA LLC")
        seller_name = st.text_input("Seller name", value=seller_name or subject.get("ownerName", ""))
        purchase_price = st.number_input("Purchase price", min_value=0, value=int(tiers["quick_sale"] * 0.75), step=1000)
        emd = st.number_input("EMD", min_value=0, value=5000, step=500)
    with c2:
        inspection = st.selectbox("Inspection period", ["5 days", "7 days", "10 days", "15 days"], index=1)
        closing = st.selectbox("Closing period", ["7 days", "10 days", "14 days", "21 days", "30 days"], index=2)
        state = normalize_address(address).get("state", "CA")
        escrow = st.text_input("Escrow / Title", value=DEFAULT_ESCROW.get(state, "Title company TBD"))
        additional_terms = st.text_area("Additional terms", value="Buyer to purchase property as-is. Buyer to pay standard buyer closing costs unless otherwise agreed in writing.")
    payload = {
        "seller_name": seller_name,
        "seller_phone": seller_phone,
        "property_address": address,
        "apn": subject.get("parcelId") or subject.get("state_parcelId") or "",
        "purchase_price": purchase_price,
        "emd": emd,
        "buyer_entity": buyer_entity,
        "inspection_period": inspection,
        "closing_period": closing,
        "escrow_company": escrow,
        "additional_terms": additional_terms,
    }
    with st.expander("Review offer data before sending", expanded=True):
        st.json(payload)
    col1, col2 = st.columns(2)
    form_url = st.secrets.get("FILLOUT_FORM_URL", "https://newoffer.fillout.com/rpa")
    col1.link_button("Open Fillout Offer Form", form_url, use_container_width=True)
    if col2.button("Submit to Fillout API (requires field mapping)", use_container_width=True):
        api_key = st.secrets.get("FILLOUT_API_KEY", "")
        form_id = st.secrets.get("FILLOUT_FORM_ID", "")
        if not api_key or not form_id:
            st.error("Add FILLOUT_API_KEY and FILLOUT_FORM_ID in Streamlit Secrets first.")
        else:
            st.warning("Field mapping must be confirmed before live submission. Use the Review data above to map exact Fillout fields.")


def analyze_page(user):
    st.title("🏠 Newcastle AI Acquisition Analyzer")
    st.caption("Realie-powered: verified comps → value tiers → purchase guide → offer composer")
    if "property_address" not in st.session_state:
        st.session_state.property_address = "1342 Branham Ln #1, San Jose, CA 95118"
    address = st.text_input("Property Address", key="property_address")
    with st.expander("Address details / override"):
        o1, o2, o3, o4 = st.columns(4)
        street_o = o1.text_input("Street override", value="")
        unit_o = o2.text_input("Unit override", value="")
        city_o = o3.text_input("City override", value="")
        county_o = o4.text_input("County", value="")
    seller_name = st.text_input("Seller name", value=st.session_state.get("seller_name", ""))
    seller_phone = st.text_input("Seller Phone", value=st.session_state.get("seller_phone", ""))
    sms_template = st.text_area("SMS message", value=f"Hi {seller_name or 'there'}, this is Marco with Newcastle Partners. I was reaching out about {normalize_address(address).get('street')}. Are you still open to selling?")
    if seller_phone:
        st.markdown(f"[📞 Call Seller]({tel_link(seller_phone)}) &nbsp;&nbsp; [💬 SMS Seller]({sms_link(seller_phone, sms_template)})", unsafe_allow_html=True)
    uploaded = st.file_uploader("Upload Property Photos", accept_multiple_files=True, type=["png", "jpg", "jpeg", "webp"])
    if st.button("Analyze Property", type="primary", use_container_width=True):
        overrides = {"street": street_o, "unit": unit_o, "city": city_o, "county": county_o}
        with st.spinner("Searching property, pulling comps, ranking ARV..."):
            subject, req, parsed = find_subject(address, overrides)
            st.session_state.last_request = req
            if not subject:
                st.error("Property not found. Try spelling out Lane/Avenue/Drive or use Address details override.")
                return
            comps, rule, debug = get_comps(subject, min_needed=3)
            all_rows = comp_rows(subject, comps)
            six_month_rows = [r for r in all_rows if months_ago(r.get("_sold_dt")) <= 6]
            if len(six_month_rows) > 3:
                rows = six_month_rows
                visible_rule = "Strict: newest verified sales only / same property type / ±300 sqft / 6 months"
            else:
                rows = [r for r in all_rows if months_ago(r.get("_sold_dt")) <= 12]
                visible_rule = "Backup: 12 months used because 3 or fewer 6-month comps were found"
            tiers = arv_tiers(rows)
            st.session_state.analysis = {"subject": subject, "rows": rows, "tiers": tiers, "rule": visible_rule, "debug": debug}
    analysis = st.session_state.get("analysis")
    if analysis:
        subject = analysis["subject"]; rows = analysis["rows"]; tiers = analysis["tiers"]; rule = analysis["rule"]
        st.success("Analysis complete")
        st.header("Subject Property")
        c = st.columns(6)
        vals = [("Property Type", prop_type(subject).title()), ("Beds", subject.get("totalBedrooms")), ("Baths", subject.get("totalBathrooms")), ("House SqFt", subject.get("livingArea") or subject.get("buildingArea")), ("Lot SqFt", subject.get("lotSizeArea")), ("Year Built", subject.get("yearBuilt"))]
        for col, (label, val) in zip(c, vals):
            col.metric(label, val or "—")
        st.caption(f"Owner: {subject.get('ownerName','—')} | APN/Parcel: {subject.get('parcelId') or subject.get('state_parcelId') or '—'} | Legal: {subject.get('legalDesc','—')}")
        if tiers:
            st.header("📈 ARV Range")
            c = st.columns(4)
            c[0].metric("Conservative ARV", money(tiers["quick_sale"]))
            c[1].metric("Expected ARV", money(tiers["market"]))
            c[2].metric("Aggressive ARV", money(tiers["premium"]))
            c[3].metric("Confidence", f"{tiers['confidence']}%")
            st.caption(f"Comps used for ARV: {len(tiers['used'])} | Verified comps shown: {len(rows)} | Rule: {rule}")
            with st.expander("Optional purchase guide", expanded=False):
                st.caption("These are not buyer resale prices. They are quick acquisition reference points using the Conservative ARV. Final offer should account for repairs, fees, holding costs, and desired profit.")
                base = tiers["quick_sale"]
                labels = [("75% of Conservative ARV", .75), ("70% of Conservative ARV", .70), ("65% of Conservative ARV", .65), ("60% of Conservative ARV", .60)]
                cols = st.columns(4)
                for col, (label, pct) in zip(cols, labels):
                    col.metric(label, money(base * pct))
        st.header("Comparable Sales")
        st.caption("Same property type locked. Only 6-month comps are shown unless there are 3 or fewer, then 12-month backup is used. Sorted newest verified sale first.")
        display = []
        for r in rows[:50]:
            display.append({
                "Address": r["Address"], "Sold Date": r["Sold Date"], "Sold Price": money(r["Sold Price"]), "$/SF": money(r["$/SF"]),
                "SqFt": int(r["SqFt"] or 0), "Beds": r["Beds"], "Baths": r["Baths"], "Distance": f"{r['Distance']:.2f} mi" if r["Distance"] else "—",
                "Buyer / Current Owner": r["Buyer / Current Owner"], "Match": f"{int(r['Match'])}%"
            })
        st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)
        st.header("Clickable Comp Details")
        for idx, r in enumerate(rows[:10], start=1):
            with st.expander(f"{idx}. {r['Address']} — {money(r['Sold Price'])} — Match {int(r['Match'])}%"):
                raw = r["_raw"]
                st.write(f"**Buyer / Current Owner:** {r['Buyer / Current Owner']}")
                st.write(f"**APN/Parcel:** {raw.get('parcelId','—')}")
                st.write(f"**Mailing Address:** {raw.get('ownerAddressFull','—')}")
                st.write(f"**Lender:** {raw.get('lenderName','—')}")
                st.write(f"**Equity Estimate:** {money(raw.get('equityCurrentEstBal'))}")
                st.write(f"**Transfer Doc:** {raw.get('transferDocType','—')} / {raw.get('transferDocNum','—')}")
        lead_map(subject, rows)
        offer_composer(subject, tiers, seller_name, seller_phone, address)
    if st.session_state.get("show_api_diag", False) and st.session_state.get("last_request"):
        with st.expander("API diagnostics"):
            st.json(st.session_state.get("last_request"))


def main():
    user = require_login()
    with st.sidebar:
        st.write(f"Logged in: **{user['username']}** ({user['role']})")
        if st.button("Logout"):
            st.session_state.auth = None
            st.rerun()
        if user["role"] == "admin":
            st.session_state.show_api_diag = st.toggle("Show API diagnostics", value=st.session_state.get("show_api_diag", False))
    # Keep the app focused on the analyzer for now. No side navigation, no fallback/repair controls.
    analyze_page(user)


if __name__ == "__main__":
    main()
