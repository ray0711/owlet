from typing import Literal, TypedDict

PropertyKey = Literal[
    "high_oxygen_alert",
    "low_battery_alert",
    "low_heart_rate_alert",
    "high_heart_rate_alert",
    "low_oxygen_alert",
    "ppg_log_file",
    "firmware_update_available",
    "lost_power_alert",
    "sock_disconnected",
    "sock_off",
    "discomfort_alert",
    "low_integrity_reading",
    "preview_high_priority_alarm",
    "preview_medium_priority_alarm",
    "preview_low_priority_alarm",
    "preview_drm_priority_alarm",
    "base_monitoring_status",
    "battery_status",
    "oxygen_saturation",
    "heart_rate",
    "battery_percentage",
    "battery_minutes",
    "signal_strength",
    "oxygen_10_av",
    "base_station_on",
    "sock_connection",
    "skin_temperature",
    "sleep_state",
    "movement",
    "alert_paused_status",
    "charging",
    "alerts_mask",
    "update_status",
    "readings_flag",
    "brick_status",
    "movement_bucket",
    "wellness_alert",
    "monitoring_start_time",
    "base_battery_status",
    "hardware_version",
    "last_updated",
    "critical_battery_alert",
    "critical_oxygen_alert",
]


class Properties(TypedDict, total=False):
    """Normalised properties. A key is present when the sock reports the
    property; its value is None when the cloud sent nothing usable for it."""

    critical_battery_alert: bool | None
    critical_oxygen_alert: bool | None
    high_oxygen_alert: bool | None
    low_battery_alert: bool | None
    low_heart_rate_alert: bool | None
    high_heart_rate_alert: bool | None
    low_oxygen_alert: bool | None
    ppg_log_file: bool | None
    firmware_update_available: bool | None
    lost_power_alert: bool | None
    sock_disconnected: bool | None
    sock_off: bool | None
    discomfort_alert: bool | None
    low_integrity_reading: bool | None
    preview_high_priority_alarm: bool | None
    preview_medium_priority_alarm: bool | None
    preview_low_priority_alarm: bool | None
    preview_drm_priority_alarm: bool | None
    base_monitoring_status: int | None
    battery_status: int | None
    oxygen_saturation: float | None
    heart_rate: float | None
    battery_percentage: float | None
    battery_minutes: float | None
    signal_strength: float | None
    oxygen_10_av: float | None
    base_station_on: bool | None
    sock_connection: int | None
    skin_temperature: int | None
    sleep_state: int | None
    movement: int | None
    alert_paused_status: int | None
    charging: int | None
    alerts_mask: int | None
    update_status: int | None
    readings_flag: int | None
    brick_status: int | None
    movement_bucket: int | None
    wellness_alert: int | None
    monitoring_start_time: int | None
    base_battery_status: int | None
    hardware_version: str | None
    last_updated: str | None


# Stand-alone Ayla properties, grouped by the type they are decoded to.
PROPERTIES: dict[type, dict[PropertyKey, str]] = {
    bool: {
        "high_oxygen_alert": "HIGH_OX_ALRT",
        "low_battery_alert": "LOW_BATT_ALRT",
        "low_heart_rate_alert": "LOW_HR_ALRT",
        "high_heart_rate_alert": "HIGH_HR_ALRT",
        "low_oxygen_alert": "LOW_OX_ALRT",
        "ppg_log_file": "PPG_LOG_FILE",
        "firmware_update_available": "FW_UPDATE_STATUS",
        "lost_power_alert": "LOST_POWER_ALRT",
        "sock_disconnected": "SOCK_DISCON_ALRT",
        "sock_off": "SOCK_OFF",
        "critical_battery_alert": "CRIT_BATT_ALRT",
        "critical_oxygen_alert": "CRIT_OX_ALRT",
        "discomfort_alert": "DISCOMFORT_ALRT",
        "low_integrity_reading": "LOW_INTEG_READ",
        "preview_high_priority_alarm": "PREVIEW_HIGH_PRIORITY_ALARM",
        "preview_medium_priority_alarm": "PREVIEW_MED_PRIORITY_ALARM",
        "preview_low_priority_alarm": "PREVIEW_LOW_PRIORITY_ALARM",
        "preview_drm_priority_alarm": "PREVIEW_DRM_PRIORITY_ALARM",
    },
    int: {
        "base_monitoring_status": "BASE_MON_STATUS",
        "battery_status": "BATTERY_STATUS",
    },
}

