# Newcastle AI Realie Analyzer V8

Adds bulletproof comp handling:

- Comparable sales are displayed newest sold first.
- Same property type lock remains enforced.
- Uses a verified sale event parser to avoid showing non-sale transfer dates as comp sold dates.
- Runs a Realie comparable sweep instead of stopping too early.
- Keeps ±300 sqft as the preferred comp range and starts with 6 months before using 12-month backup.
- ARV still uses best scored comps while the comp table displays newest sales first.
- Buyer/current owner column remains included.
