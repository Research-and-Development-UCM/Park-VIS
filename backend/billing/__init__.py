"""Billing integration package.

Handles HWID generation, license session caching, and outbound calls
to the Park VIS cloud billing portal. The on-prem app authenticates
to billing exclusively via HWID-in-body — no JWTs are stored here.
"""