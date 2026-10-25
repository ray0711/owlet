# Live data parity for the Dream Sock / Smart Sock 3

Status: first version (revision 4), for review. Refined as more information becomes available. Branch `live-data-parity` (from `capture-logging`).
Evidence: the raw captures `owlet-capture.log`, `owlet-capture2.log`, `owlet-capture3.log` and the Home Assistant history export `history.csv` (Dream Sock, base firmware 3.23.3, sock firmware 3.15.2, 2026-10-22 to 2026-10-24), and the decompiled Owlet app 3.50.0 including a bytecode disassembly of its status function. Details and timestamps are in `notes/findings.md`. An independent review of revision 3 is folded in.

## 1. Goal

Home Assistant shows what the Owlet app shows for the live state of a Smart Sock 3 / Dream Sock, and nothing that only looks live. "Live" means the data the integration already polls from the Ayla cloud. History and sleep summaries are a separate project.

## 2. Scope

In scope
- Read the vitals keys the integration ignores today and expose the ones with a known meaning.
- Decode status codes (base state, base backup battery, alert bits, body position, reading flag, sleep string) with tests.
- Make the live vitals unavailable while the sock is not on the foot.
- Follow the app's 20 s freshness rule.
- Add a per-minute sleep state sensor and a numeric twin for graphs.
- A first version of the app's status line (section 7.1), refined later.

