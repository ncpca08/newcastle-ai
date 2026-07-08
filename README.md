# Newcastle AI V11

This version tightens the comp engine and Fillout setup.

## Comp Engine Rules
- Pulls a larger pool from Realie in the background.
- Same property type is locked.
- Prefers sold comps within 6 months.
- Uses 12-month backup only when 6-month comps are too limited.
- Selects only the best 5–6 comps by match score for ARV and display.
- Displays selected comps newest sold first.

## Streamlit Secrets
Add these in Streamlit → Manage app → Settings → Secrets:

```toml
REALIE_API_KEY = "your_realie_key"

FILLOUT_API_KEY = "your_fillout_api_key"
FILLOUT_FORM_URL = "https://newoffer.fillout.com/rpa"
FILLOUT_FORM_ID = "your_fillout_form_id"

[users.marco]
password = "your_password"
role = "admin"
email = "Marco@NewcastlePartnersCA.com"

[users.doreen]
password = "temporary_password"
role = "va"
email = "doreen"
```

Fillout submission still requires mapping exact field IDs/names from your form before live auto-submit.
