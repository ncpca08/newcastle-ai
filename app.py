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
DEALRUN_API_KEY = get_secret("DEALRUN_API_KEY")

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
    """Legacy helper. Prefer verified_sale() for comp valuation."""
    sale_date = comp_field(record, "soldDate", "lastSaleDate", "saleDate", "closeDate")
    sale_price = comp_field(record, "soldPrice", "lastSalePrice", "salePrice", "price")
    return sale_date, sale_price


def history_sale(record):
    """Find the newest true sale event from RentCast sale history.
    This avoids treating tax/assessment/public-record update dates as sold comps.
    """
    history = record.get("history") if isinstance(record, dict) else None
    if not isinstance(history, dict) or not history:
        return None, None
    sale_events = []
    for key, event in history.items():
        if not isinstance(event, dict):
            continue
        event_text = " ".join(str(event.get(k, "")) for k in ["event", "eventType", "type", "status"]).lower()
        price = event.get("price") or event.get("salePrice") or event.get("soldPrice")
        d = event.get("date") or key
        dt = parse_date(d)
        # Only trust actual sale/sold events with a real price.
        if dt and price and ("sold" in event_text or "sale" in event_text or event_text == ""):
            sale_events.append((dt, d, price))
    if not sale_events:
        return None, None
    sale_events.sort(key=lambda x: x[0], reverse=True)
    return sale_events[0][1], sale_events[0][2]


def verified_sale(record):
    """Return date/price for AVM sale comps only.

    RentCast AVM comparable sale listings do not always use the field name
    soldDate. Some responses use lastSeenDate/removedDate/daysOld with price.
    Property records are still not allowed to become standalone comps.
    """
    if not isinstance(record, dict):
        return None, None
    if record.get("_verified_sale") is True:
        price = comp_field(record, "soldPrice", "salePrice", "price", "lastSalePrice", "listPrice")
        date = comp_field(record, "soldDate", "closeDate", "saleDate", "lastSaleDate", "lastSeenDate", "removedDate", "listingRemovedDate", "offMarketDate", "date")
        if not date:
            days_old = comp_field(record, "daysOld")
            try:
                if days_old is not None:
                    date = (datetime.now() - timedelta(days=int(float(days_old)))).strftime("%Y-%m-%d")
            except Exception:
                pass
        return date, price
    return None, None

def same_property_address(a, b):
    def clean(x):
        return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
    return clean(a) and clean(a) == clean(b)


def normalized_address_key(addr):
    return re.sub(r"[^A-Z0-9]", "", str(addr or "").upper())


def build_record_lookup(records):
    lookup = {}
    for rec in records or []:
        addr = comp_field(rec, "formattedAddress", "address", "addressLine1")
        key = normalized_address_key(addr)
        if key:
            lookup[key] = rec
    return lookup




def address_alias_key(addr):
    """Normalize address for fuzzy unit matching, e.g. Unit 1, #1, Apt 1."""
    x = str(addr or "").upper()
    # Keep only the portion before city/state noise when possible
    x = x.replace(" APARTMENT ", " UNIT ").replace(" APT ", " UNIT ").replace(" STE ", " UNIT ").replace(" SUITE ", " UNIT ")
    x = re.sub(r"#\s*([A-Z0-9]+)", r" UNIT \1", x)
    x = re.sub(r"\bUNIT\s*#?\s*", " UNIT ", x)
    x = re.sub(r"\bDRIVE\b", "DR", x)
    x = re.sub(r"\bLANE\b", "LN", x)
    x = re.sub(r"\bAVENUE\b", "AVE", x)
    x = re.sub(r"\bSTREET\b", "ST", x)
    x = re.sub(r"[^A-Z0-9]+", " ", x).strip()
    return re.sub(r"\s+", "", x)


def loose_address_key(addr):
    """A looser key that removes unit words but keeps the unit number."""
    x = address_alias_key(addr)
    return x.replace("UNIT", "")


