"""Check that every entity and enum option has a translation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.owlet import binary_sensor, sensor
from custom_components.owlet.owletapi.states import SLEEP_OPTIONS

COMPONENT = Path(__file__).parent.parent / "custom_components" / "owlet"
LANGUAGES = ("en", "nl", "fr")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(node: object, prefix: str = "") -> set[str]:
    """Return every key path of a nested dict."""
    if not isinstance(node, dict):
        return {prefix}
    paths: set[str] = set()
    for key, value in node.items():
        paths |= _paths(value, f"{prefix}.{key}" if prefix else key)
    return paths


def _sensor_descriptions() -> list:
    return [
        *sensor.SENSORS,
        *sensor.DERIVED_SENSORS,
        sensor.OwletSleepSensor.entity_description,
        sensor.OwletOxygenAverageSensor.entity_description,
        sensor.OwletSleepHistorySensor.entity_description,
        sensor.OwletLastUpdatedSensor.entity_description,
        sensor.OwletLastRedAlertSensor.entity_description,
    ]


def _binary_descriptions() -> list:
    return [
        *binary_sensor.SENSORS,
        *binary_sensor.DERIVED_BINARY_SENSORS,
        binary_sensor.OwletAwakeSensor.entity_description,
        binary_sensor.OwletStaleSensor.entity_description,
    ]


def _options(description: object) -> list[str]:
    """Return the enum options of a description (the sleep sensor sets them on the class)."""
    if description is sensor.OwletSleepSensor.entity_description:
        return list(SLEEP_OPTIONS)
    return list(getattr(description, "options", None) or [])


@pytest.mark.parametrize("language", ("strings", *LANGUAGES))
def test_every_entity_key_is_translated(language: str) -> None:
    """Each translation_key has a name, each enum option a state label."""
    path = COMPONENT / (
        "strings.json" if language == "strings" else f"translations/{language}.json"
    )
    entity = _load(path)["entity"]
    for platform, descriptions in (
        ("sensor", _sensor_descriptions()),
        ("binary_sensor", _binary_descriptions()),
    ):
        for description in descriptions:
            assert description.translation_key, description.key
            entry = entity[platform].get(description.translation_key)
            assert entry is not None, f"{platform}.{description.translation_key}"
            assert entry.get("name"), f"{platform}.{description.translation_key}"
            for option in _options(description):
                assert option in entry.get("state", {}), (
                    f"{platform}.{description.translation_key}.state.{option}"
                )


def test_enum_options_are_collected() -> None:
    """Guard the helper: the enum sensors do expose their options."""
    keys = {d.translation_key: _options(d) for d in _sensor_descriptions()}
    assert "sock_recently_placed" in keys["status"]
    assert "no_data" in keys["sleepstate"]
    assert keys["session_started"] == []


def test_translation_files_have_the_same_keys() -> None:
    """en.json equals strings.json apart from shared references, nl/fr match en."""
    strings = _load(COMPONENT / "strings.json")
    english = _load(COMPONENT / "translations/en.json")
    assert _paths(strings) == _paths(english)
    # Everything except the [%key:...%] references is identical.
    for path in _paths(strings):
        a: object = strings
        b: object = english
        for part in path.split("."):
            a, b = a[part], b[part]
        if not str(a).startswith("[%key:"):
            assert a == b, path
    for language in ("nl", "fr"):
        assert _paths(_load(COMPONENT / f"translations/{language}.json")) == _paths(
            english
        ), language
