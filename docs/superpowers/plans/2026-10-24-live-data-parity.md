# Live Data Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the live Dream Sock / Smart Sock 3 state as the Owlet app does: decoded status entities, vitals hidden when the sock is not worn, the app's 20 s freshness rule, per-minute sleep sensors and a first version of the app's status line.

**Architecture:** Pure decoders and the status rules live in a new HA-free module `owletapi/states.py`. The coordinator gains `readings_valid`, a seconds-based stale threshold and `status`. Entities read `sock.properties` through two generic "derived" description classes. No new API requests.

**Tech Stack:** Python 3.13+, Home Assistant custom integration (pytest-homeassistant-custom-component 0.13.372, HA 2026.10.1), ruff 0.17.0.

**Spec:** `docs/superpowers/specs/2026-10-24-live-data-parity-design.md` (revision 4, approved). Section numbers below refer to it.

**Working directory and test command** (all paths relative to the repository root, branch `live-data-parity`):
`PYTHONPATH=<path-to-test-libs>:$PWD python3 -m pytest -q -p no:cacheprovider` (do not use `python3 -I`). Lint: `python3 -m ruff check . && python3 -m ruff format --check .` with the same `PYTHONPATH`. Commit messages end with the attribution lines the session requires; commit only when a task says so.

## Global Constraints

- Read-only: the integration writes `APP_ACTIVE` only, never `APP_CMD_REQUEST`, `ALRT_PAUSE_CMD`, `SETTINGS_CMD`. No new API requests, no polling or `APP_ACTIVE` cadence change.
- Existing entity ids and unique ids (`{serial}-{key}`) do not change. Unique ids of new entities are `{serial}-{key}` with the keys of spec section 7.
- Freshness: `stale_threshold` in seconds, default 20 for version 3 sockets; version 2 sockets use at least 300 s; the option is 0 (disabled) or at least 15 and at least the polling interval plus 10, maximum 7200. Config entry version 3.
- Worn sock: `readings_valid` is false on a placement alarm (`SOCK_OFF` flag, `alrt` bit 64 or bit 16) everywhere, and also when `readings_flag` (`srf`) is present and not 0 on hardware `obs4` only.
- Alert sensors: the separate flag decides, the `alrt` bit only when the flag value is None. Entity creation for existing alert sensors does not change.
- Sleep codes by band: 0 `no_data`, 1 to 5 `awake`, 6 to 10 `light_sleep`, 11 to 15 `deep_sleep`, anything else `unknown`; the raw code is always kept.
- Never invent values: absent or malformed input gives `None`/`unknown`; no exception reaches the coordinator from decoding.
- Translations in English, Dutch and French for every new entity and enum state. Coverage stays at or above 93 %. Ruff clean (`target-version = "py313"`, rules in `pyproject.toml`).

## Review Focus

Failure modes the spec implies; each has a test in the named task.
1. `MOBILE_VITALS` empty, missing or malformed after a base reboot: sleep sensors `unknown`, no history, no crash (Tasks 1, 8).
2. `REAL_TIME_VITALS` unreadable or most keys missing (`properties` full of `None`): `status` is `unknown`, `readings_valid` stays true, nothing raises (Tasks 2, 4).
3. `hw` missing or not `obs4` with `srf` not 0: vitals stay available (Task 4).
4. Stored `stale_threshold` missing, 0, huge or in the old unit after an upgrade, and a polling interval raised above `threshold - 10` (Task 5).
5. Missing, naive or future timestamps on `REAL_TIME_VITALS` or `MOBILE_VITALS`: `status` is `trouble_getting_data`, sleep sensors unavailable, history times never raise (Tasks 4, 8).

## File Structure

- Create `custom_components/owlet/owletapi/states.py`: decoders, tables, `compute_status`.
- Modify `owletapi/const.py` (keys), `owletapi/sock.py` (sleep accessors), `const.py` (threshold constants), `coordinator.py`, `__init__.py` (migration), `config_flow.py`, `sensor.py`, `binary_sensor.py`, `diagnostics.py`, `strings.json`, `translations/{en,nl,fr}.json`, `README.md`, `CHANGELOG.md`.
- Create tests `tests/owletapi/test_states.py`, `tests/test_translations.py`, fixtures `tests/fixtures/live_*.json`; modify existing tests named in the tasks.

