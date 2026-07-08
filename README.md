# Newcastle AI - Repliers API Tester Patch

Adds a temporary Repliers API tester to the Streamlit sidebar.

## Add to Streamlit Secrets

```toml
REPLIERS_API_KEY = "your_repliers_key_here"
```

Keep your existing RentCast key. DealRun can stay or be removed later.

## First test
- Base URL: `https://api.repliers.io`
- Endpoint path: `/`
- Method: `GET`
- Auth style: `X-API-Key`

If that fails, try `REPLIERS-API-KEY`, `Authorization Bearer`, and `api_key query`.
