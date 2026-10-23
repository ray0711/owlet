"""Constants for the Owlet Smart Sock integration."""

from datetime import timedelta
from typing import Final

from .owletapi.exceptions import OwletAuthenticationError, OwletCredentialsError

DOMAIN: Final = "owlet"

CONF_OWLET_EXPIRY: Final = "expiry"
CONF_OWLET_REFRESH: Final = "refresh"
CONF_STALE_THRESHOLD: Final = "stale_threshold"
CONF_ACTIVATION_INTERVAL: Final = "activation_interval"
CONF_ALLOW_CONTROL: Final = "allow_control"

# Opt-in: lets Home Assistant switch the base station on and off. Off by default,
# then the client refuses every write except APP_ACTIVE.
DEFAULT_ALLOW_CONTROL: Final = False

# Seconds between two APP_ACTIVE writes per sock. APP_ACTIVE tells the base
# station a client is watching, which makes it upload vitals. Measured on a
# Dream Sock (base firmware 3.23.3): the base uploads for about 45 seconds
# after a write, then sets APP_ACTIVE back to 0 and stops. The client also
# writes again as soon as it sees that 0, so this interval is the ceiling.
DEFAULT_ACTIVATION_INTERVAL: Final = 30
MIN_ACTIVATION_INTERVAL: Final = 10
MAX_ACTIVATION_INTERVAL: Final = 600

REGIONS: Final = ["europe", "world"]
SUPPORTED_VERSIONS: Final = [2, 3]

# Seconds between polls of the Owlet cloud. Every poll costs several API calls
# per sock, so the default is deliberately more conservative than the minimum.
POLLING_INTERVAL: Final = 10
MIN_POLLING_INTERVAL: Final = 5

# Minutes without a fresh reading before vitals are treated as stale, 0 disables.
DEFAULT_STALE_THRESHOLD: Final = 5

# How long data has to stay stale before a repair issue is raised.
STALE_ISSUE_AFTER: Final = timedelta(hours=1)

# A reading timestamp further than this in the future is rejected. Small
# differences between the Owlet cloud's clock and the local one are normal.
MAX_FUTURE_SKEW: Final = timedelta(seconds=60)

# After a failed poll the next one is delayed exponentially (with jitter so
# many installations do not retry in lock step), up to this long.
BACKOFF_MAX: Final = timedelta(minutes=5)

MANUFACTURER: Final = "Owlet Baby Care"
SLEEP_STATES: Final = {0: "unknown", 1: "awake", 8: "light_sleep", 15: "deep_sleep"}

# Raw Ayla properties whose data_updated_at reflects a fresh vitals reading.
FRESHNESS_PROPERTIES: Final = (
    "REAL_TIME_VITALS",
    "HEART_RATE",
    "OXYGEN_LEVEL",
    "BATT_LEVEL",
)

# The Smart Sock 3 reports all vitals in one property. The Smart Sock 2 reports
# each vital as its own property with its own timestamp, so staleness has to be
# checked per vital there.
VITALS_PROPERTY_V3: Final = "REAL_TIME_VITALS"
VITAL_PROPERTIES_V2: Final = {
    "heart_rate": "HEART_RATE",
    "oxygen_saturation": "OXYGEN_LEVEL",
}

# Errors that mean the stored login is no longer valid. OwletEmailError and
# OwletPasswordError are subclasses of OwletCredentialsError.
OWLET_CREDENTIAL_ERRORS: Final = (OwletAuthenticationError, OwletCredentialsError)