---

### Task 1: Decoders in `states.py`

**Files:**
- Create: `custom_components/owlet/owletapi/states.py`
- Test: `tests/owletapi/test_states.py`

**Interfaces:**
- Produces (all pure, never raise):
  - `decode_body_position(code: int | None) -> str | None` (`moving`, `undetected`, `no_data`, `on_back`, `side`, `tummy`; None stays None)
  - `decode_reading_flag(code: int | None) -> str | None` (`normal`, `no_valid_readings`, `kicking`, `just_placed`, else `unknown`)
  - `decode_base_state(code: int | None) -> str | None` (`on_station` for 8, else `unknown`)
  - `decode_base_battery(code: int | None) -> str | None` (`not_charging` 0, `charging` 1, `charged` 2, `fault` 3, any other int `not_present`)
  - `decode_sleep_code(code: int | None) -> str | None` (bands above)
  - `parse_sleep_string(raw: Any) -> list[int | None]` (raw is the `MOBILE_VITALS` value, a JSON string `{"sh": "..."}`; newest first; a non-hex character gives `None` at its place; anything unreadable gives `[]`)
  - `@dataclass(frozen=True) SleepPoint(time: datetime, raw: int | None, state: str)` and `sleep_history(codes: list[int | None], updated: datetime) -> list[SleepPoint]` (time = `updated` minus 60 s per index)
  - `ALERT_BITS: dict[str, int]` keyed by Properties key: `low_oxygen_alert` 1, `high_oxygen_alert` 2, `low_heart_rate_alert` 4, `high_heart_rate_alert` 8, `sock_disconnected` 16, `low_integrity_reading` 32, `sock_off` 64, `low_battery_alert` 256, `preview_high_priority_alarm` 512, `preview_medium_priority_alarm` 1024, `preview_low_priority_alarm` 2048, `discomfort_alert` 4096, `lost_power_alert` 8192; `BIT_DATA_NOT_UPDATING = 128`; `PLACEMENT_BITS = 64 | 16`
  - `alert_active(flag: bool | None, mask: int | None, bit: int) -> bool | None` (flag if not None, else the bit of the mask if the mask is not None, else None)
  - `*_OPTIONS: tuple[str, ...]` for each enum (used by the enum sensors)

- [ ] **Step 1: Write the failing tests** in `tests/owletapi/test_states.py`: `test_decode_sleep_code_bands` (parametrized: 0 `no_data`; 1, 5 `awake`; 6, 8, 10 `light_sleep`; 11, 15 `deep_sleep`; 16, -1 `unknown`; None None), `test_decode_body_position` (0, 3 `no_data`; 1 `moving`; 2 `undetected`; 4, 5 `on_back`; 6, 9 `side`; 10, 11 `tummy`; -1 `tummy`), `test_decode_base_battery` (0 to 3 named, 4 `not_present`, None None), `test_decode_reading_flag`, `test_decode_base_state` (8 `on_station`, 65 and 9 `unknown`), `test_parse_sleep_string` (`'{"sh":"188F"}'` gives `[1, 8, 8, 15]`; `'{"sh":""}'`, `'{"sh":"8G8"}'` gives `[8, None, 8]`, `None`, `"x"`, `'{"a":1}'`, `'[]'` give `[]`; Review Focus 1), `test_sleep_history_times` (three codes, `updated` 14:28:03 gives 14:28:03, 14:27:03, 14:26:03), `test_alert_active` (flag wins over mask both ways; mask used only when flag is None; both None gives None).
- [ ] **Step 2: Run** `pytest tests/owletapi/test_states.py -q`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement** the signatures above in `states.py`. Use `int(c, 16)` per character for the sleep string; `json.loads` guarded by `except ValueError`.
- [ ] **Step 4: Run** the same command. Expected: all pass. Run ruff.
- [ ] **Step 5: Commit** `git add custom_components/owlet/owletapi/states.py tests/owletapi/test_states.py`; message `Add pure status decoders`.

---

### Task 2: Status rules (`compute_status`)

**Files:**
- Modify: `custom_components/owlet/owletapi/states.py`
- Test: `tests/owletapi/test_states.py`

