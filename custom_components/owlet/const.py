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
CONF_CAPTURE_RAW: Final = "capture_raw"

# Opt-in: writes the raw cloud properties of every sock to the Home Assistant
# log, to learn what the base station and sock report. Off by default.
DEFAULT_CAPTURE_RAW: Final = False

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

# Raw Ayla properties whose value is included in diagnostics and in the raw
# capture. Every other property is listed by name with its type and timestamp
# only: file properties carry URLs with device ids, CONFIG_STATUS carries MAC
# addresses, and the Smart Sock 2 profile properties carry the baby's name,
# birth date and location.
VALUE_ALLOWED: Final = frozenset(
    {
        "APP_ACTIVE",
        "REAL_TIME_VITALS",
        "HIGH_HR_ALRT",
        "LOW_HR_ALRT",
        "HIGH_OX_ALRT",
        "LOW_OX_ALRT",
        "CRIT_OX_ALRT",
        "LOW_BATT_ALRT",
        "CRIT_BATT_ALRT",
        "LOST_POWER_ALRT",
        "SOCK_DISCON_ALRT",
        "SOCK_OFF",
        "DISCOMFORT_ALRT",
        "LOW_INTEG_READ",
        "PREVIEW_HIGH_PRIORITY_ALARM",
        "PREVIEW_MED_PRIORITY_ALARM",
        "PREVIEW_LOW_PRIORITY_ALARM",
        "PREVIEW_DRM_PRIORITY_ALARM",
        "BASE_MON_STATUS",
        "BATTERY_STATUS",
        "DEVICE_PING",
        "FW_UPDATE_STATUS",
        "oem_sock_version",
        "oem_base_version",
        "oem_flash_version",
        # Properties the Owlet app reads from a Smart Sock 3 / Dream Sock:
        # the sleep history, perfusion index, alarm limits and session time.
        "MOBILE_VITALS",
        "PERFUSION_INDEX",
        "ALARM_HEART_RATE_LOW_THRESHOLD",
        "ALARM_HEART_RATE_HIGH_THRESHOLD",
        "ALARM_OXYGEN_LOW_THRESHOLD",
        "ALARM_OXYGEN_HIGH_THRESHOLD",
        "ALARM_ON_MASK",
        "MONITORING_TIME_SESSION",
        "DATA_NOT_UPDATING_ALARM",
        # Smart Sock 2
        "HEART_RATE",
        "OXYGEN_LEVEL",
        "BATT_LEVEL",
        "MOVEMENT",
        "CHARGE_STATUS",
        "BLE_RSSI",
        "BASE_STATION_ON",
        "SOCK_CONNECTION",
        "SOCK_REC_PLACED",
        "ALRTS_DISABLED",
        "LOW_PA_ALRT",
        "NURSERY_MODE",
        "OTA_STATUS",
        "OTA_ERROR",
    }
)

# Errors that mean the stored login is no longer valid. OwletEmailError and
# OwletPasswordError are subclasses of OwletCredentialsError.
OWLET_CREDENTIAL_ERRORS: Final = (OwletAuthenticationError, OwletCredentialsError)
