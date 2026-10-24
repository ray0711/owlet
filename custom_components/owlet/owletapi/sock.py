import datetime
import json
import logging
from logging import Logger
import math
import time
from typing import Any, NotRequired, TypedDict

from .api import OwletAPI, SockData, TokenDict
from .const import PROPERTIES, VALID_RANGES, VITALS_2, VITALS_3, Properties, PropertyKey
from .states import parse_sleep_string

logger: Logger = logging.getLogger(__package__)

TRUE_STRINGS = frozenset({"1", "true", "on", "yes"})
FALSE_STRINGS = frozenset({"0", "false", "off", "no"})


def decode_bool(value: Any) -> bool | None:
    """Decode an Ayla boolean, which arrives as 0/1, true/false or a string.

    Anything else, including None, is unknown rather than False.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and math.isfinite(value):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_STRINGS:
            return True
        if lowered in FALSE_STRINGS:
            return False
    return None


def decode_float(value: Any) -> float | None:
    """Decode a number, accepting numeric strings, rejecting everything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return decode_float(float(value.strip()))
        except ValueError:
            return None
    return None


def decode_int(value: Any) -> int | None:
    """Decode an integer, accepting whole floats and numeric strings."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        try:
            return decode_int(float(value.strip()))
        except ValueError:
            return None
    return None


def decode_str(value: Any) -> str | None:
    """Decode a string, anything else is unknown."""
    return value if isinstance(value, str) else None


DECODERS = {bool: decode_bool, float: decode_float, int: decode_int, str: decode_str}


def decode(data_type: type, key: PropertyKey, value: Any) -> Any:
    """Decode a raw value to the given type, turning sentinels into None."""
    decoded = DECODERS[data_type](value)
    bounds = VALID_RANGES.get(key)
    if bounds is None or decoded is None:
        return decoded
    low, high = bounds
    return decoded if low <= decoded <= high else None


def parse_timestamp(value: Any) -> datetime.datetime | None:
    """Parse an Ayla timestamp (ISO 8601, usually ...Z) to aware UTC.

    Naive or unreadable timestamps are None: without a time zone the age of a
    reading cannot be established.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.UTC)


class PropertiesDict(TypedDict):
    raw_properties: dict[str, dict[str, Any]]
    properties: Properties
    tokens: NotRequired[TokenDict]


