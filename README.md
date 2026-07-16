# Newcastle OS — Realie.ai Wholesale Analyzer

Streamlit wholesale acquisition platform for Newcastle Partners CA LLC.

## Live workflow
1. Enter a complete US property address.
2. Realie.ai Address Lookup retrieves the subject property and coordinates.
3. Realie.ai Premium Comparables searches sold comps using:
   - 0.50-mile default radius
   - Same property type
   - Exact bedroom and bathroom counts
   - ±300 square feet by default
   - Six months first
   - Twelve-month fallback only when the minimum comp count is not met
4. Newcastle OS calculates average comp ARV, PSF validation, buyer ceiling, and wholesale MAO.
5. The saved analysis transfers into the Fillout contract builder.

## Streamlit secrets
```toml
REALIE_API_KEY = "your_realie_api_key"
FILLOUT_FORM_URL = "https://form.fillout.com/t/your-form"
```

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```