def matching_record_for_address(addr, records):
    if not addr or not records:
        return None
    target_exact = normalized_address_key(addr)
    target_alias = address_alias_key(addr)
    target_loose = loose_address_key(addr)
    best = None
    for rec in records or []:
        raddr = comp_field(rec, "formattedAddress", "address", "addressLine1")
        keys = {normalized_address_key(raddr), address_alias_key(raddr), loose_address_key(raddr)}
        if target_exact in keys or target_alias in keys or target_loose in keys:
            return rec
        # fallback: same street number and same unit number when available
        ta = address_alias_key(addr)
        ra = address_alias_key(raddr)
        if ta and ra:
            tnum = re.match(r"(\d+)", ta)
            rnum = re.match(r"(\d+)", ra)
            tunit = re.search(r"UNIT([A-Z0-9]+)", ta)
            runit = re.search(r"UNIT([A-Z0-9]+)", ra)
            if tnum and rnum and tnum.group(1) == rnum.group(1):
                if tunit and runit and tunit.group(1) == runit.group(1):
                    return rec
                if not best:
                    best = rec
    return best

def sale_dates_close(d1, d2, max_days=45):
    dt1, dt2 = parse_date(d1), parse_date(d2)
    if not dt1 or not dt2:
        return False
    return abs((dt1 - dt2).days) <= max_days


def sale_prices_close(p1, p2, tolerance_pct=0.08):
    p1, p2 = safe_float(p1), safe_float(p2)
    if not p1 or not p2:
        return False
    return abs(p1 - p2) / max(p1, p2) <= tolerance_pct


def reject_stale_or_conflicting_avm_comps(avm_comps, property_records):
    """Keep AVM sale comps and use property records only for enrichment.

    Earlier versions rejected AVM comps when property-record sale history disagreed,
    but that removed valid AVM/listing comps in counties where public-record history
    lags or differs from MLS/portal sale history. Now: AVM comps drive ARV/table;
    property records only add buyer/current-owner, lot/photo fields when available.
    """
    lookup = build_record_lookup(property_records)
    clean = []
    rejected = []
    for c in avm_comps or []:
        addr = comp_field(c, "formattedAddress", "address", "addressLine1")
        key = normalized_address_key(addr)
        rec = lookup.get(key) or matching_record_for_address(addr, property_records)
        if rec:
            buyer = owner_name(rec)
            if buyer:
                c["buyerName"] = buyer
                c["_buyer_enrichment_source"] = "RentCast Property Record"
            for k in ["owner", "ownerName", "currentOwnerName", "lotSize", "lotSquareFootage", "photo", "imageUrl", "thumbnail"]:
                if rec.get(k) not in [None, "", []] and c.get(k) in [None, "", []]:
                    c[k] = rec.get(k)
            for k in ["lotSize", "lotSquareFootage", "photo", "imageUrl", "thumbnail"]:
                if c.get(k) in [None, "", []] and rec.get(k) not in [None, "", []]:
                    c[k] = rec.get(k)
        clean.append(c)
    return clean, rejected


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


def normalize_comp_record(record, subject_attrs=None, source="RentCast", verified_sale_comp=False):
    c = dict(record or {})
    c["_source"] = source
    c["_verified_sale"] = bool(verified_sale_comp)

    # Only stamp soldDate/soldPrice when this is a verified sale comp.
    # For property records, keep buyer/current owner enrichment but do NOT let
    # unverified record prices drive the ARV.
    sale_date, sale_price = verified_sale(c)
    if sale_date is not None:
        c["soldDate"] = sale_date
    if sale_price is not None:
        c["soldPrice"] = sale_price
        if verified_sale_comp:
            c["_verified_sale"] = True

    buyer = owner_name(c)
    if buyer:
        c["buyerName"] = buyer

    if subject_attrs:
        dist = comp_field(c, "distance", "distanceMiles")
        if dist is None:
            dist = haversine_miles(subject_attrs.get("lat"), subject_attrs.get("lon"), c.get("latitude"), c.get("longitude"))
        if dist is not None:
            c["distance"] = dist
    return c

