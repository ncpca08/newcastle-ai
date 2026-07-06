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
    return f"{value:,.0f}"


def fmt_date(value):
    if not value:
        return "—"
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(str(value)[:len(fmt.replace('%f','000000'))], fmt).strftime("%m/%d/%Y")
        except Exception:
            pass
    try:
        return pd.to_datetime(value).strftime("%m/%d/%Y")
    except Exception:
        return str(value)


def parse_date(value):
    if not value:
        return None
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def normalize_address(address: str) -> str:
    return re.sub(r"\s+", " ", address.strip())


def maps_links(address: str):
    q = quote_plus(address)
    return {
        "Redfin Search": f"https://www.redfin.com/stingray/do/location-autocomplete?location={q}&start=0&count=10&v=2",
        "Google Maps": f"https://www.google.com/maps/search/?api=1&query={q}",
        "Street View": f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=&query={q}",
        "Satellite": f"https://www.google.com/maps/search/?api=1&query={q}&basemap=satellite",
    }


def redfin_search_link(address: str):
    # Redfin does not provide a stable public address URL without the Redfin property id.
    # This opens a normal search results page for the exact address.
    return f"https://www.redfin.com/search#search_location={quote_plus(address)}"


def is_likely_investor(name: str) -> bool:
    if not name:
        return False
    keywords = ["LLC", "INC", "CORP", "TRUST", "HOLDINGS", "INVEST", "CAPITAL", "PROPERTIES", "HOMES", "REALTY", "PARTNERS", "LP", "L.P."]
    upper = name.upper()
    return any(k in upper for k in keywords)

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
    return None, "No subject property found."


def get_value_and_comps(address: str, radius: float = 0.5):
    if not RENTCAST_API_KEY:
        return None, "Missing RentCast API key. Add it in Streamlit secrets."
    url = "https://api.rentcast.io/v1/avm/value"
    params = {"address": address, "maxRadius": radius, "lookupSubjectAttributes": "true"}
    r = requests.get(url, headers=rentcast_headers(), params=params, timeout=30)
    if r.status_code != 200:
        return None, f"RentCast AVM lookup failed: {r.status_code} {r.text[:300]}"
    return r.json(), None


def dealmachine_enrich_owner(address: str):
    # Placeholder: DealMachine API docs/account permissions vary. This function is intentionally safe.
    # Once the exact endpoint is confirmed, wire it here and return buyer/owner/property-count data.
    return None

# -------------------------
# SAMPLE DATA FOR PREVIEW
# -------------------------
def sample_result():
    subject = {
        "address": "838 S Drake Ave, Stockton, CA 95215",
        "bedrooms": 3, "bathrooms": 2, "squareFootage": 1120, "lotSize": 8537, "yearBuilt": 2005,
        "owner": {"names": ["Sample Owner"]},
    }
    comps = [
        {"formattedAddress":"824 S Drake Ave, Stockton, CA 95215","soldDate":"2026-06-18","soldPrice":425000,"bedrooms":3,"bathrooms":2,"squareFootage":1104,"lotSize":8020,"distance":0.18,"buyerName":"ABC Investments LLC","photo":"https://placehold.co/160x100?text=Comp+Photo"},
        {"formattedAddress":"901 S Drake Ave, Stockton, CA 95215","soldDate":"2026-06-03","soldPrice":418000,"bedrooms":3,"bathrooms":2,"squareFootage":1187,"lotSize":7710,"distance":0.24,"buyerName":"Jane Doe","photo":"https://placehold.co/160x100?text=Comp+Photo"},
        {"formattedAddress":"733 S Drake Ave, Stockton, CA 95215","soldDate":"2026-05-28","soldPrice":432000,"bedrooms":3,"bathrooms":2,"squareFootage":1210,"lotSize":9100,"distance":0.39,"buyerName":"Stockton Homes LLC","photo":"https://placehold.co/160x100?text=Comp+Photo"},
        {"formattedAddress":"857 S Drake Ave, Stockton, CA 95215","soldDate":"2026-04-11","soldPrice":421000,"bedrooms":3,"bathrooms":2,"squareFootage":1098,"lotSize":8450,"distance":0.46,"buyerName":"Robert Smith","photo":"https://placehold.co/160x100?text=Comp+Photo"},
    ]
    return subject, comps, None

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
    return {
        "beds": merged.get("bedrooms") or merged.get("beds"),
        "baths": merged.get("bathrooms") or merged.get("baths"),
        "sqft": merged.get("squareFootage") or merged.get("sqft") or merged.get("livingArea"),
        "lot": merged.get("lotSize") or merged.get("lotSquareFootage"),
        "year": merged.get("yearBuilt"),
        "address": merged.get("formattedAddress") or merged.get("addressLine1") or merged.get("address"),
        "owner": ((merged.get("owner") or {}).get("names") or [None])[0] if isinstance(merged.get("owner"), dict) else None,
    }


