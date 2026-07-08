# Newcastle AI - Comp Validation + Offer Fix

Fixes two issues:

1. **Offer Matrix** now shows the true gross ARV percentages:
   - 75% ARV = ARV × 0.75
   - 70% ARV = ARV × 0.70
   - 65% ARV = ARV × 0.65
   - 60% ARV = ARV × 0.60

   Repair-adjusted values are shown underneath each tier instead of replacing the tier number.

2. **Comp validation** now rejects stale/conflicting sale records:
   - Property records are not allowed to create comps.
   - AVM sale comps are checked against matching property-record sale history when available.
   - If the record history conflicts with the AVM sale date/price, the comp is rejected and shown in a troubleshooting expander.

Upload these files to GitHub and replace the existing app.py.