def merge_comps(*lists):
    """Merge comps by address, preserving verified AVM sale date/price.

    Property records are valuable for buyer/current-owner enrichment, but they can
    carry stale or non-MLS sale data. If a record is not a verified sale comp, it
    can enrich buyer/owner/lot/photo fields but cannot overwrite verified sale date
    or sale price.
    """
    merged = {}
    for items in lists:
        for item in items or []:
            addr = item.get("formattedAddress") or item.get("address") or item.get("addressLine1") or str(item)
            key = re.sub(r"[^A-Z0-9]", "", str(addr).upper())
            if key not in merged:
                # Do not allow property records/history records to become standalone comps.
                # They are only allowed to enrich a verified AVM sale comp with the same address.
                if item.get("_verified_sale") is True:
                    merged[key] = item
                continue

            existing = dict(merged[key])
            item_verified = item.get("_verified_sale") is True
            existing_verified = existing.get("_verified_sale") is True

            if item_verified and not existing_verified:
                combined = dict(item)
                # Bring over owner/buyer enrichment from the property record if available.
                for k in ["buyerName", "owner", "ownerName", "currentOwnerName", "photo", "imageUrl", "thumbnail", "lotSize", "lotSquareFootage"]:
                    if existing.get(k) not in [None, "", []] and combined.get(k) in [None, "", []]:
                        combined[k] = existing.get(k)
                merged[key] = combined
            elif existing_verified and not item_verified:
                # Preserve verified sale values; only enrich missing non-sale fields.
                for k in ["buyerName", "owner", "ownerName", "currentOwnerName", "photo", "imageUrl", "thumbnail", "lotSize", "lotSquareFootage"]:
                    if item.get(k) not in [None, "", []] and existing.get(k) in [None, "", []]:
                        existing[k] = item.get(k)
                merged[key] = existing
            else:
                # Same trust level: fill blanks only, don't overwrite populated fields.
                for k, v in item.items():
                    if existing.get(k) in [None, "", []] and v not in [None, "", []]:
                        existing[k] = v
                merged[key] = existing
    return list(merged.values())



# -------------------------
# DEALRUN API TESTER (temporary discovery tool)
# -------------------------
def dealrun_request(base_url: str, path: str, method: str = "GET", auth_mode: str = "Bearer", address: str = ""):
    """Small safe tester to discover DealRun API behavior without exposing the key."""
    if not DEALRUN_API_KEY:
        return {"ok": False, "error": "Missing DEALRUN_API_KEY in Streamlit secrets."}

    base_url = (base_url or "").strip().rstrip("/")
    path = (path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = base_url + path

    headers = {"Accept": "application/json"}
    params = {}
    json_body = None

    if auth_mode == "Bearer token":
        headers["Authorization"] = f"Bearer {DEALRUN_API_KEY}"
    elif auth_mode == "X-API-Key":
        headers["X-API-Key"] = DEALRUN_API_KEY
    elif auth_mode == "api_key query":
        params["api_key"] = DEALRUN_API_KEY
    elif auth_mode == "Token header":
        headers["Authorization"] = f"Token {DEALRUN_API_KEY}"

    if address:
        # Try common address parameter names for GET. For POST, send JSON body.
        if method == "GET":
            params["address"] = address
        else:
            headers["Content-Type"] = "application/json"
            json_body = {"address": address}

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=20)
        else:
            r = requests.post(url, headers=headers, params=params, json=json_body, timeout=20)

        body_text = r.text or ""
        body_preview = body_text[:3000]
        try:
            parsed = r.json()
        except Exception:
            parsed = None

        return {
            "ok": 200 <= r.status_code < 300,
            "status_code": r.status_code,
            "url_tested": url,
            "auth_mode": auth_mode,
            "method": method,
            "content_type": r.headers.get("content-type", ""),
            "body_preview": body_preview,
            "json": parsed if isinstance(parsed, (dict, list)) else None,
        }
    except Exception as e:
        return {"ok": False, "url_tested": url, "auth_mode": auth_mode, "method": method, "error": str(e)}


