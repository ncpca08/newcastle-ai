import math
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Newcastle OS", page_icon="◼", layout="wide")


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


REALIE_API_KEY = secret("REALIE_API_KEY")
FILLOUT_FORM_URL = secret("FILLOUT_FORM_URL")

st.markdown(
    """
    <style>
    .stApp {background:#080b11;color:#f6f8fb}
    .block-container {max-width:1500px;padding-top:1.2rem;padding-bottom:3rem}
    [data-testid="stSidebar"] {background:#0b0f16;border-right:1px solid #202735}
    h1,h2,h3 {letter-spacing:-.035em}
    .eyebrow {font-size:.72rem;color:#61dafb;letter-spacing:.15em;text-transform:uppercase;font-weight:800}
    .hero {font-size:2.25rem;font-weight:800;margin:.2rem 0}
    .muted {color:#8d98aa}
    .panel {background:linear-gradient(145deg,#141a24,#0e131b);border:1px solid #252e3d;border-radius:18px;padding:18px;margin:8px 0 18px}
    div[data-testid="stMetric"] {background:linear-gradient(145deg,#151b26,#10151e);border:1px solid #283244;border-radius:16px;padding:14px}
    .stButton>button {min-height:46px;border-radius:12px;border:0;background:linear-gradient(90deg,#3564ff,#7957ff);font-weight:800;color:white}
    .stTextInput input,.stNumberInput input {background:#0e131b!important;border:1px solid #2b3546!important;color:#fff!important;border-radius:11px!important}
    a {color:#63d6ff!important}
    </style>
    """,
    unsafe_allow_html=True,
)


def money(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"${float(value):,.0f}"
    except Exception:
        return "—"


def number(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value):,.0f}"
    except Exception:
        return "—"


def first_value(data: Any, *paths: str) -> Any:
    for path in paths:
        current = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, "", []):
            return current
    return None


def parse_full_address(full_address: str) -> dict[str, str]:
    text = re.sub(r"\s+", " ", full_address.strip())
    pattern = re.compile(
        r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
    )
    match = pattern.match(text)
    if not match:
        raise ValueError("Use this format: 123 Main St, Memphis, TN 38103")
    return {k: v.strip() for k, v in match.groupdict().items()}


def realie_headers() -> dict[str, str]:
    return {"Authorization": REALIE_API_KEY, "Accept": "application/json"}


def realie_get(url: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if not REALIE_API_KEY:
        return None, "Missing REALIE_API_KEY in Streamlit secrets."
    try:
        response = requests.get(url, headers=realie_headers(), params=params, timeout=35)
    except requests.RequestException as exc:
        return None, f"Realie connection failed: {exc}"
    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text)
        except Exception:
            detail = response.text
        return None, f"Realie returned {response.status_code}: {str(detail)[:350]}"
    try:
        return response.json(), None
    except ValueError:
        return None, "Realie returned an unreadable response."


def lookup_subject(parts: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    params = {"address": parts["street"], "state": parts["state"]}
    # Realie requires county when city is supplied, so we intentionally use street + state only.
    payload, error = realie_get("https://app.realie.ai/api/public/property/address/", params)
    if error:
        return None, error
    subject = payload.get("property") if isinstance(payload, dict) else None
    if not isinstance(subject, dict) or not subject:
        return None, "Realie did not return a subject property for this address."
    return subject, None


def subject_fields(subject: dict[str, Any], requested_address: str) -> dict[str, Any]:
    return {
        "address": first_value(subject, "address", "formattedAddress", "propertyAddress", "siteAddress") or requested_address,
        "latitude": first_value(subject, "latitude", "lat", "location.latitude", "coordinates.latitude"),
        "longitude": first_value(subject, "longitude", "lon", "lng", "location.longitude", "coordinates.longitude"),
        "beds": first_value(subject, "bedrooms", "beds", "building.bedrooms", "propertyDetails.bedrooms"),
        "baths": first_value(subject, "bathrooms", "baths", "building.bathrooms", "propertyDetails.bathrooms"),
        "sqft": first_value(subject, "squareFootage", "livingArea", "buildingArea", "building.squareFeet", "propertyDetails.squareFootage"),
        "lot": first_value(subject, "lotSize", "lotSquareFeet", "landArea", "propertyDetails.lotSize"),
        "year": first_value(subject, "yearBuilt", "building.yearBuilt", "propertyDetails.yearBuilt"),
        "property_type": first_value(subject, "propertyType", "propertyUse", "landUse", "propertyDetails.propertyType"),
    }


def realie_property_type(value: Any) -> str:
    text = str(value or "").lower()
    if "condo" in text or "town" in text:
        return "condo"
    if text:
        return "house"
    return "any"


def get_comparables(subject: dict[str, Any], months: int, radius: float, sqft_tolerance: int, max_results: int = 50) -> tuple[list[dict[str, Any]], str | None]:
    lat = subject.get("latitude")
    lon = subject.get("longitude")
    if lat is None or lon is None:
        return [], "Realie returned the property but not its latitude/longitude, so comps could not be requested."
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "timeFrame": months,
        "maxResults": max_results,
        "propertyType": realie_property_type(subject.get("property_type")),
    }
    sqft = subject.get("sqft")
    beds = subject.get("beds")
    baths = subject.get("baths")
    if sqft is not None:
        params["sqftMin"] = max(0, int(float(sqft)) - sqft_tolerance)
        params["sqftMax"] = int(float(sqft)) + sqft_tolerance
    if beds is not None:
        params["bedsMin"] = int(round(float(beds)))
        params["bedsMax"] = int(round(float(beds)))
    if baths is not None:
        params["bathsMin"] = float(baths)
        params["bathsMax"] = float(baths)
    payload, error = realie_get("https://app.realie.ai/api/public/premium/comparables/", params)
    if error:
        return [], error
    comps = payload.get("comparables", []) if isinstance(payload, dict) else []
    return comps if isinstance(comps, list) else [], None


