# Newcastle AI - AVM Comps Display Fix

Fixes comp display by treating RentCast AVM comparable sale listings as the comp source and using property records only for buyer/current-owner enrichment.

Key changes:
- AVM comps now accept sale date fields like `lastSeenDate`, `removedDate`, or `daysOld` when `soldDate` is unavailable.
- Property-record sale history no longer deletes AVM comps.
- Offer boxes show true 75/70/65/60 of ARV before repairs.
- Buyer/current owner enrichment remains in the table when address matching is available.
