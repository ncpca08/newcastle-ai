# Newcastle AI Realie Analyzer V7

Adds:
- Login page with admin/VA roles
- Manual lead queue with status colors, assignment, lead lock, and notes
- Cleaner sidebar with Realie badge hidden
- Smarter address parsing for comma and non-comma lead formats
- Conservative / Expected / Aggressive ARV tiers
- SMS template auto-fills seller name and street address

## Streamlit Secrets

Keep your Realie key:

```toml
REALIE_API_KEY = "your_realie_key_here"

[users.marco]
name = "Marco"
password = "choose_a_password"
role = "admin"

[users.doreen]
name = "Doreen"
password = "choose_a_password"
role = "va"
```

If `[users]` is not configured yet, the app will open as Marco/admin so you do not get locked out.
