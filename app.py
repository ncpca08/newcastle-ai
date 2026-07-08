import math
import re
from datetime import datetime, timedelta
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

RENTCAST_API_KEY = get_secret("RENTCAST_API_KEY")
DEALMACHINE_API_KEY = get_secret("DEALMACHINE_API_KEY")

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
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# HELPERS
# -------------------------
def money(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"${value:,.0f}"


def number(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return str(value)


def fmt_date(value):
    if not value:
        return "—"
    try:
        return pd.to_datetime(value).strftime("%m/%d/%Y")
    except Exception:
        return str(value)


def parse_date(value):
    if not value:
        return None
    try:
        dt = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(dt):
            return None
        # Normalize to timezone-naive datetime so Streamlit/RentCast date formats compare safely.
        return dt.tz_convert(None).to_pydatetime() if hasattr(dt, "tz_convert") else dt.to_pydatetime()
    except Exception:
        try:
            return pd.to_datetime(value, errors="coerce").to_pydatetime()
        except Exception:
            return None


def normalize_address(address: str) -> str:
    return re.sub(r"\s+", " ", address.strip())


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(safe_float, [lat1, lon1, lat2, lon2])
    if None in [lat1, lon1, lat2, lon2]:
        return None
    r = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def owner_name(record):
    if not isinstance(record, dict):
        return None
    owner = record.get("owner")
    if isinstance(owner, dict):
        names = owner.get("names") or []
        if names:
            return ", ".join([str(n) for n in names if n])
        if owner.get("name"):
            return owner.get("name")
    return comp_field(record, "buyerName", "buyer", "ownerName", "currentOwnerName")


def latest_sale(record):
    """Return most recent sale date/price from direct fields or RentCast history."""
    sale_date = comp_field(record, "soldDate", "lastSaleDate", "saleDate", "closeDate")
    sale_price = comp_field(record, "soldPrice", "lastSalePrice", "salePrice", "price")
    history = record.get("history") if isinstance(record, dict) else None
    if isinstance(history, dict) and history:
        sale_events = []
        for key, event in history.items():
            if not isinstance(event, dict):
                continue
            d = event.get("date") or key
            dt = parse_date(d)
            price = event.get("price")
            if dt and price:
                sale_events.append((dt, d, price))
        if sale_events:
            sale_events.sort(key=lambda x: x[0], reverse=True)
            sale_date = sale_date or sale_events[0][1]
            sale_price = sale_price or sale_events[0][2]
    return sale_date, sale_price


def same_property_address(a, b):
    def clean(x):
        return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
    return clean(a) and clean(a) == clean(b)


def maps_links(address: str):
    q = quote_plus(address)
    return {
        "Redfin": f"https://www.redfin.com/search#search_location={q}",
        "Zillow": f"https://www.zillow.com/homes/{q}_rb/",
        "Map": f"https://www.google.com/maps/search/?api=1&query={q}",
        "Street View": f"https://www.google.com/maps/@?api=1&map_action=pano&query={q}",
        "Satellite": f"https://www.google.com/maps/search/?api=1&query={q}&basemap=satellite",
    }


def is_likely_investor(name: str) -> bool:
    if not name:
        return False
    keywords = [
        "LLC", "INC", "CORP", "TRUST", "HOLDINGS", "INVEST", "CAPITAL",
        "PROPERTIES", "HOMES", "REALTY", "PARTNERS", "LP", "L.P.", "GROUP"
    ]
    upper = str(name).upper()
    return any(k in upper for k in keywords)


def comp_field(comp, *keys):
    for key in keys:
        if isinstance(comp, dict) and comp.get(key) is not None:
            return comp.get(key)
    return None

# -------------------------
# PROPERTY TYPE LOGIC
# -------------------------
def raw_property_type(record: dict) -> str:
    if not isinstance(record, dict):
        return ""
    fields = [
        "propertyType", "propertySubType", "propertyUse", "type", "buildingType",
        "formattedPropertyType", "category", "zoningDescription"
    ]
    values = [str(record.get(f, "")) for f in fields if record.get(f)]
    return " ".join(values).strip()


def property_family(record: dict) -> str:
    """Normalize property types so comps are compared apples-to-apples."""
    raw = raw_property_type(record).upper()
    units = comp_field(record, "units", "unitCount", "numberOfUnits", "totalUnits")

    if any(x in raw for x in ["CONDO", "CONDOMINIUM"]):
        return "Condo"
    if any(x in raw for x in ["TOWNHOUSE", "TOWNHOME", "PUD"]):
        return "Townhome"
    if any(x in raw for x in ["DUPLEX", "TRIPLEX", "FOURPLEX", "QUAD", "MULTI", "APARTMENT", "2-4", "MULTIFAMILY"]):
        return "Multifamily"
    try:
        if units and float(units) >= 2:
            return "Multifamily"
    except Exception:
        pass
    if any(x in raw for x in ["SINGLE", "SFR", "DETACHED", "RESIDENTIAL"]):
        return "Single Family"
    return "Unknown"


def type_matches(subject_family: str, comp_family: str, strict=True) -> bool:
    if not subject_family or subject_family == "Unknown" or not comp_family or comp_family == "Unknown":
        return not strict
    if subject_family in ["Condo", "Townhome"]:
        return comp_family in ["Condo", "Townhome"]
    return subject_family == comp_family

# -------------------------
# API CLIENTS
# -------------------------
def rentcast_headers():
    return {"X-Api-Key": RENTCAST_API_KEY, "Accept": "application/json"}


def get_property_record(address: str):
    if not RENTCAST_API_KEY:
        return None, "Missing RentCast API key. Add it in Streamlit secrets."
    url = "https://api.rentcast.io/v1/properties"
    params = {"address": address}
    r = requests.get(url, headers=rentcast_headers(), params=params, timeout=30)
    if r.status_code != 200:
        return None, f"RentCast property lookup failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    if isinstance(data, list) and data:
        return data[0], None
    if isinstance(data, dict) and data:
        return data, None
    return None, "No subject property found."


def get_value_and_comps(address: str, radius: float = 1.0):
    if not RENTCAST_API_KEY:
        return None, "Missing RentCast API key. Add it in Streamlit secrets."
    url = "https://api.rentcast.io/v1/avm/value"
    params = {"address": address, "maxRadius": radius, "lookupSubjectAttributes": "true"}
    r = requests.get(url, headers=rentcast_headers(), params=params, timeout=30)
    if r.status_code != 200:
        return None, f"RentCast AVM lookup failed: {r.status_code} {r.text[:300]}"
    return r.json(), None

def rentcast_property_type_values(subject_family: str):
    if subject_family == "Condo":
        return ["Condo", "Townhouse"]
    if subject_family == "Townhome":
        return ["Townhouse", "Condo"]
    if subject_family == "Single Family":
        return ["Single Family"]
    if subject_family == "Multifamily":
        return ["Multi-Family", "Apartment"]
    if subject_family == "Manufactured":
        return ["Manufactured"]
    return []


def get_sold_property_records(address: str, subject_attrs: dict, radius: float = 1.0, days: int = 365, sqft_tolerance: int = 700, strict_type: bool = True, strict_beds: bool = False, strict_baths: bool = False):
    """Pull nearby SOLD property records from RentCast /v1/properties.
    This is separate from the AVM endpoint and is what powers the detailed comp table.
    """
    if not RENTCAST_API_KEY:
        return [], "Missing RentCast API key. Add it in Streamlit secrets."

    subject_family = subject_attrs.get("family", "Unknown")
    ssqft = safe_float(subject_attrs.get("sqft"))
    sbeds = subject_attrs.get("beds")
    sbaths = subject_attrs.get("baths")
    query_types = rentcast_property_type_values(subject_family) if strict_type else [None]
    if not query_types:
        query_types = [None]

    all_records = []
    errors = []
    url = "https://api.rentcast.io/v1/properties"

    for property_type in query_types:
        params = {
            "address": address,
            "radius": radius,
            "saleDateRange": str(days),
            "limit": 500,
        }
        if property_type:
            params["propertyType"] = property_type
        if strict_beds and sbeds is not None:
            params["bedrooms"] = str(int(round(float(sbeds))))
        if strict_baths and sbaths is not None:
            params["bathrooms"] = str(float(sbaths)).rstrip('0').rstrip('.')
        if ssqft and sqft_tolerance:
            params["squareFootage"] = f"{max(0, int(ssqft - sqft_tolerance))}-{int(ssqft + sqft_tolerance)}"

        try:
            r = requests.get(url, headers=rentcast_headers(), params=params, timeout=30)
            if r.status_code != 200:
                errors.append(f"Property records comp search failed: {r.status_code} {r.text[:180]}")
                continue
            data = r.json()
            if isinstance(data, list):
                all_records.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("data"), list):
                all_records.extend(data.get("data"))
        except Exception as e:
            errors.append(f"Property records comp search error: {e}")

    # Deduplicate by RentCast id or address
    unique = {}
    for rec in all_records:
        key = rec.get("id") or rec.get("formattedAddress") or str(rec)
        unique[key] = rec
    return list(unique.values()), "; ".join(errors) if errors else None


def normalize_comp_record(record, subject_attrs=None):
    c = dict(record or {})
    sale_date, sale_price = latest_sale(c)
    if sale_date is not None:
        c["soldDate"] = sale_date
    if sale_price is not None:
        c["soldPrice"] = sale_price
    buyer = owner_name(c)
    if buyer:
        c["buyerName"] = buyer
    if subject_attrs:
        dist = comp_field(c, "distance", "distanceMiles")
        if dist is None:
            dist = haversine_miles(subject_attrs.get("lat"), subject_attrs.get("lon"), c.get("latitude"), c.get("longitude"))
        if dist is not None:
            c["distance"] = dist
    c["_source"] = c.get("_source") or "RentCast Records"
    return c


def merge_comps(*lists):
    merged = {}
    for items in lists:
        for item in items or []:
            addr = item.get("formattedAddress") or item.get("address") or item.get("addressLine1") or str(item)
            key = re.sub(r"[^A-Z0-9]", "", str(addr).upper())
            if key not in merged:
                merged[key] = item
            else:
                # Prefer record data because it usually has owner/buyer and sale history.
                existing = merged[key]
                combined = dict(existing)
                combined.update({k:v for k,v in item.items() if v not in [None, "", []]})
                merged[key] = combined
    return list(merged.values())

# -------------------------
# SAMPLE DATA FOR PREVIEW
# -------------------------
def sample_result():
    subject = {
        "formattedAddress": "1342 Branham Ln #1, San Jose, CA 95118",
        "bedrooms": 2, "bathrooms": 1, "squareFootage": 810, "lotSize": 436, "yearBuilt": 1970,
        "propertyType": "Condominium", "owner": {"names": ["Sample Owner"]},
    }
    comps = [
        {"formattedAddress":"1350 Branham Ln #4, San Jose, CA 95118","soldDate":"2026-06-18","soldPrice":486000,"bedrooms":2,"bathrooms":1,"squareFootage":825,"lotSize":436,"distance":0.08,"buyerName":"Silicon Valley Homes LLC","propertyType":"Condominium","photo":"https://placehold.co/160x100?text=Condo+Comp"},
        {"formattedAddress":"1328 Branham Ln #7, San Jose, CA 95118","soldDate":"2026-05-30","soldPrice":475000,"bedrooms":2,"bathrooms":1,"squareFootage":800,"lotSize":436,"distance":0.13,"buyerName":"Jane Doe","propertyType":"Condominium","photo":"https://placehold.co/160x100?text=Condo+Comp"},
        {"formattedAddress":"1400 Branham Ln #2, San Jose, CA 95118","soldDate":"2026-04-22","soldPrice":492000,"bedrooms":2,"bathrooms":1,"squareFootage":850,"lotSize":436,"distance":0.31,"buyerName":"Bay Area Property Group LLC","propertyType":"Townhome","photo":"https://placehold.co/160x100?text=Townhome+Comp"},
    ]
    return subject, {"comparables": comps, "price": 475458}, None

# -------------------------
# COMP FILTERING
# -------------------------
def extract_comps(avm_data):
    if not avm_data:
        return []
    for key in ["comparables", "saleComparables", "comps", "listings"]:
        value = avm_data.get(key) if isinstance(avm_data, dict) else None
        if isinstance(value, list):
            return value
    return []


def get_subject_attrs(subject, avm_data):
    merged = {}
    if isinstance(avm_data, dict):
        merged.update(avm_data.get("subjectProperty", {}) or {})
    if isinstance(subject, dict):
        merged.update(subject)
    owner = None
    if isinstance(merged.get("owner"), dict):
        owner = ((merged.get("owner") or {}).get("names") or [None])[0]
    return {
        "beds": merged.get("bedrooms") or merged.get("beds"),
        "baths": merged.get("bathrooms") or merged.get("baths"),
        "sqft": merged.get("squareFootage") or merged.get("sqft") or merged.get("livingArea"),
        "lot": merged.get("lotSize") or merged.get("lotSquareFootage"),
        "year": merged.get("yearBuilt"),
        "address": merged.get("formattedAddress") or merged.get("addressLine1") or merged.get("address"),
        "owner": owner,
        "family": property_family(merged),
        "raw_type": raw_property_type(merged) or "Unknown",
        "lat": merged.get("latitude"),
        "lon": merged.get("longitude"),
    }


def filter_comps(raw_comps, subject_attrs, months=6, radius=0.5, sqft_tolerance=300, strict_type=True, strict_beds=True, strict_baths=True):
    now = datetime.now()
    cutoff = now - timedelta(days=months * 30)
    sbeds, sbaths, ssqft = subject_attrs.get("beds"), subject_attrs.get("baths"), subject_attrs.get("sqft")
    subject_family = subject_attrs.get("family", "Unknown")
    filtered = []
    for c in raw_comps:
        raw_sale_date, raw_sold_price = latest_sale(c)
        sale_date = parse_date(raw_sale_date)
        sold_price = raw_sold_price
        beds = comp_field(c, "bedrooms", "beds")
        baths = comp_field(c, "bathrooms", "baths")
        sqft = comp_field(c, "squareFootage", "sqft", "livingArea")
        dist = comp_field(c, "distance", "distanceMiles")
        status = str(comp_field(c, "status", "listingStatus") or "sold").lower()
        comp_family = property_family(c)
        comp_addr = comp_field(c, "formattedAddress", "address", "addressLine1")
        if same_property_address(comp_addr, subject_attrs.get("address")):
            continue

        if not sale_date or not sold_price:
            continue
        if sale_date < cutoff:
            continue
        if "active" in status or "pending" in status:
            continue
        if dist is not None and float(dist) > radius:
            continue
        if strict_type and not type_matches(subject_family, comp_family, strict=True):
            continue
        if strict_beds and sbeds is not None and beds is not None and int(round(float(beds))) != int(round(float(sbeds))):
            continue
        if strict_baths and sbaths is not None and baths is not None and float(baths) != float(sbaths):
            continue
        if ssqft is not None and sqft is not None and abs(float(sqft) - float(ssqft)) > sqft_tolerance:
            continue
        c2 = dict(c)
        c2["_sale_date"] = sale_date
        c2["_sold_price"] = float(sold_price)
        c2["_sqft"] = float(sqft) if sqft else None
        c2["_family"] = comp_family
        c2["soldDate"] = raw_sale_date
        c2["soldPrice"] = sold_price
        c2["buyerName"] = owner_name(c2) or comp_field(c2, "buyerName", "buyer", "ownerName", "owner")
        c2["_source"] = c2.get("_source") or "RentCast"
        filtered.append(c2)
    filtered.sort(key=lambda x: x["_sale_date"], reverse=True)
    return filtered


def find_best_comps(raw_comps, subject_attrs, min_comps=3):
    passes = [
        {"label":"Ideal: 6 mo / 0.50 mi / same type / same bed-bath / ±300 sqft", "months":6, "radius":0.5, "sqft_tolerance":300, "strict_type":True, "strict_beds":True, "strict_baths":True},
        {"label":"Expanded: 6 mo / 0.75 mi / same type / same bed-bath / ±400 sqft", "months":6, "radius":0.75, "sqft_tolerance":400, "strict_type":True, "strict_beds":True, "strict_baths":True},
        {"label":"Fallback: 12 mo / 1.00 mi / same type / same bed-bath / ±500 sqft", "months":12, "radius":1.0, "sqft_tolerance":500, "strict_type":True, "strict_beds":True, "strict_baths":True},
        {"label":"Closest same property type: 12 mo / 1.00 mi / flexible bed-bath / ±600 sqft", "months":12, "radius":1.0, "sqft_tolerance":600, "strict_type":True, "strict_beds":False, "strict_baths":False},
        {"label":"Closest available: 12 mo / 1.00 mi / type may be unknown", "months":12, "radius":1.0, "sqft_tolerance":700, "strict_type":False, "strict_beds":False, "strict_baths":False},
    ]
    for rule in passes:
        comps = filter_comps(raw_comps, subject_attrs, **{k:v for k,v in rule.items() if k != "label"})
        if len(comps) >= min_comps:
            return comps, rule["label"]
    last_rule = passes[-1]
    return filter_comps(raw_comps, subject_attrs, **{k:v for k,v in last_rule.items() if k != "label"}), last_rule["label"]


def calculate_arv(comps, subject_sqft, avm_data=None):
    if isinstance(avm_data, dict):
        for key in ["price", "value", "valuation", "estimatedValue"]:
            if avm_data.get(key):
                # Still prefer comps if we have them, but use AVM as fallback.
                avm_value = float(avm_data.get(key))
                break
        else:
            avm_value = None
    else:
        avm_value = None
    if not comps:
        return avm_value, None, None
    prices = [c["_sold_price"] for c in comps]
    psf = [c["_sold_price"] / c["_sqft"] for c in comps if c.get("_sqft")]
    median_price = float(pd.Series(prices).median())
    avg_psf = sum(psf) / len(psf) if psf else None
    arv = avg_psf * float(subject_sqft) if avg_psf and subject_sqft else median_price
    return arv, avg_psf, median_price

# -------------------------
# UI
# -------------------------
st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("V1: Address → property type → sold comps → ARV → MAO → offer strategy")

with st.sidebar:
    st.header("Settings")
    use_sample = st.toggle("Preview with sample data", value=not bool(RENTCAST_API_KEY))
    st.caption("Turn this off after your Streamlit secrets are added.")
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before fallback", min_value=1, max_value=10, value=3)

col1, col2 = st.columns([2, 1])
with col1:
    address = st.text_input("Property Address", value="1342 Branham Ln #1, San Jose, CA 95118")
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
    address = normalize_address(address)
    errors = []
    if use_sample:
        subject_record, avm_data, err = sample_result()
        subject_attrs = get_subject_attrs(subject_record, avm_data)
        raw_comps = [normalize_comp_record(c, subject_attrs) for c in extract_comps(avm_data)]
        record_count = len(raw_comps)
        avm_count = len(raw_comps)
    else:
        subject_record, err = get_property_record(address)
        if err: errors.append(err)
        avm_data, err = get_value_and_comps(address, radius=1.0)
        if err: errors.append(err)
        subject_attrs = get_subject_attrs(subject_record, avm_data)

        avm_comps = [normalize_comp_record(c, subject_attrs) for c in extract_comps(avm_data)]

        # Pull detailed SOLD comps from RentCast Property Records. This is what gives us
        # last sale price/date and current owner/buyer names for the comp table.
        record_comps_12, err = get_sold_property_records(address, subject_attrs, radius=1.0, days=365, sqft_tolerance=700, strict_type=True, strict_beds=False, strict_baths=False)
        if err: errors.append(err)
        record_comps_24, err = get_sold_property_records(address, subject_attrs, radius=1.5, days=730, sqft_tolerance=900, strict_type=True, strict_beds=False, strict_baths=False) if len(record_comps_12) < min_comps else ([], None)
        if err: errors.append(err)
        record_comps = [normalize_comp_record(c, subject_attrs) for c in (record_comps_12 + record_comps_24)]

        raw_comps = merge_comps(record_comps, avm_comps)
        record_count = len(record_comps)
        avm_count = len(avm_comps)

    comps, comp_window = find_best_comps(raw_comps, subject_attrs, min_comps=min_comps)

    if errors:
        for e in errors:
            st.error(e)

    st.subheader("Subject Property")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Property Type", subject_attrs.get("family") or "—")
    c2.metric("Beds", subject_attrs.get("beds") or "—")
    c3.metric("Baths", subject_attrs.get("baths") or "—")
    c4.metric("House SqFt", number(subject_attrs.get("sqft")))
    c5.metric("Lot SqFt", number(subject_attrs.get("lot")))
    c6.metric("Year Built", subject_attrs.get("year") or "—")
    st.caption(f"Raw property type returned by data source: {subject_attrs.get('raw_type') or 'Unknown'}")
    st.caption(f"Comp data pulled: {len(raw_comps)} total candidates ({record_count} property records + {avm_count} AVM comps).")

    arv, avg_psf, median_price = calculate_arv(comps, subject_attrs.get("sqft"), avm_data)
    st.subheader("ARV + Offer Matrix")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Recommended ARV", money(arv))
    a2.metric("Average $/SqFt", money(avg_psf) if avg_psf else "—")
    a3.metric("Median Comp Price", money(median_price))
    a4.metric("Comp Rule Used", comp_window)

    tiers = [("75% ARV", 0.75), ("70% ARV", 0.70), ("65% ARV", 0.65), ("60% ARV", 0.60)]
    tier_cols = st.columns(4)
    for col, (label, pct) in zip(tier_cols, tiers):
        offer = (arv * pct - repair_estimate) if arv else None
        col.metric(label, money(offer))
        col.caption(f"{label} - repairs")

    st.subheader("Sold Comparable Sales")
    st.caption("SOLD comps only. Pulls RentCast Property Records first for buyer/current-owner names, then blends AVM comps. Property type is matched first: condo/townhome vs single-family vs multifamily.")

    if not comps:
        st.warning("No comps matched after filtering. The app did pull candidate records above; next step is to review/widen the criteria or inspect source fields.")
        if raw_comps:
            with st.expander("Show raw comp candidates for troubleshooting"):
                preview = []
                for c in raw_comps[:25]:
                    sale_date, sale_price = latest_sale(c)
                    preview.append({
                        "Address": comp_field(c, "formattedAddress", "address", "addressLine1"),
                        "Type": raw_property_type(c) or c.get("propertyType"),
                        "Beds": comp_field(c, "bedrooms", "beds"),
                        "Baths": comp_field(c, "bathrooms", "baths"),
                        "SqFt": comp_field(c, "squareFootage", "sqft", "livingArea"),
                        "Sale Date": fmt_date(sale_date),
                        "Sale Price": money(sale_price) if sale_price else "—",
                        "Buyer/Owner": owner_name(c) or "—",
                        "Distance": comp_field(c, "distance", "distanceMiles"),
                        "Source": c.get("_source", "RentCast"),
                    })
                st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
    else:
        rows = []
        for c in comps:
            comp_addr = comp_field(c, "formattedAddress", "address", "addressLine1") or "Unknown address"
            buyer = owner_name(c) or comp_field(c, "buyerName", "buyer", "ownerName", "owner") or "Buyer name pending"
            investor = is_likely_investor(str(buyer))
            sqft = comp_field(c, "squareFootage", "sqft", "livingArea")
            lot = comp_field(c, "lotSize", "lotSquareFootage")
            dist = comp_field(c, "distance", "distanceMiles")
            sold_price = comp_field(c, "soldPrice", "lastSalePrice", "salePrice", "price")
            ppsf = float(sold_price) / float(sqft) if sold_price and sqft else None
            links = maps_links(comp_addr)
            rows.append({
                "Photo": comp_field(c, "photo", "imageUrl", "thumbnail") or "",
                "Sold Date": fmt_date(comp_field(c, "soldDate", "lastSaleDate", "saleDate", "closeDate")),
                "Address": comp_addr,
                "Property Type": c.get("_family") or property_family(c),
                "Distance": f"{float(dist):.2f} mi" if dist is not None else "—",
                "Beds": comp_field(c, "bedrooms", "beds") or "—",
                "Baths": comp_field(c, "bathrooms", "baths") or "—",
                "House SqFt": number(sqft),
                "Lot SqFt": number(lot),
                "$/SqFt": money(ppsf) if ppsf else "—",
                "Recorded Sale Price": money(sold_price),
                "Buyer": buyer,
                "Investor?": "YES" if investor else "NO",
                "Source": c.get("_source", "RentCast"),
                "Redfin": links["Redfin"],
                "Zillow": links["Zillow"],
                "Map": links["Map"],
                "Street View": links["Street View"],
                "Satellite": links["Satellite"],
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            column_config={
                "Photo": st.column_config.ImageColumn("Photo", width="small"),
                "Redfin": st.column_config.LinkColumn("Redfin", display_text="Open"),
                "Zillow": st.column_config.LinkColumn("Zillow", display_text="Open"),
                "Map": st.column_config.LinkColumn("Map", display_text="Map"),
                "Street View": st.column_config.LinkColumn("Street View", display_text="Street"),
                "Satellite": st.column_config.LinkColumn("Satellite", display_text="Satellite"),
            },
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("AI Acquisition Notes")
    if arv:
        st.markdown(f"""
        <div class="card">
        <b>Property type logic:</b> Subject was classified as <b>{subject_attrs.get('family')}</b>. The comp engine first looks for matching property types before widening criteria.<br><br>
        <b>Comp rule used:</b> {comp_window}.<br><br>
        <b>Recommended starting point:</b> Use the 70% ARV tier unless buyer demand is extremely strong.<br><br>
        <b>Next step:</b> Review photos, Street View, and condition outliers before making the final offer.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Run live data or adjust filters to generate AI notes.")
