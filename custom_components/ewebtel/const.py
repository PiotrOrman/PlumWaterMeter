"""Constants of the eWebtel integration."""

from datetime import timedelta

DOMAIN = "ewebtel"
MANUFACTURER = "Plum"

# Hourly polling picks up each batch within an hour of its arrival.
UPDATE_INTERVAL = timedelta(hours=1)
# How far back the first import looks for history.
HISTORY_DAYS = 3 * 365
# The meter sends one batch a day (just after 05:00 local), so the newest
# reading can be ~24 h old. Every update re-reads this much so the daily
# sensors still reach 24 h before it.
RECENT_DAYS = 3
# A meter whose newest reading is older than this raises a repair issue:
# the daily batch did not arrive.
STALE_AFTER = timedelta(hours=30)
