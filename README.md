# Newcastle AI - Realie Analyzer V5

Realie-powered acquisition analyzer.

## Streamlit Secrets

Add your Realie key:

```toml
REALIE_API_KEY = "your_realie_key_here"
```

## What V5 Adds

- Analyze Property button uses Realie Property Search + Premium Comparables
- Same property type lock
- Starts with strict comp rules: 0.5 miles, 6 months, +/-300 sqft
- Progressive fallback: 12 months, then 1 mile, then propertyType any only if needed
- Buyer / Current Owner column in comps table
- Clickable / expandable comp rows
- Buyer/owner details per comp
- Call/SMS launcher using tel: and sms: links
