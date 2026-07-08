# Newcastle AI Acquisition Analyzer - Verified Comps Fix

This patch fixes stale/incorrect comp prices by separating valuation comps from enrichment records.

## What changed
- ARV/comp table now prioritizes verified RentCast AVM sale comps.
- RentCast property records are used for buyer/current-owner enrichment only.
- Property record prices/dates no longer overwrite verified sale comp prices/dates.
- Adds Sale Source and Sale Verified columns.
- Keeps property-type matching: condo/townhome vs SFR vs multifamily.

Upload `app.py` and `requirements.txt` to GitHub and commit changes.
