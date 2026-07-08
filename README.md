# Newcastle AI Acquisition Analyzer

Realie-powered V2 patch.

## What changed
- Main **Analyze Property** now uses Realie Property Search and Premium Comparables.
- Property type lock: condo-to-condo, house-to-house.
- Starts with strict comp logic: same type, 0.5 mi, 6 months, ±300 sqft, exact beds/baths.
- Falls back to 12 months / 1 mile only when needed.
- Weighted ARV and confidence score.
- Offer matrix shows 75%, 70%, 65%, 60% ARV and repair-adjusted offers.
- Quick Call/SMS launcher using `tel:` and `sms:` links.

## Required Streamlit Secret
```toml
REALIE_API_KEY = "your_realie_key"
```

RentCast can stay in secrets as a backup, but final comps in this version use Realie.