**Interfaces:**
- Consumes: Task 1 constants.
- Produces: `@dataclass(frozen=True) StatusInputs(fresh: bool, charging: int | None, soft_brick: int | None, base_on: bool | None, sock_connection: int | None, readings_flag: int | None, battery_minutes: float | None, alerts: Mapping[str, bool | None])` where `alerts` holds the already resolved activity (flag first, mask fallback) for `critical_oxygen_alert`, `high_oxygen_alert`, `low_oxygen_alert`, `high_heart_rate_alert`, `low_heart_rate_alert`, `discomfort_alert`, `sock_off`, `low_integrity_reading`, `sock_disconnected`; `compute_status(data: StatusInputs | None) -> str`; `STATUS_OPTIONS: tuple[str, ...]` (all states of spec 7.1 plus `unknown`).

- [ ] **Step 1: Write the failing tests**: `test_status_each_rule` parametrized with one row per rule of spec 7.1 (rule number, inputs, expected state: 1 `fresh=False` gives `trouble_getting_data`; 2 charging 2 gives `sock_charged`, 1 gives `sock_charging`, with soft_brick 1 gives `sensor_soft_bricked`; 3 `base_on=False` gives `base_station_off`; 4 `critical_oxygen`; 5 the four combinations; 6 the four single alarms; 7 `discomfort`; 8 soft_brick 1 gives `sensor_soft_bricked`; 9 `sock_off_baby`; 10 `low_light_level`; 11 `sock_disconnected_from_base`; 12 `sock_connection=2, readings_flag=2` gives `kicking`; 13 `readings_flag=3` gives `sock_recently_placed`; 14 `readings_flag=1` gives `sock_readings_invalid`; 15 `sock_connection=2, battery_minutes=239` gives `low_sock_battery`; 16 `normal`), `test_status_order` (stale beats charging; charging beats an active sock-off alarm; `base_on=False` beats alarms; an alarm beats `readings_flag=3`; `readings_flag=2` with `sock_connection=1` is `normal`; battery minutes exactly 240 is `normal`), `test_status_unknown_inputs` (`compute_status(None)` is `unknown`; all fields None with `fresh=True` is `normal`; Review Focus 2).
- [ ] **Step 2: Run** `pytest tests/owletapi/test_states.py -q -k status`. Expected: FAIL (names undefined).
- [ ] **Step 3: Implement** `StatusInputs` and `compute_status`: evaluate the rules of spec 7.1 top to bottom, first match wins; combinations in rule 5 need both a heart rate and an oxygen alarm; singles in the order `high_oxygen`, `low_oxygen`, `low_heart_rate`, `high_heart_rate`; an input that is None never matches.
- [ ] **Step 4: Run** the full file. Expected: pass; ruff clean.
- [ ] **Step 5: Commit** `git add custom_components/owlet/owletapi/states.py tests/owletapi/test_states.py`; message `Add status rules of the app's status line`.

---

### Task 3: Data layer and capture fixtures

**Files:**
- Modify: `custom_components/owlet/owletapi/const.py` (PropertyKey, Properties, VITALS_3), `custom_components/owlet/owletapi/sock.py`
- Create: `tests/fixtures/live_on_foot.json`, `live_sock_off.json`, `live_on_station.json`, `live_cold_boot.json`, `live_power_loss.json`
- Test: `tests/owletapi/test_sock.py`

**Interfaces:**
- Consumes: Task 1 `parse_sleep_string`.
- Produces: `VITALS_3[int]` gains `"body_position": "bp"` and `"recovery_mode": "mrs"` (both in `PropertyKey` and `Properties` as `int | None`); `Sock.sleep_codes -> list[int | None]` (parsed `MOBILE_VITALS`, `[]` when absent); `Sock.sleep_updated_at -> datetime | None` (sanitised `data_updated_at` of `MOBILE_VITALS`).