def comp_value(comp: dict[str, Any], *paths: str) -> Any:
    return first_value(comp, *paths)


def normalize_comp(comp: dict[str, Any]) -> dict[str, Any] | None:
    price = comp_value(comp, "salePrice", "soldPrice", "lastSalePrice", "price", "sale.price")
    sold_date = comp_value(comp, "saleDate", "soldDate", "lastSaleDate", "closeDate", "sale.date")
    address = comp_value(comp, "address", "formattedAddress", "propertyAddress", "siteAddress")
    if price in (None, 0, "") or not sold_date or not address:
        return None
    try:
        parsed_date = pd.to_datetime(sold_date)
        parsed_price = float(price)
    except Exception:
        return None
    sqft = comp_value(comp, "squareFootage", "livingArea", "buildingArea", "building.squareFeet")
    try:
        sqft_num = float(sqft) if sqft not in (None, "") else None
    except Exception:
        sqft_num = None
    return {
        "address": str(address),
        "sold_date": parsed_date,
        "sold_price": parsed_price,
        "beds": comp_value(comp, "bedrooms", "beds", "building.bedrooms"),
        "baths": comp_value(comp, "bathrooms", "baths", "building.bathrooms"),
        "sqft": sqft_num,
        "lot": comp_value(comp, "lotSize", "lotSquareFeet", "landArea"),
        "distance": comp_value(comp, "distance", "distanceMiles", "metadata.distance"),
        "buyer": comp_value(comp, "buyerName", "buyer", "ownerName", "owner.name"),
    }


def clean_comps(raw_comps: list[dict[str, Any]], subject_address: str) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()
    subject_key = re.sub(r"\W", "", subject_address).lower()
    for raw in raw_comps:
        if not isinstance(raw, dict):
            continue
        comp = normalize_comp(raw)
        if not comp:
            continue
        comp_key = re.sub(r"\W", "", comp["address"]).lower()
        if comp_key == subject_key:
            continue
        key = (comp_key, comp["sold_price"], str(comp["sold_date"].date()))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(comp)
    cleaned.sort(key=lambda c: c["sold_date"], reverse=True)
    return cleaned


def redfin_link(address: str) -> str:
    # Redfin's hash search often drops users on the homepage. A site-restricted exact-address
    # search reliably surfaces the matching Redfin property page instead.
    return "https://www.google.com/search?q=" + quote_plus(f'site:redfin.com "{address}"')


