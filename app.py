import math
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlencode

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle OS", page_icon="◼", layout="wide", initial_sidebar_state="expanded")

# -------------------------
# CONFIG / SECRETS
# -------------------------
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

REALIE_API_KEY = get_secret("REALIE_API_KEY")

# -------------------------
# STYLE
# -------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] {font-family: 'Inter', sans-serif;}
    .stApp {background: radial-gradient(circle at top right, #151a27 0%, #090b11 38%, #07090d 100%); color:#f4f7fb;}
    .block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1500px;}
    [data-testid="stSidebar"] {background:#090c12; border-right:1px solid #1c2330;}
    [data-testid="stSidebar"] .block-container {padding-top:1rem;}
    h1,h2,h3 {letter-spacing:-0.035em;}
    .brand {font-size:1.05rem;font-weight:800;letter-spacing:.14em;color:#fff;margin-bottom:0;}
    .brand-sub {font-size:.72rem;color:#738096;letter-spacing:.08em;text-transform:uppercase;margin-top:.1rem;}
    .eyebrow {font-size:.75rem;color:#59d8ff;letter-spacing:.14em;text-transform:uppercase;font-weight:700;}
    .hero-title {font-size:2.35rem;line-height:1.08;font-weight:800;margin:.25rem 0 .4rem;}
    .hero-copy {color:#8d98aa;font-size:.98rem;margin-bottom:1.3rem;}
    .kpi-card {background:linear-gradient(145deg,rgba(24,29,40,.96),rgba(14,18,26,.96));border:1px solid #242c3a;border-radius:18px;padding:18px;min-height:135px;box-shadow:0 18px 50px rgba(0,0,0,.24);}
    .kpi-label {font-size:.72rem;text-transform:uppercase;letter-spacing:.11em;color:#7e899b;font-weight:700;}
    .kpi-value {font-size:1.8rem;font-weight:800;color:#f8fbff;margin:.35rem 0;}
    .kpi-goal {font-size:.76rem;color:#8d98aa;}
    .progress-track {height:7px;background:#242a35;border-radius:999px;margin-top:12px;overflow:hidden;}
    .progress-fill {height:100%;background:linear-gradient(90deg,#3fc9ff,#7558ff);border-radius:999px;}
    .progress-fill.complete {background:linear-gradient(90deg,#35e6a5,#65f2c2);box-shadow:0 0 16px rgba(53,230,165,.35);}
    .panel {background:rgba(17,21,30,.92);border:1px solid #222a37;border-radius:20px;padding:20px;margin:8px 0 18px;}
    .status-dot {display:inline-block;width:8px;height:8px;border-radius:50%;background:#35e6a5;box-shadow:0 0 10px rgba(53,230,165,.8);margin-right:7px;}
    .hot-pill {display:inline-block;padding:5px 9px;border-radius:999px;background:rgba(255,92,119,.12);border:1px solid rgba(255,92,119,.26);color:#ff718b;font-size:.7rem;font-weight:800;letter-spacing:.08em;}
    div[data-testid="stMetric"] {background:linear-gradient(145deg,#171c27,#10141d);border:1px solid #252d3b;border-radius:16px;padding:15px;}
    .stButton > button {border-radius:12px;border:1px solid #4b65ff;background:linear-gradient(90deg,#335dff,#7558ff);color:white;font-weight:700;min-height:46px;}
    .stTextInput input, .stNumberInput input {background:#0d1118;border:1px solid #293243;border-radius:11px;color:#f7f9fc;}
    .card {background:#111827;border:1px solid #243044;border-radius:18px;padding:18px;margin:10px 0;}
    a {color:#67d8ff !important;text-decoration:none;}
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
# REALIE.AI API CLIENT
# -------------------------
REALIE_ADDRESS_URL = "https://app.realie.ai/api/public/property/address/"
REALIE_COMPS_URL = "https://app.realie.ai/api/public/premium/comparables/"


def realie_headers():
    return {"Authorization": REALIE_API_KEY, "Accept": "application/json"}


def split_us_address(full_address: str):
    """Split a normal US address into street/state without requiring another package."""
    clean = normalize_address(full_address)
    parts = [part.strip() for part in clean.split(",") if part.strip()]
    if len(parts) < 2:
        return None, None, None, "Use a complete address: street, city, state ZIP."
    street = parts[0]
    city = parts[-2] if len(parts) >= 3 else ""
    state_zip = parts[-1]
    match = re.search(r"\b([A-Za-z]{2})\b(?:\s+\d{5}(?:-\d{4})?)?$", state_zip)
    if not match:
        return None, None, None, "Could not identify the two-letter state abbreviation."
    state = match.group(1).upper()
    return street, city, state, None


def recursive_value(data, *keys):
    """Find the first matching field in nested Realie responses, case-insensitively."""
    wanted = {str(k).lower() for k in keys}
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in wanted and value not in (None, ""):
                return value
        for value in data.values():
            found = recursive_value(value, *keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = recursive_value(item, *keys)
            if found not in (None, ""):
                return found
    return None


def get_property_record(full_address: str):
    if not REALIE_API_KEY:
        return None, "Missing REALIE_API_KEY. Add it in Streamlit secrets."
    street, city, state, parse_error = split_us_address(full_address)
    if parse_error:
        return None, parse_error
    params = {"address": street, "state": state}
    try:
        response = requests.get(REALIE_ADDRESS_URL, headers=realie_headers(), params=params, timeout=30)
    except requests.RequestException as exc:
        return None, f"Realie property lookup connection error: {exc}"
    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text[:300])
        except Exception:
            detail = response.text[:300]
        return None, f"Realie property lookup failed ({response.status_code}): {detail}"
    payload = response.json()
    property_record = payload.get("property") if isinstance(payload, dict) else None
    if not property_record:
        return None, "Realie did not return a subject property for that address."
    property_record["_input_full_address"] = full_address
    property_record["_input_city"] = city
    property_record["_input_state"] = state
    return property_record, None


def realie_property_type(subject_attrs):
    raw = str(subject_attrs.get("property_type") or "").lower()
    if "condo" in raw:
        return "condo"
    if any(word in raw for word in ["single", "house", "residential", "sfr"]):
        return "house"
    return "any"


def get_realie_comps(subject_attrs, radius=0.5, months=6, sqft_tolerance=300, max_results=50):
    if not REALIE_API_KEY:
        return [], "Missing REALIE_API_KEY. Add it in Streamlit secrets."
    lat, lon = subject_attrs.get("latitude"), subject_attrs.get("longitude")
    if lat is None or lon is None:
        return [], "Realie returned the property, but latitude/longitude were missing."
    params = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "timeFrame": months,
        "maxResults": min(int(max_results), 50),
        "propertyType": realie_property_type(subject_attrs),
    }
    sqft = safe_float(subject_attrs.get("sqft"), 0)
    beds = subject_attrs.get("beds")
    baths = subject_attrs.get("baths")
    if sqft:
        params["sqftMin"] = max(0, int(sqft - sqft_tolerance))
        params["sqftMax"] = int(sqft + sqft_tolerance)
    if beds not in (None, ""):
        params["bedsMin"] = int(round(float(beds)))
        params["bedsMax"] = int(round(float(beds)))
    if baths not in (None, ""):
        params["bathsMin"] = float(baths)
        params["bathsMax"] = float(baths)
    try:
        response = requests.get(REALIE_COMPS_URL, headers=realie_headers(), params=params, timeout=30)
    except requests.RequestException as exc:
        return [], f"Realie comparables connection error: {exc}"
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text[:300])
        except Exception:
            detail = response.text[:300]
        return [], f"Realie comparables search failed ({response.status_code}): {detail}"
    payload = response.json()
    comps = payload.get("comparables", []) if isinstance(payload, dict) else []
    return comps if isinstance(comps, list) else [], None

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
def extract_comps(data):
    if isinstance(data, dict):
        value = data.get("comparables")
        return value if isinstance(value, list) else []
    return data if isinstance(data, list) else []


def get_subject_attrs(subject, _unused=None):
    subject = subject or {}
    address = recursive_value(subject, "formattedAddress", "fullAddress", "propertyAddress", "address")
    if isinstance(address, dict):
        address = recursive_value(address, "formatted", "full", "line1")
    return {
        "beds": recursive_value(subject, "bedrooms", "beds", "totalBedrooms", "bedroomCount", "totalBedrooms", "bedroomCount"),
        "baths": recursive_value(subject, "bathrooms", "baths", "totalBathrooms", "bathroomCount", "totalBathrooms", "bathroomCount"),
        "sqft": recursive_value(subject, "squareFootage", "sqft", "livingArea", "buildingArea", "heatedArea", "buildingArea", "heatedArea"),
        "lot": recursive_value(subject, "lotSize", "lotSquareFootage", "lotArea", "landArea", "lotArea", "landArea"),
        "year": recursive_value(subject, "yearBuilt", "constructionYear"),
        "address": address or subject.get("_input_full_address"),
        "owner": recursive_value(subject, "ownerName", "ownerNames", "owner"),
        "latitude": recursive_value(subject, "latitude", "lat"),
        "longitude": recursive_value(subject, "longitude", "lng", "lon"),
        "property_type": recursive_value(subject, "propertyType", "propertyUse", "useCodeDescription", "buildingType"),
    }


def comp_field(comp, *keys):
    return recursive_value(comp, *keys)

def filter_comps(raw_comps, subject_attrs, months=6, radius=0.5, sqft_tolerance=300):
    now = datetime.now()
    cutoff = now - timedelta(days=months * 30)
    sbeds, sbaths, ssqft = subject_attrs.get("beds"), subject_attrs.get("baths"), subject_attrs.get("sqft")
    filtered = []
    for c in raw_comps:
        sale_date = parse_date(comp_field(c, "soldDate", "lastSaleDate", "saleDate", "closeDate", "saleRecordingDate", "lastTransferDate"))
        sold_price = comp_field(c, "soldPrice", "lastSalePrice", "salePrice", "price", "lastSaleAmount", "saleAmount", "transferAmount")
        beds = comp_field(c, "bedrooms", "beds", "totalBedrooms", "bedroomCount")
        baths = comp_field(c, "bathrooms", "baths", "totalBathrooms", "bathroomCount")
        sqft = comp_field(c, "squareFootage", "sqft", "livingArea", "buildingArea", "heatedArea")
        dist = comp_field(c, "distance", "distanceMiles", "distanceInMiles")
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
        if ssqft is not None and sqft is not None and abs(float(sqft) - float(ssqft)) > sqft_tolerance:
            continue
        if dist is not None and float(dist) > radius:
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
# WHOLESALE / FILLOUT HELPERS
# -------------------------
def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def wholesale_numbers(arv, buyer_pct, repairs, assignment_fee, other_costs=0):
    if not arv:
        return {"buyer_ceiling": None, "mao": None, "spread": None}
    buyer_ceiling = (arv * buyer_pct) - repairs - other_costs
    mao = buyer_ceiling - assignment_fee
    return {
        "buyer_ceiling": max(0, buyer_ceiling),
        "mao": max(0, mao),
        "spread": max(0, assignment_fee),
    }


def build_fillout_url(base_url: str, values: dict, field_map: dict) -> str:
    """Build a Fillout launch URL using the exact parameter keys configured by the user."""
    if not base_url:
        return ""
    params = {}
    for logical_name, value in values.items():
        parameter_name = (field_map.get(logical_name) or "").strip()
        if parameter_name and value not in (None, ""):
            params[parameter_name] = value
    if not params:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def render_comp_table(comps):
    rows = []
    for c in comps:
        comp_addr = comp_field(c, "formattedAddress", "fullAddress", "propertyAddress", "address", "addressLine1") or "Unknown address"
        buyer = comp_field(c, "buyerName", "buyer", "ownerName", "owner") or "Buyer name pending"
        sqft = comp_field(c, "squareFootage", "sqft", "livingArea", "buildingArea", "heatedArea")
        lot = comp_field(c, "lotSize", "lotSquareFootage", "lotArea", "landArea")
        dist = comp_field(c, "distance", "distanceMiles", "distanceInMiles")
        sold_price = comp_field(c, "soldPrice", "lastSalePrice", "salePrice", "price", "lastSaleAmount", "saleAmount", "transferAmount")
        rows.append({
            "Photo": comp_field(c, "photo", "imageUrl", "thumbnail") or "",
            "Sold Date": fmt_date(comp_field(c, "soldDate", "lastSaleDate", "saleDate", "closeDate", "saleRecordingDate", "lastTransferDate")),
            "Address": comp_addr,
            "Distance": f"{float(dist):.2f} mi" if dist is not None else "—",
            "Beds": comp_field(c, "bedrooms", "beds", "totalBedrooms", "bedroomCount") or "—",
            "Baths": comp_field(c, "bathrooms", "baths", "totalBathrooms", "bathroomCount") or "—",
            "House SqFt": number(sqft),
            "Lot SqFt": number(lot),
            "Sold Price": money(sold_price),
            "Price/SqFt": money(float(sold_price) / float(sqft)) if sold_price and sqft else "—",
            "Buyer": buyer,
            "Investor?": "YES" if is_likely_investor(str(buyer)) else "NO",
            "Redfin": redfin_search_link(comp_addr),
            "Map": maps_links(comp_addr)["Google Maps"],
            "Street View": maps_links(comp_addr)["Street View"],
        })
    st.dataframe(
        pd.DataFrame(rows),
        column_config={
            "Photo": st.column_config.ImageColumn("Photo", width="small"),
            "Redfin": st.column_config.LinkColumn("Redfin", display_text="Open"),
            "Map": st.column_config.LinkColumn("Map", display_text="Map"),
            "Street View": st.column_config.LinkColumn("Street View", display_text="Street"),
        },
        hide_index=True,
        use_container_width=True,
    )


# -------------------------
# UI COMPONENTS
# -------------------------
def kpi_card(label, value, goal_text, progress=None):
    pct = max(0, min(100, (progress or 0) * 100))
    complete = " complete" if progress is not None and progress >= 1 else ""
    st.markdown(f"""
    <div class="kpi-card">
      <div class="kpi-label">{label}</div>
      <div class="kpi-value">{value}</div>
      <div class="kpi-goal">{goal_text}</div>
      <div class="progress-track"><div class="progress-fill{complete}" style="width:{pct:.0f}%"></div></div>
    </div>
    """, unsafe_allow_html=True)


if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None

with st.sidebar:
    st.markdown('<div class="brand">NEWCASTLE OS</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand-sub">Wholesale Acquisition Platform</div>', unsafe_allow_html=True)
    st.markdown("---")
    page = st.radio("Navigation", ["Dashboard", "Wholesale Analyzer", "Contract Builder", "Hot Leads", "Transactions", "Reports", "Settings"], label_visibility="collapsed")
    st.markdown("---")
    st.markdown('<span class="status-dot"></span><span style="color:#a7b1c1;font-size:.82rem">Analyzer online</span>', unsafe_allow_html=True)

if page == "Dashboard":
    st.markdown('<div class="eyebrow">Executive command center</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Newcastle performance dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-copy">Track the activity that creates contracts and consistent monthly profit.</div>', unsafe_allow_html=True)

    metrics = [
        ("New Leads", "23", "Goal: 20+ today", 23/20),
        ("Calls Made", "187", "Goal: 250 today", 187/250),
        ("Offers Sent", "4", "Goal: 5 today", 4/5),
        ("Contracts", "1", "Goal: 1 per week", 1),
        ("Avg Assignment", "$17,250", "Goal: $15,000+", 17250/15000),
        ("Profit MTD", "$48,500", "Live monthly profit", .81),
        ("Follow Ups", "28", "Open follow-up tasks", .7),
        ("Closings Pending", "3", "Active closings", .75),
    ]
    for row in (metrics[:4], metrics[4:]):
        cols = st.columns(4)
        for col, item in zip(cols, row):
            with col:
                kpi_card(*item)

    c1, c2 = st.columns([1.4, 1])
    with c1:
        st.markdown("### Deal command center")
        st.markdown("""
        <div class="panel">
          <div style="display:flex;justify-content:space-between;margin-bottom:14px"><span style="color:#8d98aa">Weekly contract target</span><b>1 contract</b></div>
          <div style="display:flex;justify-content:space-between;margin-bottom:14px"><span style="color:#8d98aa">Monthly profit target</span><b style="color:#57e7b1">$50,000+</b></div>
          <div style="display:flex;justify-content:space-between"><span style="color:#8d98aa">Six-month target</span><b>$100,000/month</b></div>
        </div>""", unsafe_allow_html=True)
        if st.button("Analyze a property now", use_container_width=True):
            st.session_state.nav_to_analyzer = True
            st.info("Select Wholesale Analyzer from the left menu.")
    with c2:
        st.markdown("### Core workflow")
        st.markdown("""
        <div class="panel">
          <b>1.</b> Enter property address<br><br>
          <b>2.</b> Pull instant sold comps<br><br>
          <b>3.</b> Calculate ARV and wholesale MAO<br><br>
          <b>4.</b> Launch the prefilled Fillout contract form
        </div>""", unsafe_allow_html=True)

elif page == "Wholesale Analyzer":
    st.markdown('<div class="eyebrow">Instant wholesale underwriting</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Analyze the deal</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-copy">Enter one address to pull property data, strict sold comps, average ARV, and your maximum wholesale contract price.</div>', unsafe_allow_html=True)

    with st.form("wholesale_analysis_form"):
        address = st.text_input("Property address", value="838 S Drake Ave, Stockton, CA 95215", placeholder="123 Main St, City, State ZIP")
        top1, top2, top3, top4 = st.columns(4)
        with top1:
            repair_estimate = st.number_input("Estimated repairs", min_value=0, value=50000, step=1000)
        with top2:
            buyer_pct = st.number_input("Buyer formula (% of ARV)", min_value=50, max_value=90, value=70, step=1) / 100
        with top3:
            assignment_fee = st.number_input("Target assignment fee", min_value=0, value=15000, step=1000)
        with top4:
            other_costs = st.number_input("Other deal costs", min_value=0, value=0, step=500)

        with st.expander("Comp settings and seller details", expanded=False):
            e1, e2, e3, e4 = st.columns(4)
            with e1:
                radius = st.number_input("Maximum radius (miles)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)
            with e2:
                sqft_tolerance = st.number_input("SqFt tolerance", min_value=100, max_value=1000, value=300, step=50)
            with e3:
                min_comps = st.number_input("Minimum comps", min_value=1, max_value=10, value=3)
            with e4:
                use_sample = st.toggle("Use preview data", value=not bool(REALIE_API_KEY))
            seller_asking = st.number_input("Seller asking price (optional)", min_value=0, value=0, step=1000)

        analyze = st.form_submit_button("Pull instant comps and analyze", type="primary", use_container_width=True)

    if analyze:
        clean_address = normalize_address(address)
        errors = []
        with st.spinner("Pulling subject property and premium sold comps from Realie.ai..."):
            if use_sample:
                subject_record, raw_comps, avm_data = sample_result()
                subject_attrs = get_subject_attrs(subject_record, {})
            else:
                subject_record, err = get_property_record(clean_address)
                if err:
                    errors.append(err)
                subject_attrs = get_subject_attrs(subject_record, {})
                raw_comps_6, err = get_realie_comps(subject_attrs, radius=radius, months=6, sqft_tolerance=sqft_tolerance)
                if err:
                    errors.append(err)
                comps_6 = filter_comps(raw_comps_6, subject_attrs, months=6, radius=radius, sqft_tolerance=sqft_tolerance)
                if len(comps_6) < min_comps:
                    raw_comps_12, err = get_realie_comps(subject_attrs, radius=radius, months=12, sqft_tolerance=sqft_tolerance)
                    if err:
                        errors.append(err)
                    comps_12 = filter_comps(raw_comps_12, subject_attrs, months=12, radius=radius, sqft_tolerance=sqft_tolerance)
                else:
                    comps_12 = []

            if use_sample:
                comps_6 = filter_comps(raw_comps, subject_attrs, months=6, radius=radius, sqft_tolerance=sqft_tolerance)
                comps_12 = filter_comps(raw_comps, subject_attrs, months=12, radius=radius, sqft_tolerance=sqft_tolerance)
            comps = comps_6 if len(comps_6) >= min_comps else comps_12
            comp_window = "Last 6 months" if len(comps_6) >= min_comps else "Fallback: last 12 months"

            psf_arv, avg_psf, median_price = calculate_arv(comps, subject_attrs.get("sqft"))
            sold_prices = [safe_float(c.get("_sold_price")) for c in comps if c.get("_sold_price")]
            average_comp_price = sum(sold_prices) / len(sold_prices) if sold_prices else None
            # User asked for average ARV based on comps. Average sold price is the primary figure;
            # PSF-adjusted ARV remains visible as a secondary validation.
            arv = average_comp_price or psf_arv
            numbers = wholesale_numbers(arv, buyer_pct, repair_estimate, assignment_fee, other_costs)

        for error in errors:
            st.error(error)

        st.session_state.analysis_result = {
            "address": clean_address,
            "subject": subject_attrs,
            "comps": comps,
            "comp_window": comp_window,
            "average_comp_price": average_comp_price,
            "psf_arv": psf_arv,
            "avg_psf": avg_psf,
            "median_price": median_price,
            "arv": arv,
            "repairs": repair_estimate,
            "buyer_pct": buyer_pct,
            "assignment_fee": assignment_fee,
            "other_costs": other_costs,
            "seller_asking": seller_asking,
            **numbers,
        }

    result = st.session_state.analysis_result
    if result:
        subject_attrs = result["subject"]
        comps = result["comps"]
        st.markdown("### Subject property")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Beds", subject_attrs.get("beds") or "—")
        p2.metric("Baths", subject_attrs.get("baths") or "—")
        p3.metric("House SqFt", number(subject_attrs.get("sqft")))
        p4.metric("Lot SqFt", number(subject_attrs.get("lot")))
        p5.metric("Year Built", subject_attrs.get("year") or "—")

        st.markdown("### Wholesale decision")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Average comp ARV", money(result["arv"]))
        d2.metric("Buyer ceiling", money(result["buyer_ceiling"]))
        d3.metric("Wholesale MAO", money(result["mao"]))
        d4.metric("Target assignment", money(result["assignment_fee"]))

        seller_asking = result.get("seller_asking") or 0
        if seller_asking:
            projected_spread = (result["buyer_ceiling"] or 0) - seller_asking
            if seller_asking <= (result["mao"] or 0):
                st.success(f"Seller asking price is within your MAO. Estimated assignment spread: {money(projected_spread)}.")
            else:
                st.error(f"Seller is {money(seller_asking - (result['mao'] or 0))} above your MAO.")

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("PSF-adjusted ARV", money(result["psf_arv"]))
        v2.metric("Average comp $/SqFt", money(result["avg_psf"]))
        v3.metric("Median comp price", money(result["median_price"]))
        v4.metric("Qualified comps", len(comps))

        with st.expander("Show wholesale formula", expanded=True):
            st.code(
                f"Buyer ceiling = ARV × {result['buyer_pct']:.0%} − Repairs − Other Costs\n"
                f"Buyer ceiling = {money(result['arv'])} × {result['buyer_pct']:.0%} − {money(result['repairs'])} − {money(result['other_costs'])} = {money(result['buyer_ceiling'])}\n\n"
                f"Wholesale MAO = Buyer ceiling − Target Assignment Fee\n"
                f"Wholesale MAO = {money(result['buyer_ceiling'])} − {money(result['assignment_fee'])} = {money(result['mao'])}"
            )

        st.markdown("### Instant sold comps")
        st.caption(f"Realie.ai · {result['comp_window']} · sold only · same property type · same beds/baths · ± selected SqFt · within selected radius · newest first")
        if comps:
            render_comp_table(comps)
        else:
            st.warning("No sold comps matched the selected criteria. Expand the radius or tolerance carefully.")

        st.markdown("### Next action")
        n1, n2 = st.columns(2)
        with n1:
            st.link_button("Open subject property on Redfin", redfin_search_link(result["address"]), use_container_width=True)
        with n2:
            st.info("Open Contract Builder to transfer this analysis into your Fillout workflow.")

elif page == "Contract Builder":
    st.markdown('<div class="eyebrow">Fillout + DocuSign workflow</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Build the contract package</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-copy">Use the analyzed property, add seller/escrow/buyer details, then launch your Fillout form with the fields prefilled.</div>', unsafe_allow_html=True)

    result = st.session_state.analysis_result
    if not result:
        st.warning("Analyze a property first. The contract builder uses the saved wholesale analysis.")
    else:
        fillout_default = get_secret("FILLOUT_FORM_URL")
        with st.form("contract_builder_form"):
            st.markdown("#### Property and agreement")
            c1, c2 = st.columns(2)
            with c1:
                contract_address = st.text_input("Property address", value=result["address"])
                purchase_price = st.number_input("Purchase price / seller contract price", min_value=0, value=int(result["mao"] or 0), step=1000)
                closing_date = st.date_input("Closing date", value=(datetime.now() + timedelta(days=21)).date())
            with c2:
                arv_value = st.number_input("ARV", min_value=0, value=int(result["arv"] or 0), step=1000)
                repair_value = st.number_input("Repairs", min_value=0, value=int(result["repairs"] or 0), step=1000)
                assignment_value = st.number_input("Target assignment fee", min_value=0, value=int(result["assignment_fee"] or 0), step=1000)

            st.markdown("#### Seller")
            s1, s2 = st.columns(2)
            with s1:
                seller_name = st.text_input("Seller full name")
                seller_email = st.text_input("Seller email")
            with s2:
                seller_phone = st.text_input("Seller phone")
                seller_notes = st.text_area("Seller / deal notes", height=90)

            st.markdown("#### Escrow and buyer")
            e1, e2 = st.columns(2)
            with e1:
                escrow_company = st.text_input("Escrow / title company")
                escrow_officer = st.text_input("Escrow officer")
                escrow_email = st.text_input("Escrow email")
            with e2:
                buyer_name = st.text_input("End buyer / entity")
                buyer_email = st.text_input("Buyer email")
                buyer_phone = st.text_input("Buyer phone")

            with st.expander("Fillout connection", expanded=True):
                fillout_url = st.text_input("Fillout form URL", value=fillout_default, placeholder="https://form.fillout.com/t/...")
                st.caption("Enter the exact URL parameter key used by each Fillout field. Save the form URL in Streamlit Secrets as FILLOUT_FORM_URL to avoid re-entering it.")
                m1, m2, m3 = st.columns(3)
                with m1:
                    key_address = st.text_input("Address parameter", value="property_address")
                    key_purchase = st.text_input("Purchase price parameter", value="purchase_price")
                    key_closing = st.text_input("Closing date parameter", value="closing_date")
                    key_seller = st.text_input("Seller name parameter", value="seller_name")
                with m2:
                    key_seller_email = st.text_input("Seller email parameter", value="seller_email")
                    key_seller_phone = st.text_input("Seller phone parameter", value="seller_phone")
                    key_escrow = st.text_input("Escrow company parameter", value="escrow_company")
                    key_escrow_email = st.text_input("Escrow email parameter", value="escrow_email")
                with m3:
                    key_buyer = st.text_input("Buyer name parameter", value="buyer_name")
                    key_buyer_email = st.text_input("Buyer email parameter", value="buyer_email")
                    key_arv = st.text_input("ARV parameter", value="arv")
                    key_repairs = st.text_input("Repairs parameter", value="repairs")

            prepare = st.form_submit_button("Prepare Fillout package", type="primary", use_container_width=True)

        if prepare:
            values = {
                "address": contract_address,
                "purchase_price": int(purchase_price),
                "closing_date": closing_date.isoformat(),
                "seller_name": seller_name,
                "seller_email": seller_email,
                "seller_phone": seller_phone,
                "escrow_company": escrow_company,
                "escrow_email": escrow_email,
                "buyer_name": buyer_name,
                "buyer_email": buyer_email,
                "arv": int(arv_value),
                "repairs": int(repair_value),
            }
            field_map = {
                "address": key_address,
                "purchase_price": key_purchase,
                "closing_date": key_closing,
                "seller_name": key_seller,
                "seller_email": key_seller_email,
                "seller_phone": key_seller_phone,
                "escrow_company": key_escrow,
                "escrow_email": key_escrow_email,
                "buyer_name": key_buyer,
                "buyer_email": key_buyer_email,
                "arv": key_arv,
                "repairs": key_repairs,
            }
            launch_url = build_fillout_url(fillout_url, values, field_map)
            st.session_state.fillout_launch_url = launch_url
            st.session_state.contract_summary = {
                **values,
                "escrow_officer": escrow_officer,
                "buyer_phone": buyer_phone,
                "assignment_fee": assignment_value,
                "notes": seller_notes,
            }

        launch_url = st.session_state.get("fillout_launch_url", "")
        if launch_url:
            st.success("Contract package prepared. Review the summary, then launch Fillout.")
            summary = st.session_state.contract_summary
            st.markdown(f"""
            <div class="panel">
              <b>{summary['address']}</b><br><br>
              Seller: {summary['seller_name'] or '—'} · Purchase price: {money(summary['purchase_price'])}<br>
              Closing: {summary['closing_date']} · ARV: {money(summary['arv'])} · Repairs: {money(summary['repairs'])}<br>
              Escrow: {summary['escrow_company'] or '—'} · Buyer: {summary['buyer_name'] or '—'}
            </div>""", unsafe_allow_html=True)
            st.link_button("Open prefilled Fillout form", launch_url, type="primary", use_container_width=True)
            with st.expander("View generated Fillout URL"):
                st.code(launch_url)

elif page == "Hot Leads":
    st.markdown('<div class="eyebrow">Acquisitions</div><div class="hero-title">Hot Leads</div>', unsafe_allow_html=True)
    st.info("This workspace will populate from Bigin after the CRM integration step.")
elif page == "Transactions":
    st.markdown('<div class="eyebrow">Operations</div><div class="hero-title">Transactions</div>', unsafe_allow_html=True)
    st.info("Pending, escrow, and closed transactions will appear here after Bigin is connected.")
elif page == "Reports":
    st.markdown('<div class="eyebrow">Performance</div><div class="hero-title">Reports</div>', unsafe_allow_html=True)
    st.info("Daily, weekly, and monthly scorecards will appear here after live data connections are added.")
else:
    st.markdown('<div class="eyebrow">Administration</div><div class="hero-title">Settings</div>', unsafe_allow_html=True)
    st.markdown("### Required Streamlit secrets")
    st.code('REALIE_API_KEY = "your_realie_key"\nFILLOUT_FORM_URL = "https://form.fillout.com/t/your-form"')
    st.markdown("### Current wholesale comp rules")
    st.markdown("- Realie.ai Premium Comparables only\n- Sold properties only\n- Same property type\n- Same bed and bath count\n- Default 0.50-mile radius\n- Default ±300 square feet\n- Six months first, with a 12-month fallback")
