"""Constants of the eWebtel integration."""

from datetime import timedelta

DOMAIN = "ewebtel"
MANUFACTURER = "Plum"

# The meter reports in batches a few times a day; hourly polling catches
# each batch within an hour.
UPDATE_INTERVAL = timedelta(hours=1)
# How far back the first import looks for history.
HISTORY_DAYS = 3 * 365
# Every update re-reads this much so the daily sensors have their midnights.
RECENT_DAYS = 2
