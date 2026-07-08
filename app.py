import math
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle AI Acquisition Analyzer", page_icon="🏠", layout="wide")

REALIE_BASE = "https://app.realie.ai/api/public"

# -------------------------
# Helpers
# -------------------------

def money(v):
    try:
        if v is None or v == "": return "—"
        return f"${float(v):,.0f}"
    except Exception:
        return "—"


def num(v):
    try:
        if v is None or v == "": return "—"
        return f"{float(v):,.0f}"
    except Exception:
        return "—"


def pct(v):
    try:
        return f"{float(v):.0f}%"
    except Exception:
        return "—"


def clean_phone(p):
    return re.sub(r"\D", "", p or "")


def parse_address(full_address: str):
    """Simple parser with override fields available in UI."""
    s = (full_address or "").strip()
    unit = ""
    m = re.search(r"#\s*([A-Za-z0-9-]+)|\bunit\s+([A-Za-z0-9-]+)", s, re.I)
    if m:
        unit = (m.group(1) or m.group(2) or "").strip()
        s = re.sub(r"\s*(#\s*[A-Za-z0-9-]+|unit\s+[A-Za-z0-9-]+)", "", s, flags=re.I)
    parts = [p.strip() for p in s.split(",")]
    street = parts[0] if parts else s
    city = parts[1] if len(parts) > 1 else ""
    state = "CA"
    county = ""
    if len(parts) > 2:
        m2 = re.search(r"\b([A-Z]{2})\b", parts[2])
        if m2: state = m2.group(1)
    return street, unit, city, state, county


def property_type(p):
    if p.get("condo") is True:
        return "condo"
    use = str(p.get("useCode") or "").lower()
    legal = str(p.get("legalDesc") or "").lower()
    if "unit" in legal and "condo" in legal:
        return "condo"
    # Realie premium endpoint supports any/condo/house. Keep production lock simple for now.
    return "house"


def buyer_name(p):
    return p.get("grantee") or p.get("ownerName") or "—"


def sale_date(p):
    return p.get("transferDateObject") or p.get("transferDate") or p.get("purchaseSaleDate") or p.get("recordingDate")


def sale_price(p):
    for k in ["transferPrice", "salePriceLastTransfer", "pastPriceSale", "assessorSalePrice"]:
        val = p.get(k)
        try:
            if val and float(val) > 0:
                return float(val)
        except Exception:
            pass
    if p.get("transfers"):
        for t in p["transfers"]:
            try:
                if t.get("transferPrice") and float(t.get("transferPrice")) > 0:
                    return float(t.get("transferPrice"))
            except Exception:
                pass
    return None


def date_obj(value):
    if not value:
        return None
    s = str(value)
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return None


def distance_miles(lat1, lon1, lat2, lon2):
    try:
        R = 3958.8
        phi1 = math.radians(float(lat1)); phi2 = math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dl = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    except Exception:
        return None


def comp_score(subject, comp, max_radius=1.0):
    score = 0
    notes = []
    subj_type = property_type(subject)
    comp_type = property_type(comp)
    if subj_type == comp_type:
        score += 25; notes.append("same property type")
    if (subject.get("subdivision") and comp.get("subdivision") and
            str(subject.get("subdivision")).lower() == str(comp.get("subdivision")).lower()):
        score += 30; notes.append("same subdivision")
    if subject.get("totalBedrooms") == comp.get("totalBedrooms"):
        score += 15; notes.append("same beds")
    if subject.get("totalBathrooms") == comp.get("totalBathrooms"):
        score += 10; notes.append("same baths")
    subj_sqft = subject.get("livingArea") or subject.get("buildingArea")
    comp_sqft = comp.get("livingArea") or comp.get("buildingArea")
    if subj_sqft and comp_sqft:
        diff = abs(float(subj_sqft) - float(comp_sqft))
        if diff <= 100: score += 20
        elif diff <= 200: score += 15
        elif diff <= 300: score += 10
    dt = date_obj(sale_date(comp))
    if dt:
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= 180: score += 20
        elif age_days <= 365: score += 15
        elif age_days <= 548: score += 8
    d = distance_miles(subject.get("latitude"), subject.get("longitude"), comp.get("latitude"), comp.get("longitude"))
    if d is not None:
        if d <= .25: score += 20
        elif d <= .5: score += 15
        elif d <= 1: score += 10
        elif d <= 2: score += 5
    if subject.get("yearBuilt") and comp.get("yearBuilt"):
        if abs(int(subject.get("yearBuilt")) - int(comp.get("yearBuilt"))) <= 10:
            score += 10
    return min(round(score / 150 * 100), 100), ", ".join(notes)


