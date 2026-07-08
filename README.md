# Newcastle AI - Realie Tester v3

Adds corrected Realie testing tools:

- Property Search: `https://app.realie.ai/api/public/property/search/`
- Premium Comparables: `https://app.realie.ai/api/public/premium/comparables/`
- Uses header: `Authorization: <REALIE_API_KEY>`

## Streamlit Secrets

Keep existing keys and add:

```toml
REALIE_API_KEY = "your_realie_key_here"
```

## Testing Order

1. Test **Property Search** first.
2. If it returns latitude/longitude, test **Premium Comparables**.
3. Or use **Search + Auto Comps** to run both.
