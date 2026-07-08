# Newcastle AI Acquisition Analyzer

Patch: removes DealMachine API tester and removes active DealMachine API calls from the app.

Keep only supported API keys in Streamlit Secrets, for example:

```toml
RENTCAST_API_KEY = "your_rentcast_key"
DEALRUN_API_KEY = "your_dealrun_key"
```

Do not keep `DEALMACHINE_API_KEY` in Streamlit Secrets unless DealMachine provides official public API documentation/permission.