Out of scope
- Any control. The integration still writes `APP_ACTIVE` only and never `APP_CMD_REQUEST`, `ALRT_PAUSE_CMD` or `SETTINGS_CMD`.
- Session totals of awake/light/deep minutes (Home Assistant statistics can build them from the per-minute sensor; the existing `sleep_state` history already reproduces the app's session summary within about 2 minutes).
- The app's History tab, sleep summary and "wakings" (computed by the Owlet server, not in the app) and morning reports.
- A firmware-update sensor (the Dream Sock reports it in the `FW_UPDATE_STATUS` JSON; candidate for later).
- Smart Sock 2 behaviour. Nothing changes for it, including the freshness threshold (section 6).

## 3. API usage

No new requests and no change of polling or `APP_ACTIVE` cadence. Every new value is in the `GET /dsns/{dsn}/properties.json` response the coordinator already reads every poll (default 10 s). The device-list request added to the raw capture stays a diagnostic option and is not used here.

Why 10 s is enough
- The states shown stay in the cloud until the next change. The base reported every observed change within 3 to 8 s, except the sock placed on the station at 17:04, which took about 30 s.
- The sleep string keeps the last 15 minutes, so any interval below 15 minutes loses no minute.
- Cadence of the vitals (history.csv, 14,711 readings over 42 h): one reading every 10 s (median), 99 % of gaps at 15 s or less, 14 gaps above 20 s (0.10 %), 8 around base reboots and Home Assistant restarts and 6 (21 to 36 s) without a known cause. HA saw a new reading at a median age of 3.3 s (99th percentile 5.7 s).

## 4. What is known (and how well)

V = seen in the captures or the history, A = read in the app's code, H = hypothesis.

| Item | Meaning | Basis |
|---|---|---|
| `ss`, digits of `sh` | exact codes seen: 0 no data / not monitoring, 1 awake, 8 light sleep, 15 (`F`) deep sleep | V |
| `ss` scale | probably 1 to 15 in three bands: awake 1-5, light 6-10, deep 11-15 (the app's REST API groups durations that way). Six periods of 2 to 10 min between "awake" and "light" with other codes while monitoring is on (history.csv) fit codes 2 to 7, but could also be code 0; no such periods between light and deep | A (grouping), H |
| `sh` (in `MOBILE_VITALS` as `{"sh": ...}`) | last 15 minutes, one hex digit per minute, newest first; empty after a base reboot; its cloud timestamp moves about once a minute | V |
| `chg` | 0 not charging, 1 charging, 2 charged | V |
| `bsb` | base backup battery: 0 not charging (seen on power loss), 1 charging, 2 charged; the app shows other values (such as 4 in the test fixtures) as "not present" | V, A |
| `BATTERY_STATUS` | follows `bsb`: 768 with 2, 512 with 1, 0 on power loss; 131840 seen once (23 Oct 21:02 to 21:20), unexplained | V |
| `alrt` | bitmask; 64 sock off, 8192 base power loss verified; others from the app: 1 low O2, 2 high O2, 4 low HR, 8 high HR, 16 sensor disconnected, 32 low integrity (seen twice, 35 and 75 s), 128 data not updating (confirmed constant; the app counts it only on the BabySat product), 256 low sensor battery, 512/1024/2048 preview high/med/low, 4096 discomfort | V, A |
| `SOCK_OFF`, `LOST_POWER_ALRT` | agree with `alrt` bits 64 / 8192 in all 51 readable snapshots; other flags never set. The flag property updates the same second or up to 5 s before the vitals blob that carries the mask (8 events), both seen in the same poll each time | V |
| `aps` | alert silenced (by the app or the button on the base) | V |
| `mst` | session start (unix seconds), 0 when no session | V |
| `mrs` | recovery mode, 1 on; changed by the app via `APP_CMD_REQUEST` `{"cmd":"mon_recovery","state":"on/off"}`, acknowledged in `APP_CMD_RESPONSE` | V, A |
| `BASE_MON_STATUS` | **bit 0 = monitoring on**, equal to `base_station_on` (1369 of 1369 minutes agree; every flip within 0 to 15 s of the matching `base_station_on` flip). Values form off/on pairs 2/3, 4/5, 8/9, 32/33, 64/65. The upper part (2, 4, 8, 32, 64) changes only together with an on/off flip or at a reboot; hypothesis: reason of the last flip (4/5 commanded from a switch, 8/9 sock placed on / lifted from the station, 64/65 boot default). 8 was always seen with the sock on the station; the station was also seen under 2 | V (bit 0), H (upper part) |
| `srf` | read in the app's status function: 0 normal, 1 no valid readings, 2 kicking (when `sc` is 2), 3 sock just placed. Observed 0 on the foot, 2 then 1 within 20 s of removal (the only 2 seen carried frozen heart rate 144 / oxygen 95), 3 while acquiring. The older Smart Sock 3 test fixtures show 1 with valid readings (origin unverified) | A, V |
| `bp` | body position, from the app: 1 moving, 2 undetected, 0 or 3 no data, 4-5 on back, 6-9 side, 10 and above (or below 0) tummy. Observed 0, 1, 2, 4, 6, 7, 11 | A (code of `gf8.H` and `DeviceState`), V |
| `ota` | 3 or 4 means update in progress only for the BabySat product; the Dream Sock uses `FW_UPDATE_STATUS` JSON | A |
| Freshness in the app | the app shows "trouble getting new data" when its `sc` timestamp is older than 20 s, "loading" within 20 s of activation. The timestamp is set when the phone receives the update | A |
| App status line | order of checks recovered from the bytecode of `ueh.f()` (the jadx text of that method is unreliable): OTA, base offline, not loaded, freshness, charging (sb 1 soft brick), base off, base turning on, alarms (HR/O2 combinations, discomfort, soft brick, sock off, low integrity, sock disconnected), reading flag (kicking, just placed, invalid), low sock battery (btt under 240 with `sc` 2), normal | A |
| `hr`, `ox` | not set to 0 when the sock leaves the foot, they stay at the last value until the sock reaches the station; at a cold boot with the sock on a table `hr` 57, `ox` 96 with `srf` 3. With the base switched off (code 4) and the sock on the foot readings keep arriving (66 min, 123 changes) | V |
| Sock battery | discharge 4.9 %/h on the foot (two nights), charging about 80 %/h | V |
| `rsi` | not shown by the app (only "base Wi-Fi connected" is). Positive values 16 to 82, about 18 on the station; not dBm | V |
| Sleep summary | HA's `sleep_state` durations for the app's session: deep 21.3 min (app 21); light 79.5 min at 14:30 UTC, 83.5 at 14:34 (app 83; the time of the app reading is not recorded); the app's awake figure 7 against 4.6 min plus 4.2 unknown minutes right after a base reboot, most likely code 0 | V |

Not present on this firmware: `pi`, `hrl`, `hrh`, `oxl`, `oxh`, `aem`, `mts`.

## 5. Architecture

Files and responsibilities
- `owletapi/states.py` (new, no Home Assistant imports): pure decoders `decode_base_state`, `decode_base_battery`, `decode_alert_bits`, `decode_body_position`, `decode_reading_flag`, `decode_sleep_code`, `decode_sleep_history`, and `compute_status` (section 7.1), with the lookup tables. One purpose: turn raw codes into names, tolerate anything.
- `owletapi/const.py`: add `bp` (`body_position`) and `mrs` (`recovery_mode`) to `VITALS_3`; add the new keys to `PropertyKey` and `Properties`.
- `owletapi/sock.py`: expose the parsed `MOBILE_VITALS` string and its cloud timestamp (`sleep_history`); no other logic.
- `coordinator.py`: `readings_valid`, the extended `reading_available`, the seconds-based freshness threshold, `status`.
- `__init__.py`: step-wise config entry migration (section 9).
- `sensor.py`, `binary_sensor.py`, `config_flow.py`: new entities, changed alert state, threshold option in seconds.
- `diagnostics.py`: add `readings_valid`, `status` and the threshold in seconds.
- `strings.json` and `translations/{en,nl,fr}.json`, `README.md`, `CHANGELOG.md`.

Existing entity ids and unique ids do not change. Every new entity description names the property key it reads (`source_key`, the key in `sock.properties`); an entity is created when that key is present (the existing `description.key in properties` check does not fit new entities whose key differs from the source).

## 6. Availability and freshness rules

**Freshness (follows the app).** A reading is fresh when its cloud timestamp is at most `stale_threshold` seconds old. The default is 20 s, the app's value, for Smart Sock 3 / Dream Sock (version 3) sockets. Everything else works as today (stale readings make every entity from them unavailable, `readings_expected`, the repair issue after one hour). Smart Sock 2 sockets (version 2) are not changed: their threshold is at least 300 s whatever is configured, because their per-vital timestamps may only move when a value changes. An offline base is noticed after about 20 to 30 s instead of 5 minutes.

Expected cost: with a 10 s poll the cloud lag and the poll phase add to the 10 s cadence, so the readings of the history would have dropped out about 26 times in 42 hours (19 of them 21 to 41 s), each time making every entity unavailable and turning `data_stale` on. The user accepted this to follow the app (decision 6). The option is validated: 0 (disabled) or at least the polling interval plus 10 s, and not below 15 s.

**Worn sock.** `readings_valid` (coordinator) is false when any of these holds, and true otherwise:
1. a placement alarm is active: `SOCK_OFF` flag set, or `alrt` bit 64 (sock off) or bit 16 (sensor disconnected) set;
2. on hardware "obs4" (Dream Sock, from the `hw` key of the vitals) only: `readings_flag` (`srf`) is not 0 and not absent. Other hardware never uses `srf` for this (its older fixtures show `srf` 1 with valid readings).

Entities flagged `needs_worn_sock` are unavailable when `readings_valid` is false, in addition to the existing rules (stale, charging): heart rate, oxygen saturation, 10-minute oxygen average, skin temperature, movement, movement bucket, body position, sleep state (sensor and the awake binary sensor). Battery minutes, battery, charging, signal strength, alerts, base state, the diagnostic entities, the per-minute sleep sensors and the status sensor are not gated.

Resulting behaviour: a sock lifted off the foot hides its frozen vitals about 10 to 20 s later (`srf` goes 2 then 1 within 20 s of removal) instead of showing the last heart rate until the alarm, and the bogus `hr` 57 / `ox` 96 seen while the sock lies on a table during a cold boot is never shown. Known gap: with the base switched off and the sock on the foot readings keep arriving and are shown, while the app shows "base station off" (the status sensor reports it).

**Per-minute sleep sensors.** Their availability follows the `REAL_TIME_VITALS` timestamp (the same freshness as the other entities). The `MOBILE_VITALS` timestamp (it moves only about once a minute) is used only to time the history entries.

## 7. Entities

(Superseded by revision 5 at the end of this document: `base_state`, `reading_state`, `data_not_updating`, `sleep_code_minute` and `sleep_state_minute` were removed, `last_base_change`, `signal_quality` and `sleep_history` added, and the `battery_status` sensor dropped.)

New entities (all read-only, unique id `{serial}-{key}`, created when the source key is present). "Charging" says whether the entity stays available while the sock charges.

| Key | Platform | Source key | Value | Default | Charging |
|---|---|---|---|---|---|
| `status` | sensor, enum | several (7.1); created for every version 3 sock | the app's status line, first version | enabled | n/a, never unavailable |
| `alert_silenced` | binary_sensor | `alert_paused_status` | on when 1 | enabled | available |
| `recovery_mode` | binary_sensor | `recovery_mode` | on when 1, diagnostic | enabled | available |
| `readings_valid` | binary_sensor | coordinator | on when readings are valid, diagnostic | disabled | available |
| `data_not_updating` | binary_sensor | `alerts_mask` | bit 128, diagnostic; expected to stay off on a Dream Sock | disabled | available |
| `base_state` | sensor, enum | `base_monitoring_status` | `on_station` (8), else `unknown`; attributes `raw`, `monitoring` (bit 0) and `code` (raw without bit 0); diagnostic | disabled | available |
| `base_battery` | sensor, enum | `base_battery_status` | `not_charging` (0), `charging` (1), `charged` (2), `fault` (3), `not_present` (any other number), unknown when absent | enabled | available |
| `session_started` | sensor, timestamp | `monitoring_start_time` | datetime, unknown when 0 or absent | enabled | available |
| `body_position` | sensor, enum | `body_position` | `moving`, `undetected`, `no_data`, `on_back`, `side`, `tummy`; attribute `raw`; `needs_worn_sock` | enabled | hidden |
| `reading_state` | sensor, enum | `readings_flag` | `normal` (0), `no_valid_readings` (1), `kicking` (2), `just_placed` (3), else `unknown`; attribute `raw`; diagnostic | disabled | available |
| `sleep_code_minute` | sensor, numeric | `MOBILE_VITALS` `sh[0]` | the raw code 0 to 15 of this minute, for graphs: state class measurement, no unit, no device class; unknown when the string is empty or the digit is not hex | enabled | available |
| `sleep_state_minute` | sensor, enum | `MOBILE_VITALS` `sh[0]` | `no_data` (0), `awake`, `light_sleep`, `deep_sleep`, else `unknown` (also when the string is empty) | enabled | available |

`sleep_state_minute` attributes: `history`, a list of `{"time": ISO time, "state": name, "raw": code}` for the up to 15 characters, newest first, time = cloud time of `MOBILE_VITALS` minus 60 s per character; `updated`, the cloud time. The `history` attribute is excluded from the recorder (it changes every minute and would be stored in full). Separate from the existing `sleep_state` sensor. Both per-minute sensors are available while the sock charges (code 0, `no_data`), and are not gated by `readings_valid`.

`sleep_code_minute` keeps the base's own scale (0 no data, 1 awake, about 8 light, 15 deep), so a line graph reads naturally: higher is deeper sleep. It shares source, timing and availability with `sleep_state_minute`; the text enum is for dashboards and automations, the number for graphs. In long-term statistics its mean mixes code 0 with sleep codes and is not meaningful.

Sleep codes are decoded by band: 0 `no_data`, 1 to 5 `awake`, 6 to 10 `light_sleep`, 11 to 15 `deep_sleep`, anything else `unknown`; the exact code is always kept as an attribute (`raw`). The same decoder is used for the existing `sleep_state` sensor and the awake binary sensor, which today show "unknown" for every code except 0, 1, 8 and 15. The band edges are a hypothesis (section 4) and are checked against a night capture before release (decision 9). Because code 0 also occurs right after a reboot and while acquiring, it is named `no_data`, not "not monitoring".

Changed existing entities
- Alert binary sensors keep their keys. The separate flag decides when the flag property exists; the matching `alrt` bit decides only when there is no flag property. Mapping: high HR 8, low HR 4, high O2 2, low O2 1, low battery 256, lost power 8192, sock disconnected 16, sock off 64, discomfort 4096, low integrity 32, preview high/med/low 512/1024/2048. Critical oxygen, critical battery and the DRM preview alarm have no known bit. (In the 8 alarm events of the captures the flag was never later than the mask, by up to 5 s.)
- `signal_strength`: stays visible as it is today (diagnostic category, enabled), but shows the raw value without a unit and without the signal-strength device class. The value is not dBm and the app does not show it; this stays until more is known about what it measures.
- `sleep_state` and the awake binary sensor: band decoding as above.

Kept as they are: the raw `alerts_mask`, `base_monitoring_status` and `battery_status` diagnostic sensors.

### 7.1 Status line, first version

The `status` sensor reports what the app's home screen reports, from the order of checks recovered from the bytecode (section 4). The first match wins. It is never unavailable and ignores the stale, charging and worn-sock rules (it reports them instead); with no data at all it is `unknown`. Inputs that are absent make their rule not match.

| # | Condition | State |
|---|---|---|
| 1 | vitals older than the stale threshold (`vitals_fresh` false) | `trouble_getting_data` |
| 2 | `chg` 2, or `chg` 1 | with `sb` 1: `sensor_soft_bricked`; else `sock_charged` / `sock_charging` |
| 3 | `bso` is 0 (base station switched off) | `base_station_off` |
| 4 | critical oxygen alarm | `critical_oxygen` |
| 5 | heart rate and oxygen alarms together | `low_heart_rate_low_oxygen`, `high_heart_rate_low_oxygen`, `low_heart_rate_high_oxygen`, `high_heart_rate_high_oxygen` |
| 6 | single alarm | `high_oxygen`, `low_oxygen`, `low_heart_rate`, `high_heart_rate` (in this order) |
| 7 | discomfort alarm | `discomfort` |
| 8 | `sb` 1 | `sensor_soft_bricked` |
| 9 | sock off alarm | `sock_off_baby` |
| 10 | low integrity alarm | `low_light_level` |
| 11 | sensor disconnected alarm | `sock_disconnected_from_base` |
| 12 | `sc` 2 and `srf` 2 | `kicking` |
| 13 | `srf` 3 | `sock_recently_placed` |
| 14 | `srf` 1 | `sock_readings_invalid` |
| 15 | `sc` 2 and battery minutes under 240 | `low_sock_battery` |
| 16 | none of the above | `normal` |

Alarms use the alert rule of section 7 (flag first, mask as fallback). Rules 4 to 6 follow the app's list of states; the exact order among them is not verified because those alarms were never seen. Not implemented in this version, with the reason: update in progress (the test the app applies to `FW_UPDATE_STATUS` is not recovered), base offline (needs the cloud's online state, which a poll does not return; stale vitals report `trouble_getting_data` instead), base turning on and loading initial connection (they depend on timestamps of the app's own bookkeeping), the app's re-authentication state (the integration has its own reauthentication flow). The version is refined when more is known.

## 8. Edge cases

- Empty `sh` (after a base reboot): `unknown`, empty history.
- `sh` with characters other than hex digits: that character is `unknown`, the rest is still decoded.
- `mst` 0 or not a number: `session_started` is unknown.
- Unknown `BASE_MON_STATUS`, `bp` or `srf` value: `unknown`, the raw number is kept in the attribute. An unknown `bsb` is `not_present`, as in the app.
- Keys missing from the vitals JSON (other hardware): the entity is not created, or unknown if it existed before; never a made-up value.
- Several socks in one account: all rules apply per coordinator.
- Smart Sock 2 (version 2): none of the new entities are created, the gating flag has no effect and the threshold is at least 300 s.
- Base offline: all readings become unavailable once older than the threshold (20 s default) and the status sensor says `trouble_getting_data`.
- A threshold of 0 disables the freshness check as today (the status sensor then never reports `trouble_getting_data`).

## 9. Compatibility and migration

No removed entities, no changed unique ids, no new requests. Visible behaviour changes, all listed in the changelog:
- Freshness threshold in seconds, default 20 s for Smart Sock 3 / Dream Sock. The `stale_threshold` option (minutes, 0 to 120 today) becomes seconds (0 disables, otherwise at least 15 s and the polling interval plus 10 s). Config entry version 2 to 3 migrates stored values: the old default of 5 minutes becomes the new default of 20 s, 0 stays 0, any other value is multiplied by 60 and capped to the new maximum of 7200 s. A missing value gets the new default. This changes the behaviour of a user who chose 5 minutes on purpose; the changelog says so, and that going back to an older release is not supported.
- Migration is step-wise: the existing version 1 to 2 step still runs, then version 2 to 3, so a version 1 entry also ends with seconds.
- Vitals become unavailable earlier when the sock leaves the foot (Dream Sock).
- Alert sensors fall back to the mask bit when there is no flag property.
- `sleep_state` and the awake sensor decode the whole 1 to 15 scale; `signal_strength` loses its dBm unit and device class and shows the raw value.

## 10. Testing

- Fixtures in `tests/fixtures/` derived from the captures, one per situation: on the foot with valid readings, off the foot with the alarm, on the station, cold boot with the sock off, power loss during a session. They keep the allowlisted properties only, with the serial and MAC addresses replaced by placeholders, in the same Ayla format as the existing fixtures (the captured JSON values are re-encoded as strings).
- Existing tests are updated where the new behaviour changes them: the entry version asserted in `tests/test_init.py` and `tests/test_config_flow.py`, the test that uses version 3 as "a future version", and the stale-threshold unit in the options flow and coordinator tests. The older Smart Sock 3 fixtures (hardware "obl") must keep their vitals available, which is the check that the `srf` rule applies to Dream Sock hardware only.
- Unit tests for each decoder in `tests/owletapi/test_states.py` (unknown, empty and malformed input; sleep band edges 0, 1, 5, 6, 10, 11, 15, 16; body position boundaries 0, 1, 2, 3, 4, 5, 6, 9, 10, -1; `bsb` 0 to 3 and 4).
- `sleep_code_minute`: the numeric code for 0, 1, 8, 15, a value in between, an empty string and a malformed digit.
- Status rules (`compute_status`): one case per row of the table in 7.1, plus conflicts that fix the order (charging beats alarms, stale beats charging, base off beats alarms, soft brick while charging, `srf` 2 with `sc` not 2, battery minutes 239 and 240).
- Entity tests for every new entity and the changed alert sensors (flag only, mask only, both); availability tests in `tests/test_coordinator.py` for `readings_valid` (alarm flag, bit 64, bit 16, `srf` 1, 2 and 3 on obs4, `srf` ignored on other hardware, absent `srf`, charging, stale), the freshness boundary (19 s, exactly 20 s, 21 s), the version 2 minimum of 300 s, and the per-minute sensors staying available between `MOBILE_VITALS` updates.
- Migration tests: entry version 2 with `stale_threshold` 5 (to 20), 0 (to 0), 10 (to 600), 150 (capped), missing (to 20); a version 1 entry reaching seconds.
- Options flow validation: threshold below the polling interval plus 10 s is rejected.
- Diagnostics output contains `readings_valid`, `status` and the threshold in seconds.
- Coverage stays above the repository floor (93 %), `ruff check` and `ruff format --check` are clean.

## 11. Documentation

README entity list, a clearly visible "Supported hardware" note near the top: the decoded values, the worn-sock rule, the status line and the new entities were developed and verified on a Dream Sock only; on a Smart Sock 3 or Smart Sock 2 the new entities may show wrong or arbitrary values, and users are asked to report what they see (the raw data capture option exists for that). An "Availability" paragraph (worn-sock rule, the 20 s freshness rule and its cost, the per-minute sleep sensors), a short description of the status line and what it leaves out, CHANGELOG entry (including the migration and downgrade warning), English, Dutch and French labels for every new entity and enum state, and a note in the options description that the threshold is in seconds.

## 12. Risks and open points

- All of this comes from one baby, one Dream Sock and one firmware pair. The Smart Sock 3 (older base, "obl", firmware 3.8) and the Smart Sock 2 may behave differently; decoders therefore tolerate anything and the Smart Sock 3 coverage is untested. Its fixtures lack `bp` and `mrs`, so those entities would not appear there.
- The 20 s threshold drops every entity out for one poll or more about 26 times in 42 hours (section 6) because it is evaluated at poll time against the cloud timestamp; the app measures from when the phone received the update. If this proves annoying the default is raised without other changes.
- The upper bits of `BASE_MON_STATUS` are not understood (hypothesis: reason of the last on/off flip). Only `on_station` (8) is named; the raw value is always in the attribute.
- `srf`: the rule gates on any non-zero value on Dream hardware. If a night capture shows 2 ("kicking") together with good readings during sleep, 2 is taken out of the gate.
- The sleep band edges and the intermediate sleep codes are inferred, not observed; to be checked against a night capture (the per-minute string shows the exact codes).
- The sleep string records minutes with the sock off the foot as awake (digit `1` was seen); the sensor reports what the base reports.
- The status line is a first version: alarm order among the HR/O2 states is a best guess, and it lacks update, base offline and turning-on states.
- Recorder load: `body_position` and the status can change often; the history attribute is excluded from the recorder.
- How long the cloud takes to mark the base `Offline` was not measured; the design does not depend on it.

## 13. Decisions

Taken
1. Live vitals are unavailable on a placement alarm (flag, bit 64 or bit 16), and on Dream Sock hardware also when `srf` is not 0 (the alarm alone was rejected as too late; `srf` 2 is included because the only 2 seen carried frozen readings; to be revisited with a night capture).
2. No session totals; per-minute sleep sensors with a 15-minute history attribute instead.
3. No "active alerts" list attribute; the existing per-alert sensors stay, plus `data_not_updating`.
4. Alert sensors: the separate flag first, the mask bit only as a fallback when there is no flag (the flag was never later than the mask).
5. No additional API requests and no polling change.
6. Freshness follows the app: 20 s default for version 3 sockets, threshold in seconds, with the migration above; accepted despite the expected drop-outs.
7. Signal strength is not an app value: it stays visible as a diagnostic entity and shows the raw value without a unit until more is known.
8. Hardware: the work is verified on the Dream Sock only. Other hardware is not restricted in code (entities appear when the source properties exist), the README states that values there may be wrong or arbitrary.
9. Sleep codes are decoded by band (0 no data, 1-5 awake, 6-10 light, 11-15 deep) with the raw code kept in an attribute; the band edges are inferred and get checked against a night capture before release.
10. A numeric sensor `sleep_code_minute` with the raw per-minute code (0 to 15) is added next to the text enum so the sleep state can be graphed.
11. The app's status line is built now as a first version (7.1) and refined when more information is available.

## Revision 5 (after the night capture, the recorder export and a second app-source round)

Evidence: `owlet-capture4.log` (18:46Z to 05:47Z), `history_2.csv`, `notes/app-source-round2.md`. Changes against revision 4:

- **Worn-sock rule (Dream Sock):** hide vitals on a placement alarm, `srf` 1 and 3, `sc` below 2 and a base station that is off (the app shows dashes in all of these). `srf` 2 (kicking) stays valid; the app's dial keeps the last value. Measured: `srf` 1/2 mostly repeat the previous reading, `srf` 3 follows a start of monitoring with plausible readings but also a sock on a table with a frozen heart rate.
- **Sleep history:** the history lives on its own diagnostic timestamp sensor `sleep_history` (always available, state = time of the newest minute, attribute `history`, not recorded); `sleep_state` keeps `sleep_code`.
- **Status and hiding:** `sock_connected` (connection below 2 or the sock disconnected alert) makes the status line report `sock_disconnected_from_base` and, with the disconnected flag counted as a placement alarm on every version 3 sock, hides vitals.
- **Sleep:** the app always decodes by band (1 to 5, 6 to 10, 11 to 15); the sock only sent 0, 1, 8 and 15. `ss` equalled the newest `sh` digit in 251 of 252 snapshots, so the per-minute sensors were duplicates: **Sleep state of this minute** and **Sleep code of this minute** are removed, the raw code (`sleep_code`) and the `history` are attributes of **Sleep state**.
- **Removed:** `reading_state` (equals the status line), `data_not_updating` (bit 128 is not used on a Dream Sock), `battery_status` (equals `base_battery`), `base_state` (replaced).
- **Added:** `last_base_change` (the cause code of `BASE_MON_STATUS`: button 2/3, cloud command 4/5, sock station 8/9, power-up 64/65, else unknown) and `signal_quality` (app bands: 1 to 70 good, 71 to 80 okay, above 80 poor, 0 or no link disconnected). This corrects the earlier note that the app does not show the sock signal.
- **Names and defaults:** clearer names (sock battery, sock Bluetooth signal, sock off foot, ...), the four preview alarms disabled by default, `sock_off` device class problem.
