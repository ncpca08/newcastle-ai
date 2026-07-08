# Newcastle AI - Realie Tester Patch

Adds a Realie API test section to the Streamlit sidebar.

## Streamlit Secrets
Add:

```toml
REALIE_API_KEY = "your_realie_key_here"
```

Keep your existing RentCast key for now.

## Testing
1. Test Realie Address Lookup first.
2. If it returns latitude/longitude, use those values in Realie Comparables Search.
