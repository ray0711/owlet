"""Diagnostics support for Owlet.

Built as an allowlist: only fields known to be harmless are included, so a
property Owlet adds tomorrow can never leak a token, an email address, a
serial number, a MAC address or the baby's details into a diagnostics
download. The allowlist is VALUE_ALLOWED in const.py, shared with the raw
capture.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_REGION
from homeassistant.core import HomeAssistant

from . import OwletConfigEntry
from .capture import describe_property


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OwletConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    socks = []
    for index, coordinator in enumerate(entry.runtime_data.values()):
        sock = coordinator.sock
        last_updated = coordinator.last_updated
        socks.append(
            {
                "index": index,
                "version": getattr(sock, "version", None),
                "revision": getattr(sock, "revision", None),
                "model": getattr(sock, "model", None),
                "oem_model": getattr(sock, "oem_model", None),
                "sw_version": getattr(sock, "sw_version", None),
                "connection_status": getattr(sock, "connection_status", None),
                "last_update_success": coordinator.last_update_success,
                "last_updated": last_updated.isoformat() if last_updated else None,
                "vitals_fresh": coordinator.vitals_fresh,
                "stale_reason": coordinator.stale_reason,
                "readings_expected": coordinator.readings_expected,
                "is_stale": coordinator.is_stale,
                "properties": dict(sock.properties),
                "raw_properties": {
                    name: describe_property(name, prop)
                    for name, prop in (sock.raw_properties or {}).items()
                },
            }
        )

    return {
        "entry": {
            "version": entry.version,
            "data": {CONF_REGION: entry.data.get(CONF_REGION)},
            "options": dict(entry.options),
        },
        "socks": socks,
    }