def dealmachine_request(base_url: str, path: str, method: str = "GET", auth_mode: str = "Bearer token", address: str = ""):
    """Small safe tester to discover DealMachine API behavior without exposing the key."""
    if not DEALMACHINE_API_KEY:
        return {"ok": False, "error": "Missing DEALMACHINE_API_KEY in Streamlit secrets."}

    base_url = (base_url or "").strip().rstrip("/")
    path = (path or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = base_url + path

    headers = {"Accept": "application/json"}
    params = {}
    json_body = None

    if auth_mode == "Bearer token":
        headers["Authorization"] = f"Bearer {DEALMACHINE_API_KEY}"
    elif auth_mode == "X-API-Key":
        headers["X-API-Key"] = DEALMACHINE_API_KEY
    elif auth_mode == "api_key query":
        params["api_key"] = DEALMACHINE_API_KEY
    elif auth_mode == "Token header":
        headers["Authorization"] = f"Token {DEALMACHINE_API_KEY}"
    elif auth_mode == "Authorization raw":
        headers["Authorization"] = DEALMACHINE_API_KEY

    if address:
        if method == "GET":
            params["address"] = address
        else:
            headers["Content-Type"] = "application/json"
            json_body = {"address": address}

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=20)
        else:
            r = requests.post(url, headers=headers, params=params, json=json_body, timeout=20)
        body_text = r.text or ""
        try:
            parsed = r.json()
        except Exception:
            parsed = None
        return {
            "ok": 200 <= r.status_code < 300,
            "status_code": r.status_code,
            "url_tested": url,
            "auth_mode": auth_mode,
            "method": method,
            "content_type": r.headers.get("content-type", ""),
            "body_preview": body_text[:3000],
            "json": parsed if isinstance(parsed, (dict, list)) else None,
        }
    except Exception as e:
        return {"ok": False, "url_tested": url, "auth_mode": auth_mode, "method": method, "error": str(e)}

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
        raw_sale_date, raw_sold_price = verified_sale(c)
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

    st.divider()
    st.header("DealRun API Test")
    st.caption("Temporary tester. Add DEALRUN_API_KEY in Streamlit Secrets first.")
    dealrun_base = st.selectbox(
        "Base URL",
        ["https://api.dealrun.ai", "https://app.dealrun.ai/api", "https://app.dealrun.ai", "https://dealrun.ai/api"],
        index=0,
    )
    dealrun_path = st.text_input("Endpoint path", value="/", help="Try /, /api, /v1, /deals, /comps, /properties, /buyers")
    dealrun_method = st.selectbox("Method", ["GET", "POST"], index=0)
    dealrun_auth = st.selectbox("Auth style", ["Bearer token", "X-API-Key", "Token header", "api_key query"], index=0)
    dealrun_addr = st.text_input("Optional test address", value="1342 Branham Ln #1, San Jose, CA 95118")
    if st.button("Test DealRun API", use_container_width=True):
        result = dealrun_request(dealrun_base, dealrun_path, dealrun_method, dealrun_auth, dealrun_addr)
        st.write("DealRun test result")
        st.json(result)

    st.divider()
    st.header("DealMachine API Test")
    st.caption("Temporary tester. Uses DEALMACHINE_API_KEY from Streamlit Secrets.")
    dm_base = st.selectbox(
        "DealMachine Base URL",
        ["https://api.dealmachine.com", "https://api.dealmachine.com/v1", "https://app.dealmachine.com/api", "https://api.dealmachine.com/public"],
        index=0,
    )
    dm_path = st.text_input("DealMachine endpoint path", value="/", help="Start with /, then try /api, /v1, /properties, /property, /leads, /owners")
    dm_method = st.selectbox("DealMachine method", ["GET", "POST"], index=0)
    dm_auth = st.selectbox("DealMachine auth style", ["Bearer token", "X-API-Key", "Token header", "api_key query", "Authorization raw"], index=0)
    dm_addr = st.text_input("DealMachine optional test address", value="1338 Branham Ln #1, San Jose, CA 95118")
    if st.button("Test DealMachine API", use_container_width=True):
        result = dealmachine_request(dm_base, dm_path, dm_method, dm_auth, dm_addr)
        st.write("DealMachine test result")
        st.json(result)


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
        raw_comps = [normalize_comp_record(c, subject_attrs, source="Sample AVM Sale Comp", verified_sale_comp=True) for c in extract_comps(avm_data)]
        record_count = 0
        avm_count = len(raw_comps)
        rejected_count = 0
    else:
        subject_record, err = get_property_record(address)
        if err: errors.append(err)
        avm_data, err = get_value_and_comps(address, radius=1.0)
        if err: errors.append(err)
        subject_attrs = get_subject_attrs(subject_record, avm_data)

        avm_comps = [normalize_comp_record(c, subject_attrs, source="RentCast AVM Sale Comp", verified_sale_comp=True) for c in extract_comps(avm_data)]

        # Property Records are NOT allowed to create comps or ARV.
        # They are used only to verify/enrich AVM sale comps and reject stale/conflicting records.
        record_comps_12, err = get_sold_property_records(address, subject_attrs, radius=1.0, days=365, sqft_tolerance=700, strict_type=True, strict_beds=False, strict_baths=False)
        if err: errors.append(err)
        record_comps_24, err = get_sold_property_records(address, subject_attrs, radius=1.5, days=730, sqft_tolerance=900, strict_type=True, strict_beds=False, strict_baths=False) if len(record_comps_12) < min_comps else ([], None)
        if err: errors.append(err)
        record_comps_raw = record_comps_12 + record_comps_24

        verified_avm_comps, rejected_comps = reject_stale_or_conflicting_avm_comps(avm_comps, record_comps_raw)
        raw_comps = verified_avm_comps
        record_count = len(record_comps_raw)
        avm_count = len(avm_comps)
        rejected_count = len(rejected_comps)

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
    st.caption(f"Comp data pulled: {len(raw_comps)} usable AVM comp candidates ({record_count} property records checked for buyer/owner enrichment + {avm_count} AVM comps pulled).")
    if 'rejected_count' in locals() and rejected_count:
        st.warning(f"{rejected_count} comp(s) were flagged during enrichment review.")
        with st.expander("Show rejected comps"):
            st.dataframe(pd.DataFrame(rejected_comps), hide_index=True, use_container_width=True)

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
        gross_offer = (arv * pct) if arv else None
        repair_adjusted = (gross_offer - repair_estimate) if gross_offer is not None else None
        col.metric(label, money(gross_offer))
        col.caption(f"Before repairs. After repairs: {money(repair_adjusted)}")

    st.subheader("Sold Comparable Sales")
    st.caption("SOLD comps only. ARV and the comp table use RentCast AVM sale comparable listings. Buyer/owner is enriched from matching property records when available. If blank, the comp source did not return buyer data.")

    if not comps:
        st.warning("No comps matched after filtering. The app did pull candidate records above; next step is to review/widen the criteria or inspect source fields.")
        if raw_comps:
            with st.expander("Show raw comp candidates for troubleshooting"):
                preview = []
                for c in raw_comps[:25]:
                    sale_date, sale_price = verified_sale(c)
                    preview.append({
                        "Address": comp_field(c, "formattedAddress", "address", "addressLine1"),
                        "Type": raw_property_type(c) or c.get("propertyType"),
                        "Beds": comp_field(c, "bedrooms", "beds"),
                        "Baths": comp_field(c, "bathrooms", "baths"),
                        "SqFt": comp_field(c, "squareFootage", "sqft", "livingArea"),
                        "Sale Date": fmt_date(sale_date),
                        "Sale Price": money(sale_price) if sale_price else "—",
                        "Buyer / Current Owner": owner_name(c) or comp_field(c, "buyerName") or "—",
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
            _, sold_price = verified_sale(c)
            ppsf = float(sold_price) / float(sqft) if sold_price and sqft else None
            sale_date_display, _ = verified_sale(c)
            links = maps_links(comp_addr)
            rows.append({
                "Photo": comp_field(c, "photo", "imageUrl", "thumbnail") or "",
                "Sold Date": fmt_date(sale_date_display),
                "Address": comp_addr,
                "Property Type": c.get("_family") or property_family(c),
                "Distance": f"{float(dist):.2f} mi" if dist is not None else "—",
                "Beds": comp_field(c, "bedrooms", "beds") or "—",
                "Baths": comp_field(c, "bathrooms", "baths") or "—",
                "House SqFt": number(sqft),
                "Lot SqFt": number(lot),
                "$/SqFt": money(ppsf) if ppsf else "—",
                "AVM Sale Price": money(sold_price),
                "Buyer / Current Owner": buyer,
                "Investor?": "YES" if investor else "NO",
                "Buyer Source": c.get("_buyer_enrichment_source", "Not returned" if buyer == "Buyer name pending" else "Comp source"),
                "Sale Source": c.get("_source", "RentCast"),
                "Sale Source Type": "AVM Sale Comp" if c.get("_verified_sale") else "Unverified",
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