class Sock:
    """Class representing a Owlet sock device.

    Attributes
    ----------
    name : str
        The product name
    mode: str
        the product model
    serial : str
        the serial number of the device
    oem_model : str
        The oem model of the device
    sw_version : str
        The software version of the device
    mac : str
        The mac address the device
    lan_ip : str
        The current lan ip address of the device
    connection_status : str
        The current connection status of the device
    device_type : str
        The device type
    manuf_model : str
        The manunfacturer's model number
    api : OwletAPI
        The current OwletAPI object being used to call the Owlet API
    raw_properties : dict
        A dictionionary containing the raw response from the device properties api call
    properties : dict
        A formatted, cut down version of the current device properties

    Methods
    -------
    get_property:
        returns a specific property for the device
    get_properties:
        returns all the properties for the current device
    normalise_properties:
        takes the raw_properties and strips out only the most important properties making the dict object smaller and easier to use
    update_properties
        uses the OwletAPI object to call the Owlet server and return the current properties of the device.

    """

    def __init__(
        self,
        api: OwletAPI,
        data: SockData,
    ) -> None:
        """Constructs an Owlet sock object representing the owlet sock device.

        Parameters
        ----------
        api (OwletAPI):OwletAPI object used to call the Owlet API
        data (dict):Data returned from the Owlet API showing the details of the sock

        """
        self._api = api
        self._name: str = data.get("product_name", "Owlet Baby Monitors")
        self._model: str = data.get("model", "")
        self._serial: str = data.get("dsn", "Unknown")
        self._oem_model: str = data.get("oem_model", "Unknown")
        self._sw_version: str = data.get("sw_version", "Unknown")
        self._mac: str = data.get("mac", "")
        self._lan_ip: str = data.get("lan_ip", "")
        self._connection_status: str = data.get("connection_status", "Unknown")
        self._device_type: str = data.get("device_type", "Wifi")
        self._manuf_model: str = data.get("manuf_model", "Unknown")
        self._version: int | None = None
        self._revision: int | None = None
        self._vitals_unreadable = False

        self._raw_properties: dict[str, dict[str, Any]] = {}
        self._properties: Properties = {}

    @property
    def api(self) -> OwletAPI:
        return self._api

    @property
    def version(self) -> int | None:
        return self._version

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def oem_model(self) -> str:
        return self._oem_model

    @property
    def sw_version(self) -> str:
        return self._sw_version

    @property
    def mac(self) -> str:
        return self._mac

    @property
    def lan_ip(self) -> str:
        return self._lan_ip

    @property
    def connection_status(self) -> str:
        return self._connection_status

    @property
    def device_type(self) -> str:
        return self._device_type

    @property
    def manuf_model(self) -> str:
        return self._manuf_model

    @property
    def properties(self) -> Properties:
        return self._properties

    @property
    def raw_properties(self) -> dict[str, dict[str, Any]]:
        return self._raw_properties

    @property
    def revision(self) -> int | None:
        return self._revision

    @property
    def last_updated_at(self) -> datetime.datetime | None:
        """Return when the Owlet cloud last received vitals from the sock, in UTC.

        The Smart Sock 3 reports all vitals in REAL_TIME_VITALS, the Smart Sock 2
        reports every vital as its own property, then the newest one is used.
        """
        keys = ["REAL_TIME_VITALS"]
        if "REAL_TIME_VITALS" not in self._raw_properties:
            keys = [key for vitals in VITALS_2.values() for key in vitals.values()]
        timestamps = [
            parsed
            for key in keys
            if (parsed := self.property_updated_at(key)) is not None
        ]
        return max(timestamps) if timestamps else None

    @property
    def sleep_codes(self) -> list[int | None]:
        """Return the per-minute sleep codes of MOBILE_VITALS, newest first."""
        return parse_sleep_string(self._raw_value("MOBILE_VITALS"))

    @property
    def sleep_updated_at(self) -> datetime.datetime | None:
        """Return when the sleep codes were last written, in UTC."""
        return self.property_updated_at("MOBILE_VITALS")

    def property_updated_at(self, raw_key: str) -> datetime.datetime | None:
        """Return the sanitised data_updated_at of a raw property, in UTC."""
        return parse_timestamp(
            self._raw_properties.get(raw_key, {}).get("data_updated_at")
        )

    def get_property(self, property: PropertyKey) -> bool | str | float | int | None:
        """Returns the specific property based on the property argument passed in.

        Parameters
        ----------
        property (str):The required property

        Returns
        -------
        (str):Request property as a string

        """
        return self._properties[property]

    def _raw_value(self, raw_key: str) -> Any:
        prop = self._raw_properties.get(raw_key)
        return prop.get("value") if isinstance(prop, dict) else None

    def _decode_vitals(self) -> dict[str, Any] | None:
        """Decode the REAL_TIME_VITALS JSON document, None when unreadable."""
        raw = self._raw_value("REAL_TIME_VITALS")
        vitals: Any = None
        if isinstance(raw, str):
            try:
                vitals = json.loads(raw)
            except ValueError:
                vitals = None
        if not isinstance(vitals, dict):
            if not self._vitals_unreadable:
                logger.warning(
                    "Sock %s sent unreadable vitals, showing them as unknown: %r",
                    self._serial,
                    raw,
                )
            self._vitals_unreadable = True
            return None
        self._vitals_unreadable = False
        return vitals

    async def _normalise_properties(
        self,
    ) -> Properties:
        """Takes the raw properties dictionary returned from the API and formats it into another dict that is more stripped down and easier to work with.

        A key is present when the sock reports the property. Its value is None
        when the cloud sent nothing usable for it (null, wrong type, or a
        sentinel such as a heart rate of 0), never a made-up value. One bad
        field never breaks the rest of the update.

        Returns
        -------
            (dict):Returns the stripped down properties as a dict

        """
        properties: Properties = {}

        for data_type, properties_tmp in PROPERTIES.items():
            for key, raw_key in properties_tmp.items():
                if raw_key in self._raw_properties:
                    properties[key] = decode(data_type, key, self._raw_value(raw_key))

        if self._version == 3:
            vitals = self._decode_vitals()
            for data_type, vitals_list in VITALS_3.items():
                for vital_desc, vital_key in vitals_list.items():
                    if vitals is None:
                        properties[vital_desc] = None
                    elif vital_key in vitals:
                        properties[vital_desc] = decode(
                            data_type, vital_desc, vitals[vital_key]
                        )

            updated = self.property_updated_at("REAL_TIME_VITALS")
            if updated is not None:
                properties["last_updated"] = updated.strftime("%Y/%m/%d %H:%M:%S")

        if self._version == 2:
            for data_type, vitals_list in VITALS_2.items():
                for vital_desc, vital_key in vitals_list.items():
                    if vital_key in self._raw_properties:
                        properties[vital_desc] = decode(
                            data_type, vital_desc, self._raw_value(vital_key)
                        )

        return properties

    async def _check_version(self) -> None:
        version = 0
        if "REAL_TIME_VITALS" in self._raw_properties:
            version = 3
        elif "CHARGE_STATUS" in self._raw_properties:
            version = 2
        self._version = version

    async def _check_revision(self) -> None:
        """Read the hardware revision from oem_sock_version, None when unreadable."""
        raw = self._raw_value("oem_sock_version")
        data: Any = None
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
        revision = data.get("rev") if isinstance(data, dict) else None
        self._revision = revision if isinstance(revision, int) else None

    async def update_properties(
        self,
    ) -> PropertiesDict:
        """Calls the Owlet api to update the properties and then returns the raw response dict, the formatted dict from
        normalise_properties and any new api tokens if they have changed.

        Returns
        -------
        (dict):Dictionary containing three dictionaries, one with the raw json response from the API and another with the stripped down
        properties from normalise_properties, the third will contain the new api tokens if they have changed, if they haven't changed this will be None

        """
        properties = await self._api.get_properties(self.serial)
        self._raw_properties = properties["response"]
        # Keep detecting until the sock identified itself, a first snapshot
        # without the telltale properties must not pin the version forever.
        if not self._version:
            await self._check_version()
        if self._revision is None and self._version == 3:
            await self._check_revision()
        self._properties = await self._normalise_properties()

        response: PropertiesDict = {
            "raw_properties": self._raw_properties,
            "properties": self._properties,
        }

        if "tokens" in properties:
            response["tokens"] = properties["tokens"]

        return response

    async def control_base_station(self, on: bool) -> bool:
        """Calls the Owlet api to turn the base station on or off, returns a bool if this was successful.

        Refused with OwletReadOnlyError unless the API client was created with
        allow_control=True.

        Returns
        -------
        (bool):Was the command successful

        """
        value = json.dumps(
            {"ts": int(time.time()), "val": "true" if on else "false"},
        )
        data = {"datapoint": {"metadata": {}, "value": value}}

        response = await self._api.post_command(
            self.serial,
            "BASE_STATION_ON_CMD",
            data,
        )

        return True if response else False
