# Newcastle AI Acquisition Analyzer

Streamlit MVP for Newcastle Partners CA LLC.

## Features
- Address-based property analysis
- RentCast API hooks for property data and sold comps
- DealMachine API hook placeholder for buyer/owner enrichment
- Sold comps only
- 6-month comp window first, fallback to 12 months
- Exact beds/baths and +/- 300 sqft filters
- Lot sqft display
- MM/DD/YYYY sold date format
- Most recent sold comps first
- Buyer name / likely investor flag
- Redfin, Google Maps, Street View, Satellite links
- Photo upload and Dropbox/Google Drive photo link field
- 75%, 70%, 65%, and 60% ARV MAO tiers minus repairs

## Streamlit secrets
Do not commit API keys to GitHub. Add them in Streamlit Community Cloud under Advanced settings:

```toml
RENTCAST_API_KEY = "your_rentcast_key_here"
DEALMACHINE_API_KEY = "your_dealmachine_key_here"
```

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```