- [ ] **Step 1: Build the fixtures** with a one-off script kept outside the repo (scratchpad). For each row take the snapshot of the capture file with that `polled_at` UTC time and write `{"response": {name: {"name": name, "value": ..., "base_type": ..., "direction": ..., "data_updated_at": ...}}}`: encode the parsed `REAL_TIME_VITALS` and `MOBILE_VITALS` values back to JSON strings, replace `**REDACTED**` by `"redacted"`, keep everything else. Rows (capture file in the project root, `polled_at`, freeze time to use in tests = the vitals `data_updated_at` plus 3 s): `live_on_foot` = `owlet-capture2.log` 14:23:09 (srf 0); `live_sock_off` = `owlet-capture2.log` 15:04:11 (SOCK_OFF 1, alrt 64, aps 1, srf 1); `live_on_station` = `owlet-capture3.log` 15:36:48 (chg 2, bso 0, BMS 8); `live_cold_boot` = `owlet-capture3.log` 15:35:58 (srf 3, hr 57, sh empty); `live_power_loss` = `owlet-capture2.log` 15:23:51 (alrt 8192, LOST_POWER_ALRT 1, bsb 0). Check no identifier is present: `grep -c "AC000\|SERIAL\|@" tests/fixtures/live_*.json` shows nothing real.
- [ ] **Step 2: Write the failing tests** in `tests/owletapi/test_sock.py`: `test_new_vitals_keys` (using `live_on_foot.json`: `properties["body_position"] == 7`, `recovery_mode == 1`, `hardware_version == "obs4"`), `test_sleep_accessors` (`sleep_codes == [8] * 15`, `sleep_updated_at` equals the `MOBILE_VITALS` timestamp; Review Focus 1 and 5), `test_sleep_accessors_without_property` (the older `properties_v3.json`: `[]` and None), `test_sleep_codes_after_reboot` (`live_cold_boot.json`: `[]`).
- [ ] **Step 3: Run** `pytest tests/owletapi/test_sock.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement** the const additions and the two `Sock` properties (reuse `property_updated_at` and `parse_timestamp`).
- [ ] **Step 5: Run** the whole suite. Expected: pass (existing tests unaffected).
- [ ] **Step 6: Commit** the fixtures, const, sock and tests; message `Read body position and recovery mode, expose the sleep string`.

---

### Task 4: Coordinator: threshold, worn sock, status

**Files:**
- Modify: `custom_components/owlet/const.py`, `custom_components/owlet/coordinator.py`
- Test: `tests/test_coordinator.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces: constants `DEFAULT_STALE_THRESHOLD = 20`, `MIN_STALE_THRESHOLD = 15`, `MAX_STALE_THRESHOLD = 7200`, `STALE_POLL_MARGIN = 10`, `V2_MIN_STALE_THRESHOLD = 300`, `DREAM_HARDWARE = "obs4"`; `OwletCoordinator.stale_threshold -> timedelta | None` (seconds; at least 300 for `sock.version == 2`); `OwletCoordinator.readings_valid -> bool`; `OwletCoordinator.alert_state(key: str) -> bool | None` (the flag-first resolution of one alert, using `alert_active` from `states.py`); `reading_available(key: str, available_during_charging: bool, needs_worn_sock: bool = False) -> bool`; `OwletCoordinator.status -> str`.