def realie_get(path, params):
    key = st.secrets.get("REALIE_API_KEY", "")
    if not key:
        return {"ok": False, "error": "Missing REALIE_API_KEY in Streamlit Secrets."}
    url = REALIE_BASE + path
    try:
        r = requests.get(url, headers={"Authorization": key, "Accept": "application/json"}, params=params, timeout=30)
        data = None
        try: data = r.json()
        except Exception: data = {"raw": r.text[:2000]}
        return {"ok": r.ok, "status_code": r.status_code, "url": r.url, "json": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_property(street, unit, city, county, state):
    params = {"state": state, "address": street, "residential": "true", "limit": 20}
    if unit: params["unitNumberStripped"] = unit
    if city: params["city"] = city
    if county: params["county"] = county
    res = realie_get("/property/search/", params)
    props = (res.get("json") or {}).get("properties") or []
    if unit and props:
        for p in props:
            if str(p.get("unitNumberStripped", "")).strip().lower() == str(unit).strip().lower():
                return p, res
        for p in props:
            if f"unit {unit}" in str(p.get("legalDesc", "")).lower():
                return p, res
    return (props[0] if props else None), res


def get_comps(subject, min_comps=3):
    subj_type = property_type(subject)
    lat, lon = subject.get("latitude"), subject.get("longitude")
    sqft = subject.get("livingArea") or subject.get("buildingArea") or 0
    beds = subject.get("totalBedrooms")
    baths = subject.get("totalBathrooms")

    rules = [
        {"label":"Ideal: 6 mo / 0.5 mi / same type / ±300 sqft", "radius":0.5, "timeFrame":6, "ptype":subj_type, "sqft":True},
        {"label":"Fallback: 12 mo / 0.5 mi / same type / ±300 sqft", "radius":0.5, "timeFrame":12, "ptype":subj_type, "sqft":True},
        {"label":"Fallback: 12 mo / 1.0 mi / same type / ±300 sqft", "radius":1.0, "timeFrame":12, "ptype":subj_type, "sqft":True},
        {"label":"Fallback: 18 mo / 1.0 mi / any type, filtered back to same type", "radius":1.0, "timeFrame":18, "ptype":"any", "sqft":False},
    ]
    last = None
    for rule in rules:
        params = {"latitude": lat, "longitude": lon, "radius": rule["radius"], "timeFrame": rule["timeFrame"], "maxResults": 25, "propertyType": rule["ptype"]}
        if sqft and rule["sqft"]:
            params["sqftMin"] = max(0, int(sqft) - 300)
            params["sqftMax"] = int(sqft) + 300
        if beds:
            params["bedsMin"] = int(beds); params["bedsMax"] = int(beds)
        if baths:
            params["bathsMin"] = int(baths); params["bathsMax"] = int(baths)
        res = realie_get("/premium/comparables/", params)
        last = res
        comps = (res.get("json") or {}).get("comparables") or []
        cleaned = []
        subj_parcel = subject.get("parcelId")
        for c in comps:
            if c.get("parcelId") == subj_parcel:
                continue
            if property_type(c) != subj_type:
                continue
            if not sale_price(c):
                continue
            c["_distance"] = distance_miles(lat, lon, c.get("latitude"), c.get("longitude"))
            c["_match"], c["_match_notes"] = comp_score(subject, c, rule["radius"])
            cleaned.append(c)
        # de-dupe by parcel + price + date
        seen, unique = set(), []
        for c in sorted(cleaned, key=lambda x: (-x.get("_match",0), x.get("_distance") or 99)):
            k = (c.get("parcelId"), sale_price(c), sale_date(c))
            if k not in seen:
                unique.append(c); seen.add(k)
        if len(unique) >= min_comps:
            return unique[:8], rule["label"], res
    return [], "No rule returned enough same-type sold comps", last


def calc_arv(comps):
    if not comps: return None
    weights, prices = [], []
    for c in comps:
        price = sale_price(c)
        match = max(c.get("_match", 50), 1)
        if price:
            weights.append(match)
            prices.append(price)
    if not prices: return None
    return sum(p*w for p,w in zip(prices,weights)) / sum(weights)


def comp_dataframe(comps):
    rows = []
    for i, c in enumerate(comps, start=1):
        price = sale_price(c)
        sqft = c.get("livingArea") or c.get("buildingArea")
        rows.append({
            "#": i,
            "Address": c.get("addressFull") or c.get("addressUnit") or c.get("addressRaw"),
            "Sold Date": str(sale_date(c) or "—")[:10],
            "Sold Price": money(price),
            "$/SF": money((price / sqft) if price and sqft else None),
            "SqFt": num(sqft),
            "Beds": c.get("totalBedrooms") or "—",
            "Baths": c.get("totalBathrooms") or "—",
            "Distance": f"{c.get('_distance'):.2f} mi" if c.get("_distance") is not None else "—",
            "Buyer / Current Owner": buyer_name(c),
            "Match": pct(c.get("_match")),
        })
    return pd.DataFrame(rows)

# -------------------------
# UI
# -------------------------

st.markdown("""
<style>
.block-container {padding-top: 2rem;}
.metric-card {background:#111827;border:1px solid #253044;border-radius:14px;padding:18px;}
.small-muted {color:#9ca3af;font-size:.9rem;}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Settings")
    st.success("Primary Data Source: Realie")
    st.caption("RentCast is no longer used for final comps.")
    repair_estimate = st.number_input("Repair Estimate", min_value=0, value=58000, step=1000)
    min_comps = st.number_input("Minimum comps before fallback", min_value=1, value=3, step=1)
    show_diag = st.toggle("Show API diagnostics", value=False)
    st.divider()
    st.subheader("Contact Actions")
    st.caption("Call/SMS opens your Mac/iPhone default calling or Messages app.")

st.title("🏠 Newcastle AI Acquisition Analyzer")
st.caption("Realie-powered V2: accurate property type lock → verified comps → weighted ARV → MAO")

col_main, col_photo = st.columns([3, 1.5])
with col_main:
    property_address = st.text_input("Property Address", "1342 Branham Ln #1, San Jose, CA 95118")
    street0, unit0, city0, state0, county0 = parse_address(property_address)
    with st.expander("Address details / override"):
        state = st.text_input("State", state0 or "CA")
        street = st.text_input("Street address only", street0)
        unit = st.text_input("Unit only", unit0)
        city = st.text_input("City", city0)
        county = st.text_input("County", county0 or "Santa Clara")
    seller_phone = st.text_input("Seller Phone (optional, for Call/SMS buttons)", "")
    photo_link = st.text_input("Dropbox / Google Drive Photo Link", placeholder="Paste photo folder link here")
with col_photo:
    st.write("Upload Property Photos")
    photos = st.file_uploader("Upload", accept_multiple_files=True, type=["png","jpg","jpeg","webp"], label_visibility="collapsed")
    st.caption("200MB per file • PNG, JPG, WEBP")

analyze = st.button("Analyze Property", use_container_width=True, type="primary")

st.divider()

# Quick Call/SMS Launcher
st.subheader("Quick Call / SMS Launcher")
st.caption("Enter any phone number and click Call or SMS. This opens the default phone or Messages app on your Mac/iPhone.")
call_col1, call_col2 = st.columns([1, 1.5])
with call_col1:
    quick_phone = st.text_input("Phone number", seller_phone or "", key="quick_phone")
with call_col2:
    sms_msg = st.text_input("SMS message", "Hi, this is Marco with Newcastle Partners...", key="sms_msg")
phone_digits = clean_phone(quick_phone)
if phone_digits:
    c1, c2 = st.columns(2)
    c1.link_button("📞 Call", f"tel:{phone_digits}", use_container_width=True)
    c2.link_button("💬 SMS", f"sms:{phone_digits}&body={quote_plus(sms_msg)}", use_container_width=True)

if analyze:
    with st.spinner("Searching Realie property data..."):
        subject, search_res = search_property(street, unit, city, county, state)

    if show_diag:
        st.subheader("Property Search Diagnostics")
        st.json(search_res)

    if not subject:
        st.error("Realie did not return a subject property. Try spelling out Lane, Avenue, Drive, etc., or remove the unit and retry.")
        st.stop()

    st.subheader("Subject Property")
    cols = st.columns(6)
    cols[0].metric("Property Type", property_type(subject).title())
    cols[1].metric("Beds", subject.get("totalBedrooms") or "—")
    cols[2].metric("Baths", subject.get("totalBathrooms") or "—")
    cols[3].metric("House SqFt", num(subject.get("livingArea") or subject.get("buildingArea")))
    cols[4].metric("Lot SqFt", num(subject.get("lotSizeArea")))
    cols[5].metric("Year Built", subject.get("yearBuilt") or "—")
    st.caption(f"Owner: {subject.get('ownerName','—')} | Parcel: {subject.get('parcelId','—')} | Subdivision: {subject.get('subdivision','—')}")

    with st.spinner("Finding strict same-type sold comparables..."):
        comps, rule_used, comps_res = get_comps(subject, min_comps=int(min_comps))

    if show_diag:
        st.subheader("Comparables Diagnostics")
        st.json(comps_res)

    arv = calc_arv(comps) or subject.get("modelValue")
    confidence = round(sum([c.get("_match", 0) for c in comps]) / len(comps)) if comps else 0

    st.subheader("ARV + Offer Matrix")
    m = st.columns(5)
    m[0].metric("Recommended ARV", money(arv))
    m[1].metric("Confidence", pct(confidence) if confidence else "Low")
    m[2].metric("Comp Rule Used", rule_used[:30] + ("..." if len(rule_used) > 30 else ""))
    m[3].metric("Comps Used", len(comps))
    m[4].metric("Repairs", money(repair_estimate))

    tcols = st.columns(4)
    for idx, tier in enumerate([0.75, 0.70, 0.65, 0.60]):
        before = arv * tier if arv else None
        after = before - repair_estimate if before else None
        tcols[idx].metric(f"{int(tier*100)}% ARV", money(before))
        tcols[idx].caption(f"After repairs: {money(after)}")

    st.subheader("Comparable Sales")
    st.caption("Same property type only. Buyer/current owner is pulled from grantee first, then ownerName.")
    if comps:
        st.dataframe(comp_dataframe(comps), use_container_width=True, hide_index=True)
        st.markdown("### Click / Expand Comp Details")
        for i, c in enumerate(comps, start=1):
            price = sale_price(c)
            label = f"#{i} — {c.get('addressFull') or c.get('addressRaw')} | {money(price)} | {pct(c.get('_match'))} match"
            with st.expander(label):
                d1, d2, d3 = st.columns(3)
                d1.write("**Buyer / Current Owner**")
                d1.write(buyer_name(c))
                d1.write("**Owner Mailing Address**")
                d1.write(c.get("ownerAddressFull") or "—")
                d2.write("**Sale / Transfer**")
                d2.write(f"Date: {str(sale_date(c) or '—')[:10]}")
                d2.write(f"Price: {money(price)}")
                d2.write(f"Doc Type: {c.get('transferDocType') or '—'}")
                d2.write(f"Parcel: {c.get('parcelId') or '—'}")
                d3.write("**Finance / Equity**")
                d3.write(f"Lender: {c.get('lenderName') or '—'}")
                d3.write(f"Lien Balance: {money(c.get('totalLienBalance'))}")
                d3.write(f"Equity Est: {money(c.get('equityCurrentEstBal'))}")
                d3.write(f"LTV: {c.get('LTVCurrentEstCombined', '—')}")
                st.write("**Why this comp matched**")
                st.write(c.get("_match_notes") or "—")
                map_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(c.get('addressFull') or c.get('addressRaw') or '')}"
                st.link_button("Open Map", map_url)
    else:
        st.warning("No usable same-type sold comps found under the fallback rules. Try lowering strictness or checking the raw Realie response.")

    st.subheader("AI Acquisition Notes")
    if comps:
        st.info(f"Used {len(comps)} same-type comps. Started strict at 0.5 mi / 6 months / ±300 sqft and used fallback only if needed. Buyer names are shown from Realie grantee/owner fields. Review photos and condition before final offer.")
    else:
        st.info("Realie found the subject property, but the comp engine did not find enough same-type sold comps. Manual review recommended.")
