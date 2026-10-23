# Changelog

<!--next-version-placeholder-->
## Unreleased
### Feature
* New opt-in option **Allow base station control** (off by default). When enabled, the base station state becomes a **Base station on** switch again (same unique id) and the API client additionally accepts `BASE_STATION_ON_CMD`; every other write except `APP_ACTIVE` is still refused. With the option off nothing changes: read-only binary sensor, no way to switch the base station. Changing the option swaps the binary sensor and the switch automatically.

## 2026.10.1 (2026-10-12)
### Fix
* No more gaps in the readings. Measured on a Dream Sock: the base station uploads vitals for about 45 seconds after `APP_ACTIVE` is written, then sets the flag back to 0 itself and stops, so the previous 60 s default left a gap of up to 30 s every minute. The integration now writes `APP_ACTIVE` again as soon as the cloud shows the base cleared it, and the activation interval (default now 30 s) is only a ceiling.

## 2026.10.0 (2026-10-12)
First release of [idomp/owlet](https://github.com/idomp/owlet), a fork of [lucasvollebergh/owlet](https://github.com/lucasvollebergh/owlet) 2026.9.1 that is read-only towards the base station and stricter about what it shows. Drop-in replacement: same domain and entity unique ids.
### Breaking
* Requires Home Assistant 2026.10 or newer; the test suite runs against 2026.10.1.
* The base station switch is gone. The integration only ever writes `APP_ACTIVE` to the Owlet cloud (the "a client is watching" flag that makes the base station upload vitals); it can no longer switch the base station on or off, so an automation, a dashboard tap or a voice assistant can never stop the monitoring. The state is still available as the new **Base station on** binary sensor, with the same unique id, and a leftover switch entity is removed automatically.
* Freshness rules rewritten. Every value is judged by the cloud's own timestamp on the reading, and a reading without a usable timestamp (missing, unreadable, without time zone or in the future) is never fresh. When the reading is not fresh every entity taken from it is unavailable, including the alerts, the battery and the charging and base station state, where earlier releases kept alerts and battery visible. Whether the sock is charging or the base station is off is only believed from a fresh reading, so an old reading can no longer hide stale data behind "charging". The **Data stale** sensor explains itself through its attributes, entities expire between polls when the reading ages out, and the **Awake** sensor is unknown instead of on when the sock reports no sleep state.
### Feature
* New **Activation interval** option (default 60 s, 10 to 600). `APP_ACTIVE`, the flag that tells the base station a client is watching, is written at this cadence instead of before every poll, which roughly halves the requests to the Owlet cloud. The property read happens first and no longer depends on the write succeeding; a failed write is logged and retried on the next poll.
* New entities for properties the Dream Sock reports that earlier releases ignored: Discomfort alert, Low integrity reading, the four Preview priority alarms, Base monitoring status, Battery status and Last red alert (when the red alert summary last changed).
* Dutch dashboard in `dashboards/owlet_nl.yaml`: a live view with trend graphs on the heart rate, O2 and skin temperature tiles, sock status and active Owlet alerts, and a trends view with the last 12 hours, sleep, daily statistics and battery.
* Screenshots of the dashboard and step-by-step installation instructions in the README.
### Fix
* Diagnostics are built from an allowlist instead of a redaction list: the entry section holds only the region and options, raw properties include their value only for known-harmless properties (vitals, alerts, status, firmware versions) and are otherwise listed with type and timestamp alone, so a property Owlet adds later cannot leak identifiers.
* The alert blueprint fires only when an alert goes from off to on; an alert that reappears after being unavailable (restart, stale data) no longer sends a notification. The dashboards' "No Owlet alerts active" card shows only while every alert is really off, not while alerts are unavailable.
* Adding the integration now lists the account's socks while logging in, so an account without a supported sock is refused with a clear message instead of failing later at setup. Nothing is written to the base station from the login flow.
* Accounts are identified by region and email (config entry version 2), so the same email can be set up in both the Europe and World regions; existing entries migrate automatically, and entries created by the jensdufour fork use the same identity and need no change. Reconfiguring into a region that is already set up for the email is refused. Entity unique ids are unchanged.
* Rate limits and server errors are never mistaken for a bad login. A 429 or 5xx from the Owlet cloud or the login services (including Firebase's "too many attempts") delays the next poll, honouring `Retry-After`, instead of starting a reauthentication; consecutive failures back off exponentially with jitter up to five minutes. Only one token refresh runs at a time, so two socks polling together cannot invalidate each other's token. Refreshed tokens are stored even when the poll that refreshed them failed, and unreadable responses are a connection error rather than a crash. Device discovery no longer writes `APP_ACTIVE`.
* One bad field no longer breaks the whole update: unreadable vitals, a missing or unreadable `oem_sock_version`, null or oddly typed values and unusual timestamp formats are handled per field. Values are decoded by type, a null alert is unknown instead of off, and sentinel readings (heart rate, oxygen or skin temperature of 0, ten minute average of 255) are unknown instead of being recorded as measurements. A sock whose first snapshot lacked the identifying properties is detected on a later poll instead of staying unidentified.

## 2026.9.1 (2026-09-24)
### Feature
* Ready-made dashboard in `dashboards/owlet.yaml` with built-in cards only: live vitals, active Owlet alerts, the last 12 hours of heart rate, O2 and sleep state, daily min/mean/max trends over 30 days, and battery and signal.

## 2026.9.0 (2026-09-24)
First release of the maintained fork [lucasvollebergh/owlet](https://github.com/lucasvollebergh/owlet). Drop-in replacement for ryanbdclark/owlet: same domain and unique ids. Thanks to [Ryan Clark (@ryanbdclark)](https://github.com/ryanbdclark) for creating the integration, all earlier entries below are his work.
### Fix
* Options dialog works again on Home Assistant 2025.12 and later (the options flow no longer sets `config_entry` itself).
* "Invalid handler specified" when adding the integration: the Owlet API client now ships inside the integration (a maintained copy of pyowletapi, see [lucasvollebergh/pyowletapi](https://github.com/lucasvollebergh/pyowletapi)), so no external `pyowletapi` version can break it anymore.
* Refreshed Owlet tokens are no longer lost while polling, so a restart does not need an extra login.
* One poll costs fewer requests to the Owlet cloud: the extra token check before every request is gone. Rejected tokens are refreshed and retried once, server errors no longer cause a re-login loop, and requests have a timeout.
* Expired logins during polling now start a reauthentication flow instead of failing with a `KeyError`.
* Setup without supported socks or with a connection error now retries instead of failing permanently.
* The reauthentication dialog shows an error instead of silently doing nothing when login fails.
* No more fake `unknown` MAC address and hardcoded hardware version on the device.
* Option labels match the actual options, the minimum polling interval text was wrong.
### Feature
* Stale data detection: new Data stale binary sensor and Last reading sensor, vitals become unavailable when the Owlet cloud stops receiving readings. On the Smart Sock 2 every vital is checked against its own timestamp.
* Configurable stale data threshold in the options.
* Repair issue when the data stays stale for more than an hour, cleared automatically when readings return.
* Automation blueprints: Owlet alert notification (optionally critical) and Owlet stale data notification.
* Reconfigure flow to change region or log in again.
* Diagnostics download with redaction of tokens and personal data, including identifiers inside JSON property values.
* Dutch translation. Removed the untranslated English copy under the Ukrainian language code.
* Region dropdown with translated labels and a description of which region to pick.
* Base station switch refreshes its state right after a command and shows an error when the command fails.
### Changed
* Default polling interval for new setups is 10 seconds (minimum stays 5) to reduce the load on the Owlet cloud.
* Minimum Home Assistant version is 2025.12.
* Signal strength and the new Last reading and Data stale entities are diagnostic entities.
* Uses `entry.runtime_data`, `icons.json` and the current config flow helpers.
* New test suite on pytest-homeassistant-custom-component, CI for hassfest, HACS, ruff and pytest, and a release workflow that builds `owlet.zip`.
## 2025.4.3 (2025-04-15)
### Fix
* Changes to how the sensors are stored to solve the issue where only one device is added, thanks [`@MarjovanLier`](https://github.com/MarjovanLier). ([`1244bff`](https://github.com/ryanbdclark/owlet/commit/1244bffcb48d7337a9d7a0da518959fe4b31a230))

## 2025.4.2 (2025-04-14)
### Fix
* Bumping pyowletapi to 2025.4.1, should hopefully stop issue where only one device was added to HA. ([`d323cbf`](https://github.com/ryanbdclark/owlet/commit/d323cbfd11411ff34866ead492de10c109c72689))

## 2025.4.1 (2025-04-11)
### Fix
* Changes to stop errors after refactoring pyowletapi ([`6b343a7`](https://github.com/ryanbdclark/owlet/commit/6b343a76caad3375e10c80f4d26942a1bbbb831d))

## 2025.4.0 (2025-04-11)
### Fix
* Bumping pyowletapi to 2025.4.0 ([`268365c`](https://github.com/ryanbdclark/owlet/commit/268365ccd428418dd5707f0569ce738b54a12fdd))

## 2024.10.1 (2024-10-09)
### Feature
* Base station has now been removed from binary sensors and added as a switch ([`f63e0a6`](https://github.com/ryanbdclark/owlet/commit/f63e0a6dfeab1a05ba09ef3e0087cb404ba0dac4))
### Fix
* Bump pyowletapi to 2024.10.1 ([`82823be`](https://github.com/ryanbdclark/owlet/commit/82823be1c8265d2b9431771136853febef648650))
* Fix strings for password and connection error in all languages ([`14787e0`](https://github.com/ryanbdclark/owlet/commit/14787e03c4d275f46f446921a3ee133fc7cfd1b1))
* Add state class to battery minutes and O2 saturation 10 minute average ([`0991eb3`](https://github.com/ryanbdclark/owlet/commit/0991eb31d919f3ee9f65ece793166d7ee3e33c38))

## 2024.9.1 (2024-09-26)
### Feature
* Now includes French translation, thanks [`@Julien80`](https://github.com/Julien80) ([`f3c853e`](https://github.com/ryanbdclark/owlet/commit/f3c853e2d7243d766889f2d18c718819da30e4be))

## 2024.6.1 (2024-06-17)
### Fix
* Bumping pyowletapi to 2024.6.1 to resolve setup errors ([`52710ba`](https://github.com/ryanbdclark/owlet/commit/52710ba7de53fd07195537c2a5fd2f95bc7dfd1a))

## 2024.5.1 (2024-05-13)
### Feature
* As per HA core patterns, certain sensors will now show as unavailable when sock is charging ([`ceade24`](https://github.com/ryanbdclark/owlet/commit/ceade24851479b8c9bc60b7b8bed74a7bdb927e9))
* Oxygen 10 minute average now only shows a figure if it is between 0 and 100 this avoids skewing by 255 values before the 10 minutes is reached, thanks @coreywillwhat ([`5e17ecd`](https://github.com/ryanbdclark/owlet/commit/5e17ecdeb2aca5bbb35f19ca5795a2c5e0f776ab))
### Fix
* Refactoring as per core maintainers suggestions ([`ceade24`](https://github.com/ryanbdclark/owlet/commit/ceade24851479b8c9bc60b7b8bed74a7bdb927e9))


## 2024.3.1
### Feature
* Base station on added as binary sensor ([`50c55dc`](https://github.com/ryanbdclark/owlet/commit/50c55dcfd30d15027155a8f1d05340238501522d))

### Fix
* Bumping pyowletapi to 2024.3.2 ([`50c55dc`](https://github.com/ryanbdclark/owlet/commit/50c55dcfd30d15027155a8f1d05340238501522d))
* UI config now allows you to set interval to 5 seconds, previously the minimum was 10 ([`50c55dc`](https://github.com/ryanbdclark/owlet/commit/50c55dcfd30d15027155a8f1d05340238501522d))

## 2023.11.2 (2023-11-23)
### Feature
* Support added for V2 sock ([`50fe1a8`](https://github.com/ryanbdclark/owlet/commit/50fe1a87656b7d6413d06f06f3650fd0bfb48e02))
* Added tests for binary sensors ([`50fe1a8`](https://github.com/ryanbdclark/owlet/commit/50fe1a87656b7d6413d06f06f3650fd0bfb48e02))
### Fix
* Bumping pyowletapi to 2023.11.4 to allow V2 support ([`50fe1a8`](https://github.com/ryanbdclark/owlet/commit/50fe1a87656b7d6413d06f06f3650fd0bfb48e02))
* Refactoring ([`50fe1a8`](https://github.com/ryanbdclark/owlet/commit/50fe1a87656b7d6413d06f06f3650fd0bfb48e02))
* Corrected spelling of sock disconnected sensor ([`50fe1a8`](https://github.com/ryanbdclark/owlet/commit/50fe1a87656b7d6413d06f06f3650fd0bfb48e02))

## 2023.11.1 (2023-11-16)
### Fix
* Bumping pyowletapi to 2023.11.1 ([`3acf847`](https://github.com/ryanbdclark/owlet/commit/3acf8473526665382b44ef6325d708a6c62fff45))
* Sensors and binary sensors are now only created where the sock contains that property, this stops errors where different sock versions have different properties ([`3acf847`](https://github.com/ryanbdclark/owlet/commit/3acf8473526665382b44ef6325d708a6c62fff45))

## 2023.9.1 (2023-09-20)
### Fix
* Bumping pyowletapi to 2023.9.1 to allow for revisions ([`0a7f703`](https://github.com/ryanbdclark/owlet/commit/0a7f70310080a129c988e9607331baa2f6c691e0))
* New revision of sock, revision 5 doesn't report all vitals as before, this would cause the integration to fail to update. Have adjusted the integration to detect the revision and ignore the vitals that are no longer reported ([`0a7f703`](https://github.com/ryanbdclark/owlet/commit/0a7f70310080a129c988e9607331baa2f6c691e0))


## 2023.8.1 (2023-08-21)
### Feature
* 2 new sensors, movement and movement bucket disabled by default, thanks [`@seanford`](https://github.com/seanford) ([`575b213`](https://github.com/ryanbdclark/owlet/commit/575b213ddd732779cd7938e575fc87c8881a69b0))

### Fix
* Various refactoring tasks completed to make this integration more inline with home assistants style guidelines ([`2cd46c1`](https://github.com/ryanbdclark/owlet/commit/c45959b123a6e5f77747475f11d3d3ab67859756))
* Added new sensors to strings jsons ([`2cd46c1`](https://github.com/ryanbdclark/owlet/commit/c45959b123a6e5f77747475f11d3d3ab67859756))

## 2023.7.2 (2023-07-04)
### Fix
* Bumping pyowletapi version to 2023.7.2 ([`c45959b`](https://github.com/ryanbdclark/owlet/commit/c45959b123a6e5f77747475f11d3d3ab67859756))

## 2023.7.1 (2023-07-03)
### Fix
*  Bumping pyowletapi to 2023.7.1 ([`c693fef`](https://github.com/ryanbdclark/owlet/commit/c693fefbf3dba8f35802b87d064401dadbb211b5))
  
## 2023.5.7 (2023-05-30)
### Fix
* Fixed issue with binary sensors not loading, caused by change to way the coordinators are stored ([`8d17317`](https://github.com/ryanbdclark/owlet/commit/8d173174e286b0451cbb2c0d4ae3087028d1ea23))

## 2023.5.6 (2023-05-30)
### Fix
* In light of submitting this as a pull request to the core of HA there have been some refactoring changes to comply with HA's style requirements
* Sensor names now moved to strings file to allow for translations
* Coordinator now properly handles multiple devices
* Spelling of signal strength sensor corrected

### Feature
* Tests added

## 2023.5.5 (2023-05-19)
#### Fix
* Owlet refresh token becomes invalid after 24 hours. Meant that after 1 day the integration would stop working. Moved to pyowletapi v2023.5.28 which uses different refresh token, should no longer need reconfiguring after 24 hours ([`dc58b19`](https://github.com/ryanbdclark/owlet/commit/0141f7d01a9ac9b3e1dcc74cabb896e19bd4a821))

## 2023.5.4 (2023-05-17)
#### Fix
* Bumping to pyowletapi 2023.5.25

## 2023.5.3 (2023-05-17)
#### Fix
* Bumping to pyowletapi 2023.5.24
* Reauthing now no longer re adds users' password to config entry

## 2023.5.2 (2023-05-16)
#### Feature
* Integration now makes use of refresh token from pyowletapi to reauthenticate, user password in no longer stored by integration ([`dc710a1`](https://github.com/ryanbdclark/owlet/commit/dc710a1783a4cad9d6cf355240fe12ac779a87ef))
* New sensors create for baby sleep state ([`9b3392b`](https://github.com/ryanbdclark/owlet/commit/9b3392bdbcd82015ed31d3a50a517e4e22905684))
 
## 2023.5.1 (2023-05-15)
#### Feature
* Changed versioning to date based
### Fix
* Bumping to pyowletapi 2023.5.21 to fix issue with unawaited authentication call, should resolve issue with refreshing authentication ([`228d54b`](https://github.com/ryanbdclark/owlet/commit/228d54b6414e0b9171064254246d1f36c3af8f5b))


## v1.5.0 (2023-05-12)
### Feature
* Now supports reauthentication if credentials have expired/no longer work
* Better error handling when configuring integration, will notify user of incorrect credentials

### Fix
* Removed Owlet specific constants, now using homeassistant generic constants
* On initialisation the integration would crash when trying to update the auth token, the integration would then have to be deleted and setup again