- [ ] **Step 1: Keep existing tests on their old semantics.** In `tests/conftest.py` the mock entry sets `CONF_STALE_THRESHOLD: 300` instead of `DEFAULT_STALE_THRESHOLD` (it keeps the version 2; the version change belongs to Task 5, because an entry newer than `ENTRY_VERSION` is refused). The other coordinator tests then keep working with their minute-scale ticks. After the unit change run the suite once and fix only test setup that depended on the old unit, not behaviour.
- [ ] **Step 2: Write the failing tests** in `tests/test_coordinator.py` (fixtures from Task 3 via `properties_fixture`, time frozen at the fixture's vitals time plus 3 s): `test_default_threshold_is_20_seconds` (entry without the option: a reading 19 s old is fresh, exactly 20 s is fresh, 21 s is stale), `test_version_2_threshold_is_at_least_300` (v2 fixture, option 20: a 120 s old reading is still fresh), `test_readings_valid_with_placement_alarm` (`live_sock_off.json`: false; also true once `SOCK_OFF` is 0 and `alrt` is 0), `test_alarm_bit_16_makes_readings_invalid` (patch the fixture value `alrt` 16), `test_srf_on_dream_hardware` (`live_cold_boot.json` srf 3: false; srf 1: false; srf 2: false; srf 0: true), `test_srf_ignored_on_other_hardware` (change `hw` to `obl` with srf 1: true; remove `hw`: true; Review Focus 3), `test_srf_absent_is_valid`, `test_needs_worn_sock_gates_vitals` (`reading_available("heart_rate", False, True)` false for `live_sock_off.json`, true for `reading_available("battery_percentage", True, False)`), `test_status_property` (`live_on_foot.json` gives `normal`; `live_sock_off.json` gives `sock_off_baby`; `live_on_station.json` gives `sock_charged`; `live_cold_boot.json` gives `sock_recently_placed`; stale vitals give `trouble_getting_data`), `test_threshold_zero_disables_freshness` (option 0 and a reading hours old: readings available, `status` is `normal`, never `trouble_getting_data`), `test_status_with_unreadable_vitals_and_bad_timestamps` (vitals value `"x"`, a missing timestamp, a naive timestamp, a future timestamp: `status` never raises, and `trouble_getting_data` for the timestamp cases; Review Focus 2 and 5).
- [ ] **Step 3: Run** the new tests. Expected: FAIL.
- [ ] **Step 4: Implement**: the constants in `const.py`; `stale_threshold` from seconds with the v2 minimum; `alert_state` resolving `alerts_mask` bits through `ALERT_BITS` and `alert_active` from `states.py`; `readings_valid` as specified in the Global Constraints (an unknown `hw` or absent `srf` never invalidates); `reading_available` with the extra argument; `status` building `StatusInputs` (`fresh=self.vitals_fresh`, alerts resolved with the flag-first rule, `charging`, `soft_brick` from `brick_status`, `base_on`, `sock_connection`, `readings_flag`, `battery_minutes`) and returning `compute_status`.
- [ ] **Step 5: Run** the whole suite and ruff. Expected: pass.
- [ ] **Step 6: Commit** `const.py`, `coordinator.py`, `tests/conftest.py`, `tests/test_coordinator.py`; message `Follow the app's 20 s freshness rule and add worn-sock and status logic`.

---

### Task 5: Migration and the threshold option

**Files:**
- Modify: `custom_components/owlet/__init__.py`, `custom_components/owlet/config_flow.py`, `strings.json`, `translations/{en,nl,fr}.json` (option text and the error)
- Test: `tests/test_init.py`, `tests/test_config_flow.py`, `tests/conftest.py` (the mock entry becomes `version=3`; its option stays 300 seconds)

**Interfaces:**
- Produces: `ENTRY_VERSION = 3`; `migrate_stale_threshold(minutes: int | None) -> int` in `__init__.py` (None or 5 gives 20; 0 gives 0; otherwise `min(minutes * 60, 7200)`); `async_migrate_entry` runs the version 1 step, then the version 2 step (rewrites `options[CONF_STALE_THRESHOLD]` with `migrate_stale_threshold` and sets version 3); the options form stores seconds and rejects a value that is not 0 and below 15 or below `scan_interval + 10` with the form error key `threshold_too_low`.

- [ ] **Step 1: Write the failing tests**: in `tests/test_init.py` `test_version_2_entry_threshold_migration` (parametrized options 5 gives 20, 0 gives 0, 10 gives 600, 150 gives 7200, missing key gives 20; Review Focus 4), `test_version_1_entry_reaches_seconds` (a version 1 entry with `stale_threshold` 10 ends at version 3 and 600), `test_future_version_left_alone` (change the existing future-version entry from 3 to 4). Update the existing assertions of version 2 in `test_init.py` (about lines 88 to 105) and `test_config_flow.py:78` to 3. In `tests/test_config_flow.py` `test_options_flow_rejects_low_threshold` (threshold 10 with scan interval 10 gives the form error `threshold_too_low`; 19 with interval 10 rejected; 20 accepted; 0 accepted; threshold 100 with scan interval 100 rejected; Review Focus 4); change the existing option values 5 (about lines 287 and 307) to 300.
- [ ] **Step 2: Run** `pytest tests/test_init.py tests/test_config_flow.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the version constant, the helper, the step-wise migration, the form validation in `OwletOptionsFlow.async_step_init` (show the form again with `errors={CONF_STALE_THRESHOLD: "threshold_too_low"}`), the `vol.Range(min=0, max=MAX_STALE_THRESHOLD)` schema, the new entry default `DEFAULT_STALE_THRESHOLD` (20) in the user flow, and the option/error strings (English text states seconds; Dutch and French equivalents).
- [ ] **Step 4: Run** the full suite and ruff. Expected: pass.
- [ ] **Step 5: Commit** the changed files; message `Migrate the stale threshold to seconds`.

---

### Task 6: Binary sensors

**Files:**
- Modify: `custom_components/owlet/binary_sensor.py`
- Test: `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: Task 4 (`readings_valid`, `alert_state`, `reading_available(..., needs_worn_sock)`).
- Produces: `OwletBinarySensorEntityDescription` gains `needs_worn_sock: bool = False`; new `OwletDerivedBinarySensorDescription(BinarySensorEntityDescription)` with `source_key: str`, `is_on_fn: Callable[[OwletCoordinator], bool | None]`, `available_during_charging: bool`, and class `OwletDerivedBinarySensor`; new entities `alert_silenced` (source `alert_paused_status`, on when 1), `recovery_mode` (source `recovery_mode`, diagnostic, on when 1), `readings_valid` (coordinator, diagnostic, disabled by default, created for every version 3 sock), `data_not_updating` (source `alerts_mask`, bit 128, diagnostic, disabled by default); `awake` sensor decodes by band through `decode_sleep_code` and is flagged `needs_worn_sock`; the existing alert sensors use the flag-first rule.

- [ ] **Step 1: Write the failing tests** (fixtures of Task 3): `test_alert_silenced` (`live_sock_off.json` on, `live_on_foot.json` off), `test_recovery_mode` (on), `test_readings_valid_entity` (entity is disabled by default in the registry; enabled it shows off for `live_sock_off.json`), `test_data_not_updating_bit` (alerts mask 128 patched in: on; disabled by default), `test_sock_off_sensor_follows_flag_then_mask` (flag 1: on; flag None and mask 64: on; flag 0 and mask 64: off), `test_awake_sensor_unavailable_when_sock_off` (`live_sock_off.json` makes `binary_sensor.awake` unavailable; available for `live_on_foot.json`), `test_awake_sensor_bands` (patched `ss`: 3 gives on, 7 gives off, 12 gives off, 0 and 16 give unknown), `test_new_entities_not_created_for_v2` (the v2 fixture creates none of the four keys).
- [ ] **Step 2: Run** `pytest tests/test_binary_sensor.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the description classes and entity class; `OwletBinarySensor.is_on` uses `coordinator.alert_state(description.key)` when the key is in `ALERT_BITS`; `available` passes `needs_worn_sock` through; creation uses `source_key in coordinator.sock.properties` (or, for `readings_valid`, `coordinator.sock.version == 3`); `entity_registry_enabled_default=False` where the spec says disabled.
- [ ] **Step 4: Run** the full suite and ruff. Expected: pass.
- [ ] **Step 5: Commit** `binary_sensor.py`, `tests/test_binary_sensor.py`; message `Add alert silenced, recovery mode and worn-sock binary sensors`.

---

### Task 7: Derived sensors and sleep bands

**Files:**
- Modify: `custom_components/owlet/sensor.py`
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: Tasks 1, 4.
- Produces: `OwletSensorEntityDescription` gains `needs_worn_sock: bool = False`; new `OwletDerivedSensorDescription(SensorEntityDescription)` with `source_key: str`, `value_fn: Callable[[OwletCoordinator], StateType | datetime]`, `available_during_charging: bool`, `needs_worn_sock: bool = False`, `attributes_fn: Callable[[OwletCoordinator], dict[str, Any]] | None = None`, `always_available: bool = False`, and class `OwletDerivedSensor`. Entities: `base_state` (diagnostic, disabled by default; attributes `raw`, `monitoring`, `code`), `base_battery`, `session_started` (timestamp, None for 0), `body_position` (`needs_worn_sock`, attribute `raw`), `reading_state` (diagnostic, disabled by default, attribute `raw`). Existing: `signal_strength` loses unit and device class (stays diagnostic, enabled); `OwletSleepSensor` and the awake binary sensor decode by band through `decode_sleep_code` (options `unknown`, `awake`, `light_sleep`, `deep_sleep`, plus `no_data`); `oxygen_saturation`, `heart_rate`, `oxygen_10_av`, `skin_temperature`, `movement`, `movement_bucket`, `sleep_state` flagged `needs_worn_sock`.

- [ ] **Step 1: Write the failing tests**: `test_base_state` (BMS 8 gives `on_station`, 65 gives `unknown` with attributes `raw` 65, `monitoring` true, `code` 64), `test_base_battery` (`live_on_foot` `charged`, `live_power_loss` `not_charging`), `test_session_started` (`live_on_foot` equals 2026-10-24T12:40:27+00:00; `live_cold_boot` mst 0 gives unknown), `test_body_position` (7 gives `side`; hidden for `live_sock_off.json`), `test_reading_state_enabled_registry_default` (disabled; `live_cold_boot` gives `just_placed` when enabled), `test_vitals_unavailable_when_sock_off` (heart_rate, oxygen, skin temperature unavailable for `live_sock_off.json`; battery_percentage available; `battery_remaining` available), `test_cold_boot_hides_bogus_vitals` (`live_cold_boot.json`: heart_rate unavailable although it holds 57), `test_signal_strength_has_no_unit` (no `unit_of_measurement`, no `device_class` in the state attributes), `test_sleep_state_bands` (patched `ss`: 3 gives `awake`, 7 gives `light_sleep`, 12 gives `deep_sleep`, 0 gives `no_data`, 16 gives `unknown`).
- [ ] **Step 2: Run** `pytest tests/test_sensor.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the description class, entity class and descriptions; creation by `source_key in properties`; the `OwletSleepSensor` options list `SLEEP_STATES` becomes the band output set (update `const.SLEEP_STATES` usage accordingly); remove the dBm unit/device class from `signal_strength`.
- [ ] **Step 4: Run** the full suite and ruff. Expected: pass; fix existing sensor tests that assumed only codes 0, 1, 8, 15.
- [ ] **Step 5: Commit** `sensor.py`, `const.py` if touched, `tests/test_sensor.py`; message `Add base, battery, position and reading state sensors, decode sleep by band`.

---

### Task 8: Per-minute sleep sensors

**Files:**
- Modify: `custom_components/owlet/sensor.py`
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 7 (`OwletDerivedSensor`).
- Produces: `sleep_code_minute` (numeric, state class measurement, no unit, no device class; `value_fn` returns `sock.sleep_codes[0]` or None) and `sleep_state_minute` (enum `no_data`, `awake`, `light_sleep`, `deep_sleep`, `unknown`; attributes `history` and `updated`); both created when `MOBILE_VITALS` is in the raw properties, available while charging, not worn-sock gated, freshness from the `REAL_TIME_VITALS` timestamp; the `history` attribute is excluded from the recorder (`_unrecorded_attributes = frozenset({"history"})` on the entity class).

- [ ] **Step 1: Write the failing tests**: `test_sleep_code_minute` (`live_on_foot`: 8; patched `sh` `"F88"`: 15; `""`: unknown; `"G"`: unknown; Review Focus 1), `test_sleep_state_minute_and_history` (`sh` `"188"` with `MOBILE_VITALS` time 14:28:03Z: state `awake`, `history` is three dicts with `state` `awake`, `light_sleep`, `light_sleep`, `raw` 1, 8, 8 and times 14:28:03Z, 14:27:03Z, 14:26:03Z, `updated` 14:28:03Z), `test_sleep_sensors_available_while_charging` (`live_on_station.json`: state `no_data` or the string's own code, available), `test_sleep_sensors_stay_available_between_mobile_vitals_updates` (vitals fresh, `MOBILE_VITALS` timestamp 59 s old: available), `test_sleep_sensors_unavailable_when_vitals_stale`, `test_history_with_bad_mobile_vitals_timestamp` (missing or naive timestamp: state still decodes, `history` is `[]`, no exception; Review Focus 5), `test_history_not_recorded` (`history` is in `_unrecorded_attributes`).
- [ ] **Step 2: Run** `pytest tests/test_sensor.py -q -k sleep`. Expected: FAIL.
- [ ] **Step 3: Implement** the two descriptions with `value_fn`/`attributes_fn` using `sock.sleep_codes`, `sock.sleep_updated_at`, `decode_sleep_code`, `sleep_history`; `available` for these two uses `reading_available(..., available_during_charging=True)` with the vitals timestamp only.
- [ ] **Step 4: Run** the full suite and ruff. Expected: pass.
- [ ] **Step 5: Commit**; message `Add per-minute sleep sensors`.

---

### Task 9: Status sensor

**Files:**
- Modify: `custom_components/owlet/sensor.py`
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: Task 4 `OwletCoordinator.status`, Task 2 `STATUS_OPTIONS`, Task 7 `OwletDerivedSensor` (`always_available=True`).
- Produces: sensor `status` (enum, options `STATUS_OPTIONS`, enabled by default), created for every sock with `sock.version == 3`, never unavailable (not stale, charging or worn-sock gated).

- [ ] **Step 1: Write the failing tests**: `test_status_entity_values` (`live_on_foot` `normal`; `live_sock_off` `sock_off_baby`; `live_on_station` `sock_charged`; `live_cold_boot` `sock_recently_placed`), `test_status_stays_available_when_stale` (advance time 5 minutes with no new reading: state `trouble_getting_data`, not `unavailable`), `test_status_not_created_for_v2`.
- [ ] **Step 2: Run** `pytest tests/test_sensor.py -q -k status`. Expected: FAIL.
- [ ] **Step 3: Implement** the description with `value_fn=lambda c: c.status`, `translation_key="status"`; the entity updates through the existing expiry timer (`_async_reading_expired` already notifies listeners).
- [ ] **Step 4: Run** the full suite and ruff. Expected: pass.
- [ ] **Step 5: Commit**; message `Add the status line sensor`.

---

### Task 10: Diagnostics

**Files:**
- Modify: `custom_components/owlet/diagnostics.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Produces: each sock entry gains `readings_valid` (bool), `status` (str), `stale_threshold_seconds` (int | None).

- [ ] **Step 1: Write the failing test** `test_diagnostics_has_new_fields` (`live_sock_off.json`: `readings_valid` false, `status` `sock_off_baby`, `stale_threshold_seconds` 300 for the conftest entry; and 20 for an entry without the option).
- [ ] **Step 2: Run** `pytest tests/test_diagnostics.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the three keys in `async_get_config_entry_diagnostics`.
- [ ] **Step 4: Run** the full suite. Expected: pass.
- [ ] **Step 5: Commit**; message `Add the new state to diagnostics`.

---

### Task 11: Translations, documentation, final checks

**Files:**
- Modify: `strings.json`, `translations/en.json`, `translations/nl.json`, `translations/fr.json`, `README.md`, `CHANGELOG.md`
- Create: `tests/test_translations.py`

**Interfaces:**
- Consumes: every `translation_key` and enum option defined in Tasks 6 to 9.

- [ ] **Step 1: Write the failing test** `tests/test_translations.py`: `test_every_entity_key_is_translated` (import `SENSORS`/derived descriptions from `sensor.py` and `binary_sensor.py`; each `translation_key` exists in `strings.json` under its platform; each enum sensor has a `state` entry for every option), `test_translation_files_have_the_same_keys` (`en.json` equals `strings.json`; `nl.json` and `fr.json` have exactly the same key paths as `en.json`).
- [ ] **Step 2: Run** `pytest tests/test_translations.py -q`. Expected: FAIL (missing keys).
- [ ] **Step 3: Write the strings** (names and state labels) for all new entities and enum states in the four JSON files; Dutch and French translations; the options description of the threshold in seconds.
- [ ] **Step 4: README and CHANGELOG** per spec section 11: a "Supported hardware" note near the top (verified on a Dream Sock only, other hardware may show wrong or arbitrary values, report with the raw capture option); entity list; an "Availability" paragraph (worn-sock rule, 20 s rule and its cost, per-minute sleep sensors); the status line and what it leaves out; CHANGELOG feature list including the migration, the downgrade warning, the `signal_strength` change and the sleep band decoding.
- [ ] **Step 5: Final checks**: run the full suite with coverage (`--cov --cov-report=term-missing`; total at least 93 %), `ruff check` and `ruff format --check`, and replay the five `live_*.json` fixtures once through a setup to confirm the visible entity states match the spec examples (heart rate hidden for `live_sock_off`, `status` values of Task 9).
- [ ] **Step 6: Commit**; message `Document live data parity and add translations`.

---

## Release gate (not a task)

Before a release the sleep band edges and the `srf` 2 rule are checked against one night capture (spec decisions 1 and 9): turn the raw capture option on, send the log, and adjust `decode_sleep_code` or `readings_valid` if the data contradicts them.