def comp_field(comp, *keys):
    for key in keys:
        if isinstance(comp, dict) and comp.get(key) is not None:
            return comp.get(key)
    return None


def filter_comps(raw_comps, subject_attrs, months=6):
    now = datetime.now()
    cutoff = now - timedelta(days=months * 30)
    sbeds, sbaths, ssqft = subject_attrs.get("beds"), subject_attrs.get("baths"), subject_attrs.get("sqft")
    filtered = []
    for c in raw_comps:
        sale_date = parse_date(comp_field(c, "soldDate", "lastSaleDate", "saleDate", "closeDate"))
        sold_price = comp_field(c, "soldPrice", "lastSalePrice", "salePrice", "price")
        beds = comp_field(c, "bedrooms", "beds")
        baths = comp_field(c, "bathrooms", "baths")
        sqft = comp_field(c, "squareFootage", "sqft", "livingArea")
        dist = comp_field(c, "distance", "distanceMiles")
        status = str(comp_field(c, "status", "listingStatus") or "sold").lower()
        if not sale_date or not sold_price:
            continue
        if sale_date < cutoff:
            continue
        if "active" in status or "pending" in status:
            continue
        if sbeds is not None and beds is not None and int(round(float(beds))) != int(round(float(sbeds))):
            continue
        if sbaths is not None and baths is not None and float(baths) != float(sbaths):
            continue
        if ssqft is not None and sqft is not None and abs(float(sqft) - float(ssqft)) > 300:
            continue
        if dist is not None and float(dist) > 0.5:
            continue
        c2 = dict(c)
        c2["_sale_date"] = sale_date
        c2["_sold_price"] = float(sold_price)
        c2["_sqft"] = float(sqft) if sqft else None
        filtered.append(c2)
    filtered.sort(key=lambda x: x["_sale_date"], reverse=True)
    return filtered


def calculate_arv(comps, subject_sqft):
    if not comps:
        return None, None, None
    prices = [c["_sold_price"] for c in comps]
    psf = [c["_sold_price"] / c["_sqft"] for c in comps if c.get("_sqft")]
    avg_price = sum(prices) / len(prices)
    median_price = float(pd.Series(prices).median())
    avg_psf = sum(psf) / len(psf) if psf else None
    arv = avg_psf * subject_sqft if avg_psf and subject_sqft else median_price
    return arv, avg_psf, median_price

# -------------------------
# UI
# -------------------------
st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("V1: Address → sold comps → ARV → MAO → offer strategy")

with st.sidebar:
    st.header("Settings")
    use_sample = st.toggle("Preview with sample data", value=not bool(RENTCAST_API_KEY))
    st.caption("Turn this off after your Streamlit secrets are added.")
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before 12-month fallback", min_value=1, max_value=10, value=3)