# Keys inside the REAL_TIME_VITALS JSON of the Smart Sock 3 / Dream Sock.
VITALS_3: dict[type, dict[PropertyKey, str]] = {
    float: {
        "oxygen_saturation": "ox",
        "heart_rate": "hr",
        "battery_percentage": "bat",
        "battery_minutes": "btt",
        "signal_strength": "rsi",
        "oxygen_10_av": "oxta",
    },
    bool: {
        "base_station_on": "bso",
    },
    int: {
        "sock_connection": "sc",
        "skin_temperature": "st",
        "sleep_state": "ss",
        "movement": "mv",
        "alert_paused_status": "aps",
        "charging": "chg",
        "alerts_mask": "alrt",
        "update_status": "ota",
        "readings_flag": "srf",
        "brick_status": "sb",
        "movement_bucket": "mvb",
        "wellness_alert": "onm",
        "monitoring_start_time": "mst",
        "base_battery_status": "bsb",
    },
    str: {
        "hardware_version": "hw",
    },
}

VITALS_3_OTHER: dict[PropertyKey, str] = {
    "last_updated": "data_updated_at",
}

VITALS_2: dict[type, dict[PropertyKey, str]] = {
    float: {
        "signal_strength": "BLE_RSSI",
    },
    bool: {
        "base_station_on": "BASE_STATION_ON",
        "sock_connection": "SOCK_CONNECTION",
    },
    int: {
        "oxygen_saturation": "OXYGEN_LEVEL",
        "heart_rate": "HEART_RATE",
        "battery_percentage": "BATT_LEVEL",
        "movement": "MOVEMENT",
        "charging": "CHARGE_STATUS",
        "update_status": "OTA_STATUS",
    },
    str: {
        "hardware_version": "oem_sock_version",
    },
}

# Readings outside these ranges are sentinels, not measurements: the sock
# reports 0 for heart rate, oxygen and skin temperature while it is not
# measuring (charging, not worn) and 255 for the ten minute oxygen average
# until it has ten minutes of data. They are decoded to None so they are never
# recorded as real values.
VALID_RANGES: dict[PropertyKey, tuple[float, float]] = {
    "heart_rate": (1, 300),
    "oxygen_saturation": (1, 100),
    "oxygen_10_av": (1, 100),
    "skin_temperature": (1, 60),
}

# The only properties this client is allowed to write. APP_ACTIVE tells the
# base station that a client is watching, which makes it upload vitals.
# Nothing else is ever written, so the base station's own behaviour (alarms,
# on/off) can never be changed from Home Assistant.
WRITABLE_PROPERTIES: frozenset[str] = frozenset({"APP_ACTIVE"})

# Written only when the client is created with allow_control=True (opt-in in
# the integration's options): the command that switches the base station.
CONTROL_PROPERTIES: frozenset[str] = frozenset({"BASE_STATION_ON_CMD"})

REGION_INFO: dict[str, dict[str, str]] = {
    "world": {
        "url_mini": "https://ayla-sso.owletdata.com/mini/",
        "url_signin": "https://user-field-1a2039d9.aylanetworks.com/api/v1/token_sign_in",
        "url_refresh": "https://user-field-1a2039d9.aylanetworks.com/users/refresh_token.json",
        "url_base": "https://ads-field-1a2039d9.aylanetworks.com/apiv1",
        "apiKey": "AIzaSyCsDZ8kWxQuLJAMVnmEhEkayH1TSxKXfGA",
        "app_id": "sso-prod-3g-id",
        "app_secret": "sso-prod-UEjtnPCtFfjdwIwxqnC0OipxRFU",
    },
    "europe": {
        "url_mini": "https://ayla-sso.eu.owletdata.com/mini/",
        "url_signin": "https://user-field-eu-1a2039d9.aylanetworks.com/api/v1/token_sign_in",
        "url_refresh": "https://user-field-eu-1a2039d9.aylanetworks.com/users/refresh_token.json",
        "url_base": "https://ads-field-eu-1a2039d9.aylanetworks.com/apiv1",
        "apiKey": "AIzaSyDm6EhV70wudwN3iOSq3vTjtsdGjdFLuuM",
        "app_id": "OwletCare-Android-EU-fw-id",
        "app_secret": "OwletCare-Android-EU-JKupMPBoj_Npce_9a95Pc8Qo0Mw",
    },
}