def map_link(address: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(address)


def street_view_link(address: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(address) + "&layer=c"


def calculate_values(comps: list[dict[str, Any]], subject_sqft: Any) -> dict[str, Any]:
    prices = [c["sold_price"] for c in comps]
    if not prices:
        return {"average_price": None, "median_price": None, "avg_psf": None, "psf_arv": None}
    psfs = [c["sold_price"] / c["sqft"] for c in comps if c.get("sqft")]
    avg_psf = sum(psfs) / len(psfs) if psfs else None
    psf_arv = avg_psf * float(subject_sqft) if avg_psf and subject_sqft else None
    return {
        "average_price": sum(prices) / len(prices),
        "median_price": float(pd.Series(prices).median()),
        "avg_psf": avg_psf,
        "psf_arv": psf_arv,
    }


for key, default in {
    "analysis": None,
    "analysis_address": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.markdown("**NEWCASTLE OS**")
    st.caption("Wholesale Acquisition Platform")
    page = st.radio("Navigation", ["Wholesale Analyzer", "Contract Builder"], label_visibility="collapsed")
    st.divider()
    st.caption("Live data source")
    st.success("Realie.ai")
    st.caption("Every search uses the address entered. No preview/sample-data mode.")

if page == "Wholesale Analyzer":
    st.markdown('<div class="eyebrow">LIVE WHOLESALE UNDERWRITING</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero">Analyze a property</div>', unsafe_allow_html=True)
    st.markdown('<div class="muted">Realie property data → strict sold comps → ARV → wholesale MAO.</div>', unsafe_allow_html=True)

    with st.form("analyze_form"):
        address = st.text_input("Property address", placeholder="123 Main St, Memphis, TN 38103")
        c1, c2, c3, c4 = st.columns(4)
        radius = c1.number_input("Comp radius (miles)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)
        sqft_tolerance = c2.number_input("Sq. ft. tolerance", min_value=100, max_value=1000, value=300, step=50)
        min_comps = c3.number_input("Minimum comps", min_value=1, max_value=10, value=3)
        repairs = c4.number_input("Estimated repairs", min_value=0, value=50000, step=5000)
        d1, d2, d3 = st.columns(3)
        buyer_percentage = d1.number_input("Buyer formula %", min_value=50.0, max_value=90.0, value=70.0, step=1.0) / 100
        assignment_fee = d2.number_input("Target assignment fee", min_value=0, value=15000, step=1000)
        other_costs = d3.number_input("Other costs", min_value=0, value=0, step=1000)
        submitted = st.form_submit_button("Analyze with Realie.ai", use_container_width=True)

    if submitted:
        # Clear prior results before the new request. A failed Memphis request can never display Stockton or any older search.
        st.session_state.analysis = None
        st.session_state.analysis_address = normalize = re.sub(r"\s+", " ", address.strip())
        try:
            parts = parse_full_address(normalize)
        except ValueError as exc:
            st.error(str(exc))
        else:
            with st.spinner("Looking up the subject property and pulling sold comps from Realie.ai…"):
                subject_raw, subject_error = lookup_subject(parts)
                if subject_error:
                    st.error(subject_error)
                else:
                    subject = subject_fields(subject_raw, normalize)
                    comps_6_raw, comps_6_error = get_comparables(subject, 6, radius, int(sqft_tolerance))
                    comps_6 = clean_comps(comps_6_raw, normalize) if not comps_6_error else []
                    window = "Last 6 months"
                    warning = comps_6_error
                    comps = comps_6
                    if len(comps_6) < int(min_comps):
                        comps_12_raw, comps_12_error = get_comparables(subject, 12, radius, int(sqft_tolerance))
                        if not comps_12_error:
                            comps = clean_comps(comps_12_raw, normalize)
                            window = "12-month fallback"
                            warning = None
                        elif not warning:
                            warning = comps_12_error
                    values = calculate_values(comps, subject.get("sqft"))
                    # Recommended ARV is the average sold comp price, exactly as requested.
                    arv = values["average_price"]
                    buyer_ceiling = arv * buyer_percentage - repairs - other_costs if arv else None
                    mao = buyer_ceiling - assignment_fee if buyer_ceiling is not None else None
                    st.session_state.analysis = {
                        "query_address": normalize,
                        "subject": subject,
                        "comps": comps,
                        "window": window,
                        "warning": warning,
                        "values": values,
                        "arv": arv,
                        "buyer_ceiling": buyer_ceiling,
                        "mao": mao,
                        "repairs": repairs,
                        "assignment_fee": assignment_fee,
                        "buyer_percentage": buyer_percentage,
                        "other_costs": other_costs,
                    }

    result = st.session_state.analysis
    if result and result.get("query_address") == st.session_state.analysis_address:
        subject = result["subject"]
        st.markdown(f"### {result['query_address']}")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Beds", subject.get("beds") or "—")
        p2.metric("Baths", subject.get("baths") or "—")
        p3.metric("Living area", number(subject.get("sqft")))
        p4.metric("Lot size", number(subject.get("lot")))
        p5.metric("Year built", subject.get("year") or "—")

        st.markdown("### Wholesale decision")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Average comp ARV", money(result["arv"]))
        a2.metric("Buyer ceiling", money(result["buyer_ceiling"]))
        a3.metric("Newcastle MAO", money(result["mao"]))
        a4.metric("Comp window", result["window"])

        v = result["values"]
        b1, b2, b3 = st.columns(3)
        b1.metric("Median comp price", money(v["median_price"]))
        b2.metric("Average price / sq. ft.", money(v["avg_psf"]))
        b3.metric("PSF-adjusted ARV", money(v["psf_arv"]))

        if result.get("warning"):
            st.warning(result["warning"])

        st.markdown("### Sold comparable sales")
        st.caption("Realie.ai only · sold records · same property type · exact beds/baths · ±300 sq. ft. by default · 0.50 miles by default · newest first")
        if not result["comps"]:
            st.warning("No qualifying comps were returned for this property. No prior property's comps are being shown.")
        else:
            rows = []
            for comp in result["comps"]:
                rows.append({
                    "Sold date": comp["sold_date"].strftime("%m/%d/%Y"),
                    "Address": comp["address"],
                    "Distance": f"{float(comp['distance']):.2f} mi" if comp.get("distance") not in (None, "") else "—",
                    "Beds": comp.get("beds") or "—",
                    "Baths": comp.get("baths") or "—",
                    "Sq. ft.": number(comp.get("sqft")),
                    "Sold price": money(comp.get("sold_price")),
                    "$ / Sq. ft.": money(comp["sold_price"] / comp["sqft"]) if comp.get("sqft") else "—",
                    "Redfin": redfin_link(comp["address"]),
                    "Map": map_link(comp["address"]),
                    "Street View": street_view_link(comp["address"]),
                })
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Redfin": st.column_config.LinkColumn("Find exact Redfin page", display_text="Open"),
                    "Map": st.column_config.LinkColumn("Map", display_text="Map"),
                    "Street View": st.column_config.LinkColumn("Street View", display_text="Street"),
                },
            )

elif page == "Contract Builder":
    st.markdown('<div class="eyebrow">FILLOUT WORKFLOW</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero">Contract builder</div>', unsafe_allow_html=True)
    analysis = st.session_state.analysis or {}
    subject = analysis.get("subject", {})
    with st.form("contract_form"):
        property_address = st.text_input("Property address", value=analysis.get("query_address", ""))
        c1, c2, c3 = st.columns(3)
        purchase_price = c1.number_input("Purchase price", min_value=0, value=int(analysis.get("mao") or 0), step=1000)
        closing_date = c2.date_input("Closing date", value=date.today() + timedelta(days=21))
        arv = c3.number_input("ARV", min_value=0, value=int(analysis.get("arv") or 0), step=1000)
        s1, s2, s3 = st.columns(3)
        seller_name = s1.text_input("Seller name")
        seller_email = s2.text_input("Seller email")
        seller_phone = s3.text_input("Seller phone")
        e1, e2, e3 = st.columns(3)
        escrow_company = e1.text_input("Escrow company")
        escrow_officer = e2.text_input("Escrow officer")
        escrow_email = e3.text_input("Escrow email")
        b1, b2, b3 = st.columns(3)
        buyer_name = b1.text_input("End buyer / entity")
        buyer_email = b2.text_input("Buyer email")
        assignment = b3.number_input("Assignment fee", min_value=0, value=int(analysis.get("assignment_fee") or 0), step=1000)
        notes = st.text_area("Deal notes")
        build = st.form_submit_button("Create prefilled Fillout link", use_container_width=True)
    if build:
        if not FILLOUT_FORM_URL:
            st.error("Add FILLOUT_FORM_URL to Streamlit secrets first.")
        else:
            params = {
                "property_address": property_address,
                "purchase_price": purchase_price,
                "closing_date": closing_date.isoformat(),
                "arv": arv,
                "seller_name": seller_name,
                "seller_email": seller_email,
                "seller_phone": seller_phone,
                "escrow_company": escrow_company,
                "escrow_officer": escrow_officer,
                "escrow_email": escrow_email,
                "buyer_name": buyer_name,
                "buyer_email": buyer_email,
                "assignment_fee": assignment,
                "notes": notes,
            }
            query = "&".join(f"{quote_plus(str(k))}={quote_plus(str(v))}" for k, v in params.items() if v not in (None, ""))
            separator = "&" if "?" in FILLOUT_FORM_URL else "?"
            url = FILLOUT_FORM_URL + separator + query
            st.link_button("Open Fillout contract form", url, use_container_width=True)
            st.caption("Exact Fillout field IDs can be mapped after you provide the form's field references.")