col1, col2 = st.columns([2, 1])
with col1:
    address = st.text_input("Property Address", value="838 S Drake Ave, Stockton, CA 95215")
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
        subject_record, raw_comps, avm_data = sample_result()
        subject_attrs = get_subject_attrs(subject_record, {})
        comps_6 = filter_comps(raw_comps, subject_attrs, months=6)
        comps = comps_6 if len(comps_6) >= min_comps else filter_comps(raw_comps, subject_attrs, months=12)
        comp_window = "Last 6 months" if len(comps_6) >= min_comps else "Fallback: last 12 months"
    else:
        subject_record, err = get_property_record(address)
        if err: errors.append(err)
        avm_data, err = get_value_and_comps(address)
        if err: errors.append(err)
        subject_attrs = get_subject_attrs(subject_record, avm_data)
        raw_comps = extract_comps(avm_data)
        comps_6 = filter_comps(raw_comps, subject_attrs, months=6)
        comps = comps_6 if len(comps_6) >= min_comps else filter_comps(raw_comps, subject_attrs, months=12)
        comp_window = "Last 6 months" if len(comps_6) >= min_comps else "Fallback: last 12 months"

    if errors:
        for e in errors:
            st.error(e)

    st.subheader("Subject Property")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Beds", subject_attrs.get("beds") or "—")
    c2.metric("Baths", subject_attrs.get("baths") or "—")
    c3.metric("House SqFt", number(subject_attrs.get("sqft")))
    c4.metric("Lot SqFt", number(subject_attrs.get("lot")))
    c5.metric("Year Built", subject_attrs.get("year") or "—")

    arv, avg_psf, median_price = calculate_arv(comps, subject_attrs.get("sqft"))
    st.subheader("ARV + Offer Matrix")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Recommended ARV", money(arv))
    a2.metric("Average $/SqFt", money(avg_psf) if avg_psf else "—")
    a3.metric("Median Comp Price", money(median_price))
    a4.metric("Comp Window", comp_window)

    tiers = [("75% ARV", 0.75), ("70% ARV", 0.70), ("65% ARV", 0.65), ("60% ARV", 0.60)]
    tier_cols = st.columns(4)
    for col, (label, pct) in zip(tier_cols, tiers):
        offer = (arv * pct - repair_estimate) if arv else None
        col.metric(label, money(offer))
        col.caption(f"{label} - repairs")

    st.subheader("Sold Comparable Sales")
    st.caption("SOLD comps only. Sorted newest sold first. Recorded Sale Price = the purchase price paid by the buyer in the sale record returned by the data source.")

    if not comps:
        st.warning("No comps matched the strict criteria. Try widening radius/sqft/date rules manually after reviewing the subject.")
    else:
        rows = []
        for c in comps:
            comp_addr = comp_field(c, "formattedAddress", "address", "addressLine1") or "Unknown address"
            buyer = comp_field(c, "buyerName", "buyer", "ownerName", "owner") or "Buyer name pending"
            investor = is_likely_investor(str(buyer))
            sqft = comp_field(c, "squareFootage", "sqft", "livingArea")
            lot = comp_field(c, "lotSize", "lotSquareFootage")
            dist = comp_field(c, "distance", "distanceMiles")
            rows.append({
                "Photo": comp_field(c, "photo", "imageUrl", "thumbnail") or "",
                "Sold Date": fmt_date(comp_field(c, "soldDate", "lastSaleDate", "saleDate", "closeDate")),
                "Address": comp_addr,
                "Distance": f"{float(dist):.2f} mi" if dist is not None else "—",
                "Beds": comp_field(c, "bedrooms", "beds") or "—",
                "Baths": comp_field(c, "bathrooms", "baths") or "—",
                "House SqFt": number(sqft),
                "Lot SqFt": number(lot),
                "Recorded Sale Price": money(comp_field(c, "soldPrice", "lastSalePrice", "salePrice", "price")),
                "Buyer": buyer,
                "Investor?": "YES" if investor else "NO",
                "Redfin": redfin_search_link(comp_addr),
                "Map": maps_links(comp_addr)["Google Maps"],
                "Street View": maps_links(comp_addr)["Street View"],
                "Satellite": maps_links(comp_addr)["Satellite"],
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            column_config={
                "Photo": st.column_config.ImageColumn("Photo", width="small"),
                "Redfin": st.column_config.LinkColumn("Redfin", display_text="Open"),
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
        <b>Recommended starting point:</b> Use the 70% ARV tier unless the buyer pool is extremely strong.<br><br>
        <b>Comp rule used:</b> {comp_window}, same beds/baths, ±300 sqft, within 0.50 miles, sold only.<br><br>
        <b>Next step:</b> Review the thumbnails and Street View to remove condition/location outliers before making the final offer.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Run live data or adjust filters to generate AI notes.")
