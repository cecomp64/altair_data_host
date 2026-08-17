# Complete Off-Grid Solar, Weather & Observatory Monitoring System

This repository contains the complete code, configuration files, and deployment scripts to run an off-grid solar, weather, and observatory monitoring system on a Raspberry Pi or Mini PC.

It collects telemetry from a **Victron SmartShunt** (via VE.Direct), an **Eco-Worthy MPPT Charge Controller** (via RS485 Modbus RTU), a **LAN weather station**, a **roll-off-roof controller** (via ASCOM Alpaca), and **N.I.N.A.** astrophotography sessions. Everything is stored in **InfluxDB v2**, rendered in **Grafana**, and the solar summary is also pushed to an **E-Paper display**.

---

## 1. Directory Structure

```text
altair_data_host/
├── docker-compose.yml
├── telegraf.conf
├── 99-solar-serial.rules
├── .env.example
├── docker/
│   └── telegraf/
│       ├── Dockerfile                   # telegraf:latest + python3/pyserial/grpcio deps
│       ├── requirements-starlink.txt    # pip deps for read_starlink.py, see §7
│       └── third_party/
│           └── starlink_grpc.py         # vendored client lib, see §7
├── grafana/
│   ├── snapshot_dashboard.sh               # pulls the live dashboard (incl. UI edits) before you hand-edit it, see §9
│   ├── dashboard_backups/                  # timestamped snapshots kept by the script above (last 5)
│   └── provisioning/
│       ├── datasources/influxdb.yml        # one Grafana data source per bucket
│       └── dashboards/
│           ├── dashboards.yml              # dashboard provider config
│           └── solar-observatory.json      # hand-edited source of truth, see §9
├── scripts/
│   ├── deploy.sh                          # one-shot end-to-end setup (host + docker)
│   ├── install-docker.sh                  # host: Docker Engine + Compose plugin
│   ├── install-udev-rules.sh              # host: persistent serial device symlinks
│   ├── install-epaper-deps.sh             # host: SPI + venv + waveshare_epd driver for the e-paper script
│   ├── install-epaper-service.sh          # host: systemd unit for the e-paper display
│   ├── install-charge-cutoff-deps.sh      # host: venv + gpio-group membership for the charge-cutoff relay
│   ├── install-charge-cutoff-service.sh   # host: systemd unit for the charge-cutoff relay
│   ├── init-influx-buckets.sh             # docker-aware: creates weather/observatory/imaging/network/starlink/system buckets
│   ├── init-grafana-alerting.sh           # docker-aware: provisions Discord alerting, see §13
│   ├── init_grafana_alerting.py           # the actual alerting setup logic, called by the script above
│   ├── epaper-dashboard.service.template  # systemd unit template used above
│   ├── charge-cutoff.service.template     # systemd unit template used above
│   ├── read_vedirect.py                   # runs inside the telegraf container
│   ├── read_ping.py                       # runs inside the telegraf container
│   ├── read_starlink.py                   # runs inside the telegraf container
│   ├── epaper_dashboard.py                # runs on the host (needs GPIO/SPI)
│   ├── charge_cutoff.py                   # runs on the host (needs GPIO) - see §12
│   ├── requirements-epaper.txt            # host-side Python deps for the e-paper script
│   └── requirements-charge-cutoff.txt     # host-side Python deps for the charge-cutoff script
├── vendor/                                # gitignored - waveshare-epaper cloned here by install-epaper-deps.sh
└── README.md
```

---

## 2. Quick Start

```bash
git clone <this repo> altair_data_host
cd altair_data_host
scripts/deploy.sh
```

`scripts/deploy.sh` is the single entry point and is safe to re-run. It:

1. Installs Docker Engine + the Compose plugin if not already present (`scripts/install-docker.sh`), via Docker's official apt repository, and adds the invoking user to the `docker` group.
2. Generates `.env` with random secrets if one doesn't already exist (see [Secrets](#4-secrets)).
3. Installs the persistent serial device udev rules on the host (`scripts/install-udev-rules.sh`).
4. Builds the custom Telegraf image and brings up `influxdb`, `grafana`, and `telegraf` with `docker compose` - InfluxDB's first-run setup creates the `power` bucket at this point (`DOCKER_INFLUXDB_INIT_BUCKET` in `docker-compose.yml`).
5. Waits for InfluxDB's healthcheck before continuing.
6. Creates the remaining `weather`, `observatory`, `imaging`, `network`, `starlink`, and `system` buckets (`scripts/init-influx-buckets.sh`) - a separate step because InfluxDB's first-run setup only creates one bucket.
7. Provisions Discord alerting (`scripts/init-grafana-alerting.sh`) - **skipped** (doesn't fail) until `DISCORD_WEBHOOK_URL` is set in `.env`, since that requires a webhook you create yourself (see [§13](#13-grafana-alerting-discord)).
8. Installs the e-paper display's host-side Python dependencies (`scripts/install-epaper-deps.sh`): enables SPI, creates a `.venv/`, and vendors + installs Waveshare's `waveshare_epd` driver (see [§10](#10-python-scripts)).
9. Installs and starts the `epaper-dashboard` systemd service on the host (`scripts/install-epaper-service.sh`).
10. Installs the charge-cutoff relay's host-side Python dependencies (`scripts/install-charge-cutoff-deps.sh`): creates a `.venv-charge-cutoff/` and adds the invoking user to the `gpio` group (see [§12](#12-charge-cutoff-relay)).
11. Installs and starts the `charge-cutoff` systemd service (`scripts/install-charge-cutoff-service.sh`) - **skipped** until `CHARGE_CUTOFF_GPIO_PIN` is set in `.env`, since that depends on hardware you may not have wired yet. Re-run `scripts/deploy.sh` (or just this script) once it is.

**On a genuinely fresh machine, step 1 will exit with instructions to log out and back in** (or reboot) before re-running `scripts/deploy.sh` - Linux group membership changes (needed so `docker compose` works without `sudo`) only take effect in a new login session, and there's no way around that being a one-time speed bump. Every later re-run is a no-op if Docker's already installed and usable.

If step 8 enabled SPI for the first time, reboot the Pi before the e-paper display will actually work - the service will otherwise start and fail to talk to the panel until the SPI overlay is loaded.

Each of these steps is also runnable standalone (e.g. to re-apply udev rules after editing them, or to add a bucket after changing the list in `init-influx-buckets.sh`).

After that, edit `.env` to point `WEATHER_API_URL` and `ALPACA_BASE_URL` at your real devices (see §6/§7), and configure N.I.N.A.'s InfluxDB Exporter plugin per §8. Grafana at `http://<host>:3000` will already have all seven data sources and the full dashboard provisioned - no manual data-source setup or dashboard import needed.

---

## 3. InfluxDB Architecture (audit findings)

The original design used one bucket (`battery_metrics`) for everything. Extending this to weather, observatory, and imaging data surfaced a few things worth fixing up front:

- **One bucket per domain**, not one shared bucket. Retention needs differ a lot: high-frequency solar/weather/observatory telemetry is cheap to sample but not very valuable per-point after a while, while imaging events are low-volume and worth keeping forever (they're your session log). A shared bucket forces one retention policy on everything. `scripts/init-influx-buckets.sh` creates (and `docker-compose.yml`'s `DOCKER_INFLUXDB_INIT_RETENTION` sets, for `power` specifically, since that bucket is bootstrapped at first-run setup rather than by the script):

  | Bucket | Contents | Retention | Why |
  |---|---|---|---|
  | `power` | Victron SmartShunt + Eco-Worthy MPPT (5s interval) | 730d | High-frequency, cheap to resample, not worth infinite storage |
  | `weather` | LAN weather station (60s interval) | 730d | Same reasoning as power |
  | `observatory` | Roof shutter/slew/home/connected state | 730d | Same reasoning as power |
  | `imaging` | N.I.N.A. equipment + per-frame stats | infinite | Low volume, this *is* your session log |
  | `network` | Ping health (internet + Tailscale reachability/latency) | 90d | Operational signal you care about recently, not a dataset worth 2 years of storage |
  | `starlink` | Dish power draw + alerts via its local gRPC API | 180d | Worth a longer look-back than raw ping health for spotting power/alert trends, but still not a session log |

  Retention is only set at creation time - change it later with `influx bucket update --retention <duration>`.

- **One telegraf agent, six outputs.** Rather than running separate telegraf containers per domain, `telegraf.conf` has one `[[outputs.influxdb_v2]]` block per bucket, each scoped with `namepass` to the measurements that belong there. This keeps the "one collector" mental model while still getting bucket-level retention isolation.

- **Shared admin token.** All outputs (and the Grafana data sources) currently use the same all-access `INFLUXDB_ADMIN_TOKEN`. That's fine for a single-host hobby system; if you ever expose InfluxDB beyond your LAN, consider `influx auth create` with per-bucket read/write scopes instead.

- **Cardinality stays low.** Tags are only added where there's a real identity to distinguish (`station`, `device_number`, camera/focuser/mount names from N.I.N.A., image target/filter). The solar measurements still carry no tags, which is correct for a single-shunt, single-controller system - if you ever add a second shunt, tag it (e.g. `device="house_bank"`) rather than encoding it in the measurement name.

- **Not implemented (candidate future work, not built here to avoid speculative complexity):** downsampling tasks that roll 5s power/weather data up to 1-minute averages after N days. Worth doing if disk usage ever becomes a problem; skipped for now since a Pi-class SSD comfortably holds 2 years of 5s samples for ~10 fields.

---

## 4. Secrets

Credentials (InfluxDB admin user/password/token, Grafana admin user/password, all six bucket names, `DISCORD_WEBHOOK_URL`) live in a git-ignored `.env` file at the repo root, not hardcoded in `docker-compose.yml` or `telegraf.conf`. `docker-compose.yml` interpolates them via `${VAR}`, and the `telegraf` and `grafana` services load the whole file via `env_file` so `telegraf.conf` and Grafana's datasource provisioning can reference `${INFLUXDB_ADMIN_TOKEN}` etc. directly. `DISCORD_WEBHOOK_URL` is the one exception to that last part - Grafana's own alerting provisioner can't read it via `${VAR}` interpolation the way datasources can (see §13), so it's picked up by a setup script instead.

`scripts/deploy.sh` generates a `.env` with random secrets (and placeholder device URLs you must edit) on first run. To set your own instead, copy `.env.example` to `.env` and edit it before running `deploy.sh`:

```bash
cp .env.example .env
$EDITOR .env
```

The host-side e-paper service also reads these values via `EnvironmentFile=` in its systemd unit (see [§10](#10-e-paper-display-systemd-service)), so there's a single source of truth for credentials across containers and the host process. N.I.N.A.'s plugin (§8) needs the org/bucket/token values entered manually into its own settings UI, since it runs on a separate Windows PC outside this repo's reach.

---

## 5. Linux System Configuration

### Persistent Serial Device Rules (`99-solar-serial.rules`)
To prevent `/dev/ttyUSB0` and `/dev/ttyUSB1` from swapping positions on reboot, persistent symlinks are created based on hardware vendor/product IDs:

```ini
# Victron VE.Direct USB Interface (SmartShunt) - genuine Victron cable enumerates as FT-X (idProduct 6015)
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", SYMLINK+="ttyUSB_VICTRON", MODE="0666"

# Eco-Worthy RS485 to USB Adapter (FTDI FT232R variant, idProduct 6001)
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="ttyUSB_ECOWORTHY", MODE="0666"

# Eco-Worthy RS485 to USB Adapter (CH340 variant, seen on some cables)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="ttyUSB_ECOWORTHY", MODE="0666"
```

Both the Victron cable and the FT232R-based Eco-Worthy adapter use FTDI vendor ID `0403`, distinguished only by product ID (`6015` vs `6001`) - don't assume vendor ID alone identifies the device. Verify with `udevadm info -a -n /dev/ttyUSBx | grep -E "idVendor|idProduct"` and cross-check against `dmesg` (which prints the USB product string, e.g. `VE Direct cable` vs `FT232R USB UART`) before trusting either symlink.

The telegraf container bind-mounts `/dev`, so it automatically sees whatever symlinks udev creates on the host - no container-side configuration needed.

*Apply on the host:* `scripts/install-udev-rules.sh` (installs the rules file to `/etc/udev/rules.d/`, then reloads and triggers udev; re-execs itself with `sudo` if needed).

### Eco-Worthy MPPT: RS485 RJ45 Pinout (don't trust a generic cable)

The controller's RJ45 communication port, per Eco-Worthy's own manual:

| Pin | Function |
|---|---|
| 1 | RS485-A |
| 2 | RS485-B |
| 3–4 | Dry contact |
| 5–6 | GND (isolated) |
| 7–8 | +5V (isolated) |

This is **not** the pinout most off-the-shelf "USB to RS485 RJ45" cables use - most (including cables sold for Seplos/PUSUNG/SUTEN and other server-rack BMS packs) put RS485-A/B on pins 7/8 instead. Plugging one of those in doesn't just fail to communicate - it lands the adapter's A/B lines on this controller's +5V pins. Symptom: telegraf's `inputs.modbus` gets a clean `serial: timeout` (port opens fine, slave never responds) under every baud rate/slave ID/function code, because the query never reaches the controller's actual RS485 transceiver.

**Fix: use a generic USB-RS485 adapter with bare screw terminals and wire it to this controller's pinout yourself**, rather than trusting a pre-wired RJ45 cable's convention:

| Controller RJ45 pin | Signal | Wire to |
|---|---|---|
| 1 | RS485-A | adapter's `A` terminal |
| 2 | RS485-B | adapter's `B` terminal |
| 5 or 6 | GND | adapter's `GND` terminal |
| 3–4, 7–8 | dry contact / +5V | leave disconnected |

Identify which physical wire in the patch cable corresponds to pins 1/2/5 with a multimeter's continuity function rather than trusting color coding - cheap/off-brand Cat5 cables don't reliably follow T568A/T568B. No `telegraf.conf` changes are needed once wired correctly; the existing `inputs.modbus` block (9600 baud, 8N1, slave 1) already matches the controller's defaults.

---

## 6. Docker Orchestration

### Installing Docker (`scripts/install-docker.sh`)
`scripts/deploy.sh` runs this first (step 1). It's idempotent - if `docker` and `docker compose` are already installed and usable, it's a no-op. Otherwise it installs Docker Engine + the Compose plugin from Docker's official apt repository (only supports apt-based distros - Debian/Raspberry Pi OS/Ubuntu), removing any conflicting distro-packaged `docker.io`/`podman-docker`/etc. first, per Docker's own install docs. It then adds the invoking user to the `docker` group.

Run it standalone with:
```bash
scripts/install-docker.sh
```

On a genuinely fresh machine this will install Docker, add you to the `docker` group, then **exit 1 with instructions to log out and back in** - group membership changes don't apply to the current login session, only new ones, and there's no scriptable way around that. Re-run `scripts/deploy.sh` (or this script) after logging back in and it'll pick up cleanly.

### Docker Compose (`docker-compose.yml`)
Runs InfluxDB v2, Grafana, and Telegraf with serial bus access. `influxdb` has a healthcheck that `grafana` and `telegraf` wait on via `depends_on: condition: service_healthy`, so neither starts hammering InfluxDB before it's actually ready to accept writes.

`telegraf` is built from `docker/telegraf/Dockerfile` rather than using the stock `telegraf:latest` image directly: the stock image is Debian-based and has no Python interpreter, but `inputs.exec` needs `python3` to run `read_vedirect.py`. The Dockerfile installs `python3` and `python3-serial` (pyserial) via `apt`, so no internet/pip access is needed inside the container at runtime.

`grafana` mounts `./grafana/provisioning` read-only, which auto-configures all seven data sources and the dashboard on startup - no manual Grafana UI setup required.

Bring the stack up directly (bypassing `deploy.sh`) with:

```bash
docker compose build
docker compose up -d
```

---

## 7. Telegraf Configuration (`telegraf.conf`)

One telegraf agent, six `[[outputs.influxdb_v2]]` blocks (one per bucket, each restricted via `namepass` - see §3), and inputs grouped by domain.

### Power: Victron SmartShunt (VE.Direct) + Eco-Worthy MPPT (Modbus RTU)
Unchanged from the original design: `inputs.exec` runs `read_vedirect.py` for the Victron shunt's text protocol, `inputs.modbus` polls the Eco-Worthy controller's holding registers directly (no custom script needed there - telegraf's modbus plugin is enough).

### Weather: LAN station, via `scripts/read_ecowitt.py`
```toml
[[inputs.exec]]
  commands = [["python3", "/scripts/read_ecowitt.py"]]
  timeout = "5s"
  data_format = "influx"
```
Like the Victron shunt, this goes through a script rather than `inputs.http`/`json_v2` directly - an Ecowitt GW1000/GW2000-style gateway's local API (`get_livedata_info`) returns nested `id`/`val` arrays with units baked into the value string (e.g. `"0.00 mph"`, `"31%"`), which telegraf's `json_v2` float parser can't strip. `read_ecowitt.py` fetches `$WEATHER_API_URL`, looks up each sensor by its documented id (`COMMON_LIST_IDS`/`PIEZO_RAIN_IDS` at the top of the script), strips the unit suffix, and emits Influx line protocol directly. **Verify against your actual station**: `curl --compressed "$WEATHER_API_URL"` and compare the `id` values in `common_list`/`piezoRain`/`wh25` against the script's id maps, adjusting if your model differs. Note `rain_hourly_in` is intentionally not emitted - this station's rain gauge doesn't report an hourly-total id (only event/day/week/month/year), so Grafana's hourly-rain panel will read "No data" rather than a fabricated value.

### Observatory: roll-off-roof via ASCOM Alpaca
Three `[[inputs.http]]` blocks poll the standardized Alpaca dome REST endpoints (`shutterstatus`, `slewing`, `connected`) every 15s. This is a versioned, documented REST spec, so confidence here is high - no verification step needed beyond confirming `ALPACA_BASE_URL` (including the device number) is correct. `shutterstatus` returns the ASCOM enum (0=Open, 1=Closed, 2=Opening, 3=Closing, 4=Error); Grafana maps it to readable labels/colors rather than telegraf doing translation. `athome` is intentionally not polled - AtHome is optional in the ASCOM Dome spec and this driver returns `ErrorNumber 1024 "Not implemented"` for it.

**`shutterstatus` reports Error (4) whenever the roof is stopped partway open, not just on a real fault** - ASCOM's `ShutterState` enum has no value for "partially open", so this driver (Dark Dragons Astronomy's DragonLAIR) falls back to `Error`. A fourth `[[inputs.http]]` block polls the controller's own non-Alpaca `$DRAGONLAIR_STATUS_URL` (`/status`) for a `roof_state` string field (`open`/`closed`/`opening`/`closing`/`partially_open`) plus `safety_monitor_safe`/`weather_locked`/voltage/current, so Grafana can tell a genuinely stuck/errored roof apart from one that's simply half open. This endpoint is vendor-specific and undocumented (reverse-engineered from the controller's own web UI), unlike the versioned Alpaca spec above, so if you're running a different roof/dome controller this block won't apply and can be deleted. `roof_controller_voltage_raw`/`_current_raw` units are unconfirmed (named `_raw` rather than assuming volts/amps) - verify against a multimeter before trusting them in an alert.

Since `ALPACA_BASE_URL` (and `DRAGONLAIR_STATUS_URL`, same host) is typically a `.local` mDNS hostname and telegraf's static Go binary can't resolve those via NSS, the telegraf container runs with `network_mode: host` and a small background script (`docker/telegraf/refresh-mdns-hosts.sh`) keeps `/etc/hosts` updated via active mDNS queries - see comments in `docker/telegraf/Dockerfile` for the full rationale.

### Network: periodic ping health check, via `scripts/read_ping.py`
```toml
[[inputs.exec]]
  commands = [["python3", "/scripts/read_ping.py"]]
  interval = "60s"
  timeout = "20s"
  data_format = "influx"
```
Pings a comma-separated, user-configurable host list from `.env` (`PING_TARGETS_INTERNET`, `PING_TARGETS_TAILSCALE` - e.g. `1.1.1.1,8.8.8.8,google.com` and `octopi`), 3 packets per host every 60s, and emits Influx line protocol shaped like telegraf's own `inputs.ping` plugin (same field names, and `result_code` matching the ping subprocess's own exit code: 0 = replies received, 1 = no reply, 2 = local error such as an unresolvable host). Each host is tagged `network="internet"` or `network="tailscale"` so a downed Tailscale peer doesn't read as "the internet is down" on the dashboard.

This goes through a script rather than `inputs.ping` directly because the host list is variable-length and lives in `.env` - telegraf can substitute an env var *inside* a quoted TOML array element (`urls = ["$HOST"]`, confirmed via `telegraf --test`), but under the strict environment-variable handling that's been the default since telegraf 1.38, it can't expand a single variable into a variable *number* of array elements. Turning that off (`--non-strict-env-handling`) would apply to telegraf's entire config, silently weakening typo-checking everywhere else in `telegraf.conf` - not worth it for one input. Pings run in parallel (one thread per host), so wall-clock time stays roughly one ping-count's worth regardless of how many hosts are configured, and total traffic is a handful of ICMP packets a minute.

### Starlink: dish power/alerts via its local gRPC API, via `scripts/read_starlink.py`
```toml
[[inputs.exec]]
  commands = [["python3", "/scripts/read_starlink.py"]]
  interval = "60s"
  timeout = "15s"
  data_format = "influx"
```
The dish exposes a local gRPC API at `192.168.100.1:9200` (override with `STARLINK_DISH_ADDR` in `.env`) - the same one the Starlink mobile app talks to. `read_starlink.py` wraps the vendored `docker/telegraf/third_party/starlink_grpc.py` (from [sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools), public domain) to pull:

- **Status**: link state, uptime, pop ping latency, downlink/uplink throughput, obstruction fraction, GPS satellite count.
- **Alerts** (the "events"): all ~20 boolean alert flags the dish reports (thermal throttle, roaming, mast not vertical, water detected, etc.), plus a derived `alert_active` field that's true if any of them are, so Grafana doesn't need to OR twenty booleans together for a single "something's wrong" tile.
- **Power**: latest/mean/min/max watts and energy (Wh) over the last `STARLINK_POLL_INTERVAL_S` seconds (default 60, should match this block's `interval`) - the dish keeps a rolling buffer of 1-second power samples internally; this is the same data source as the app's own Power tab. Also pulls download/upload bytes over that same window as a low-cost bonus field.

**Why vendored rather than pip-installed**: `starlink_grpc.py` isn't on PyPI. It's committed as a file (`docker/telegraf/third_party/starlink_grpc.py`) rather than fetched from GitHub at Docker build time, so a build never depends on GitHub being reachable or unchanged - same reasoning as vendoring `waveshare_epd` for the e-paper display (§10), just via a straight file commit instead of a install-time git clone, since this is one file rather than a whole driver repo. It talks to the dish via gRPC server reflection (the `yagrc` package) rather than pregenerated SpaceX protobuf stubs, so no `.proto` files are needed either.

`grpcio`/`grpcio-reflection`/`protobuf`/`yagrc` are installed via pip in the Dockerfile (`docker/telegraf/requirements-starlink.txt`), not apt like `python3-serial` above - confirmed by testing both paths against a real dish: Debian bookworm's `python3-grpcio` (1.51.1) is old enough that `yagrc` pulls in a newer `grpcio`/`protobuf` to satisfy its own version constraints anyway, so apt's copies end up unused regardless. All four have prebuilt `aarch64` wheels, so this doesn't need a compiler in the image.

**Only reachable if the Pi is on the dish's own LAN** (plugged into the Starlink router/dish, not some other router downstream) - `read_starlink.py` will just time out and telegraf will log an exec error otherwise.

### System: the Pi's own health, via telegraf's built-in system plugins
```toml
[[inputs.cpu]]
  interval = "30s"
  percpu = false
  totalcpu = true

[[inputs.mem]]
  interval = "30s"

[[inputs.disk]]
  interval = "30s"
  mount_points = ["/"]

[[inputs.temp]]
  interval = "30s"

[[inputs.system]]
  interval = "30s"
```
Everything else in `telegraf.conf` monitors something *external* to the host; this is the one section that watches the host itself - CPU usage, memory, root filesystem usage, temperature (both the SoC's `cpu_thermal` sensor and, on a Pi 5, the RP1 southbridge's `rp1_adc` sensor - both throttle-relevant), and load average. Unlike every other section here, this uses telegraf's own built-in plugins (gopsutil-backed) rather than a custom script - there's no oddly-shaped API to work around, this is exactly what they're for.

**Requires host-root visibility that a container doesn't have by default.** These plugins read `/proc`, `/sys`, and mounted filesystems - inside a container with no special configuration, that's the *container's* isolated view (its own overlay disk, not the Pi's real SD card), not the host's. `docker-compose.yml`'s `telegraf` service works around this the standard way: a read-only `/:/hostfs:ro` bind mount plus `HOST_PROC=/hostfs/proc`, `HOST_SYS=/hostfs/sys`, `HOST_MOUNT_PREFIX=/hostfs` env vars, which gopsutil honors to report the real host instead. Confirmed correct by testing directly: disk/CPU/temp values from inside the container matched `df`/`vcgencmd`-equivalent reads on the host. Read-only, and this container is already `privileged: true` with `/dev` mounted for the serial devices, so this doesn't meaningfully change its trust boundary.

### Imaging: nothing in telegraf
N.I.N.A.'s own plugin writes to InfluxDB directly - see §8.

---

## 8. Imaging: N.I.N.A. "InfluxDB Exporter" Plugin

Imaging data comes from [daleghent/nina-influxdb-exporter](https://github.com/daleghent/nina-influxdb-exporter), a N.I.N.A. plugin that writes metrics straight to InfluxDB from the imaging PC - telegraf and this repo are not involved in that path at all.

**Schema note** (confirmed from the plugin's README and its own example dashboard): every metric is its own InfluxDB *measurement* (e.g. `image_hfr`, `camera_sensor_temperature`, `guider_rms_arcsec`), each with a single field always literally named `value`, plus tags per equipment class (`camera_name`, `focuser_name`, `target_name`, etc., plus global `profile_name`/`host_name`). This is a different shape than the other three buckets (which use one measurement with several named fields) - `grafana/generate_dashboard.py`'s `nina_last`/`nina_range` helpers query it accordingly.

**Install and configure the plugin** in N.I.N.A. (Options → Plugins → InfluxDB Exporter), on the imaging PC:

| Plugin setting | Value |
|---|---|
| InfluxDB Url | `http://<this-docker-host's-LAN-IP-or-hostname>:8086` (not `localhost`, and not `influxdb` - that only resolves inside the Docker network) |
| InfluxDB Bucket | value of `INFLUXDB_BUCKET_IMAGING` in `.env` (default `imaging`) |
| InfluxDB Org | value of `INFLUXDB_ORG` in `.env` |
| InfluxDB Token | value of `INFLUXDB_ADMIN_TOKEN` in `.env` |

Metrics the plugin can produce (only for connected equipment / `LIGHT` frames): camera temp/cooler power/battery, focuser position/temp, mount altitude/azimuth, rotator angle, guiding RMS (RA/Dec/combined, arcsec and pixels), sun/moon altitude, and per-image stats (HFR, star count, mean/median/std-dev ADU, eccentricity/FWHM if the Hocus Focus plugin is installed). Full list in the plugin's own README.

---

## 9. Grafana Dashboard

`grafana/provisioning/dashboards/solar-observatory.json` is provisioned automatically on Grafana startup (`http://<host>:3000`, default admin credentials from `.env`) - no manual data source setup or dashboard import needed. It's organized into seven row sections matching the buckets:

- **Power** - battery SOC gauge, time-to-go, controller temp, consumed Ah, battery voltage, battery/charging current, net vs. solar power, PV voltage/current.
- **Weather** - current conditions (temp/humidity/wind/pressure) plus trends for temperature, humidity, wind speed/gust, rain accumulation, solar radiation/UV.
- **Observatory** - roof shutter status (color-coded stat + history state-timeline), slewing/at-home/connected indicators.
- **Imaging** - camera/focuser/cooler status, HFR and star-count trends, guiding RMS, sun/moon altitude, and a recent-frames table.
- **Network** - packet loss % and latency stat tiles for internet/Tailscale, plus per-host latency and packet-loss-over-time trends.
- **Starlink** - dish state, alert status, power draw, and obstruction % stat tiles, power-over-time and dish-reported latency trends, uptime/GPS satellites/throughput charts, and a per-alert-flag timeline.
- **System** - CPU/memory/disk usage and CPU temperature stat tiles, plus CPU breakdown, temperature (both sensors), memory & disk, and load average trends.

`solar-observatory.json` is hand-edited directly - there's no generator. An earlier version of this dashboard was built by a `grafana/generate_dashboard.py` script (grid math, ids, and fieldConfig boilerplate via small panel helpers), but that made the JSON file a derived artifact, at odds with also allowing UI edits (below): regenerating and recommitting would silently discard anything changed in the UI since the last run.

**Storage model.** The dashboard effectively lives in two places at once:

- **This JSON file**, mounted read-only into the `grafana` container (`docker-compose.yml`). Grafana's file provisioner (`allowUiUpdates: true` in `dashboards.yml`) loads it on startup and re-polls it every 30s (`updateIntervalSeconds`) for changes - no restart needed after an edit.
- **Grafana's own database** (the `grafana_data` docker volume), which is where `allowUiUpdates: true` actually saves any edit made in the Grafana UI - it can't write back to the file, since the mount is read-only. A UI edit lives on in the database *only until this file's content next changes*: at that point Grafana treats the file as authoritative and overwrites the database copy, discarding the UI edit.

**Workflow: always snapshot before you edit.**

```bash
grafana/snapshot_dashboard.sh
```

This pulls the *live* dashboard from Grafana's API (`/api/dashboards/uid/solar-observatory`, using `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` from `.env`) - which reflects any UI edits made since the file was last updated - backs up whatever was previously committed to `grafana/dashboard_backups/solar-observatory.<timestamp>.json` (keeping the 5 most recent), and overwrites `solar-observatory.json` with the live version. Hand-edit *that* file, then commit both the updated dashboard and the new backup. Skipping this step before an edit risks committing over UI changes nobody remembered to port back into the file.

---

## 10. Python Scripts

### VE.Direct Serial Parser (`scripts/read_vedirect.py`)
Runs inside the `telegraf` container (via the custom image from §6). Parses the ASCII serial stream coming from the Victron SmartShunt and translates VE.Direct keys into Influx Line Protocol.

### E-Paper Display Controller (`scripts/epaper_dashboard.py`)
Runs on the host OS, not in Docker, since it needs direct GPIO/SPI access to the Waveshare e-Paper HAT. Queries InfluxDB every 60 seconds over the host network (`http://localhost:8086`, exposed by the `influxdb` container's port mapping) and renders an updated power dashboard onto a Waveshare **7.5" B V2** E-Paper display (800x480, tri-color black/white/red - the dashboard itself is drawn black-on-white only, with the red plane left blank). If you have a different 7.5" variant (monochrome `epd7in5_V2`, or the 880x528 grayscale `7in5_HD`), swap the driver import at the top of the script - **the mono and tri-color drivers use commands 0x10/0x13 for completely different purposes** (old/new frame vs. black-plane/red-plane), so also update `draw_and_update_display()`'s final `epd.display(...)` call to match the target driver's signature. The layout constants assume 800x480 and would need adjusting for the HD panel's resolution.

Layout: a two-column dashboard (Battery | Solar) with a hero SOC%/PV-power number and a fill gauge per column, secondary stats below (voltage, net power, time-to-go, consumed Ah / PV voltage & current, charge current, controller temp), and a footer with the timestamp plus a CHARGING/DISCHARGING/IDLE state derived from net power's sign.

**Refresh cadence and ghosting.** The script uses the driver's `init_Fast()` instead of `init()` - different booster/temperature registers that select a shorter waveform. Measured on this panel: ~12.6s per full refresh under `init_Fast()` vs ~19s under standard `init()`, for both `Clear()` and `display()`, with no visible quality loss. This chip has no way to load a custom LUT over SPI (no `0x20`-`0x24` commands in this driver), so `init_Fast()` is the fastest full-refresh mode actually available - `EPAPER_REFRESH_SECONDS` (default 60) comfortably fits it with room to spare.

A full refresh's waveform flashes every pixel through black/red/white regardless of the previous frame before settling on the new one - it's a reset-and-redraw, not a diff against the old frame, so **it's already self-cleaning on every call**. Tested directly: drawing a solid black block, then a different solid block, then plain white, back-to-back with no `Clear()` anywhere in between, came out completely clean on the real panel. So the old "deep-clear every N refreshes" behavior was mostly redundant overhead once the driver mismatch (above) was fixed - `EPAPER_DEEP_CLEAR_EVERY` (default 1440, ~once a day at 60s/refresh) is now just a cheap safety net against slower degradation that a handful of back-to-back refreshes wouldn't reveal, not routine de-ghosting. Set it to `0` to disable entirely.

**Partial refresh doesn't work on this hardware - don't try it.** The vendored driver exposes `init_part()`/`display_Partial()`, which looks like an obvious way to go even faster (it does - to ~3s), but its partial-refresh RAM write only touches the black plane; it has no path to move the red pigment at all. Testing confirmed this directly: a few consecutive partial refreshes left visible red bleed-through from unrelated earlier content and visible ghosting on the black plane, recoverable only with a full `Clear()`. This is a known general limitation of black/white/red e-paper (unlike monochrome panels, which usually support partial refresh across many cycles) - full refresh is the only refresh mode that reliably resets both pigment layers on this panel.

Its host-side dependencies are installed automatically by `scripts/deploy.sh` (step 6, `scripts/install-epaper-deps.sh`), which is also runnable standalone and safe to re-run:

```bash
scripts/install-epaper-deps.sh
```

This enables SPI (via `raspi-config`, skipped with a manual-setup note on non-Raspberry Pi OS hosts), creates a Python venv at `.venv/`, installs `scripts/requirements-epaper.txt` into it, and vendors + installs Waveshare's `waveshare_epd` driver (not on PyPI) from [Waveshare's e-Paper repo](https://github.com/waveshare/e-Paper) into `vendor/waveshare-epaper/` (gitignored). If you have a different 7.5" panel than the V2, the vendored repo has the other drivers too - just swap the import in `epaper_dashboard.py` as noted above.

On a **Raspberry Pi 5**, the script also detects the board (via `/proc/device-tree/model`) and swaps the driver's `RPi.GPIO` dependency for `rpi-lgpio`, a drop-in replacement - the Pi 5's RP1-based GPIO chip isn't supported by legacy `RPi.GPIO`. `rpi-lgpio`'s backend (`lgpio`) has no prebuilt PyPI wheels and fails to build from source without the native `liblgpio` C library, so - same as the charge-cutoff relay (§12) - this installs Raspberry Pi OS's precompiled `python3-lgpio` via apt and creates `.venv/` with `--system-site-packages` so it's visible, installing `rpi-lgpio` itself with `--no-deps` so pip doesn't try to satisfy that dependency by compiling it anyway.

The systemd service (`scripts/epaper-dashboard.service.template`) runs the script with `.venv/bin/python3`, not the system interpreter, so `install-epaper-deps.sh` must run before `install-epaper-service.sh` (deploy.sh already orders them this way).

---

## 11. E-Paper Display Systemd Service

`scripts/install-epaper-service.sh` installs and starts a systemd unit that keeps the E-Paper display updating in the background on boot. It's docker-aware: it warns (without failing) if the `influxdb` container isn't running yet, since the e-paper script depends on it.

It renders `scripts/epaper-dashboard.service.template` into `/etc/systemd/system/epaper-dashboard.service`, substituting the repo's actual path and the invoking user, then runs `systemctl daemon-reload` and `systemctl enable --now`:

```ini
[Unit]
Description=Solar E-Paper Display Updater
After=network.target docker.service

[Service]
Type=simple
User=<detected automatically>
WorkingDirectory=<repo path, detected automatically>
EnvironmentFile=<repo path>/.env
ExecStart=<repo path>/.venv/bin/python3 <repo path>/scripts/epaper_dashboard.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

*Install and start standalone* (requires `.env` to already exist, and `.venv/` from `install-epaper-deps.sh` - run `scripts/deploy.sh`, or those two steps individually, first):
```bash
scripts/install-epaper-service.sh
```

---

## 12. Charge Cutoff Relay

Stops solar charging once the battery hits a configurable SOC (default 85%), using a physical DC contactor in the charge line between the MPPT controller's `BATT+` output and the battery bank - not the shunt's shared negative path, so house loads are unaffected while charging is paused.

**This is a hardware feature.** Nothing here replaces careful part selection and wiring for a switch that carries real battery current - see the design notes below before building it. `scripts/charge_cutoff.py` is the software half; it does nothing useful without the contactor and its coil driver wired to a GPIO pin.

### Circuit design

```
PV Array → MPPT (BATT+) ──[Contactor]──[Manual Disconnect]──[Fuse]── Battery (+)
                                                                        │
                                                        Shunt (unaffected) ── Loads
```

- **Contactor**: normally-closed / energize-to-open, DC-rated (not an automotive AC relay - DC arcs don't self-extinguish at zero-crossing), sized with margin over your actual max charge current. NC/energize-to-open contactors are a less common part than the ubiquitous NO ones - verify the contact configuration against the datasheet before buying, since this determines which way the system fails if it loses control power. This repo's default (`.env`'s `CHARGE_CUTOFF_*` variables, `charge_cutoff.py`'s startup behavior) assumes **fail-closed / keep-charging**: on a Pi crash or coil-circuit failure, the contactor's own spring returns it to closed, and charging continues rather than silently stopping. If your battery bank has no BMS-level overcharge protection of its own, reconsider this default - fail-open (stop charging) needs only a standard NO contactor and is the simpler/cheaper part.
- **Manual master disconnect**: a mechanical, non-electronic, DC-rated battery disconnect switch in series with the contactor, mounted somewhere physically reachable. This is the real emergency stop - contactor contacts can weld shut under fault current, and when that happens the manual disconnect is the only thing left that can still open the circuit.
- **Inline fuse**: sized to the wiring/contactor rating, independent of the Pi or contactor entirely.
- **3-position selector switch (AUTO / FORCE-ON / FORCE-OFF)**: sits in the low-current coil circuit (not the battery path), gives day-to-day override of the automatic SOC logic without tools - AUTO lets the daemon drive the coil, FORCE-OFF manually stops charging, FORCE-ON blocks the coil from ever energizing so the contactor stays closed regardless of what the daemon says.
- **Opto-isolated driver**: between the Pi's GPIO pin and the coil, so a fault on the 12V coil circuit can't back-feed into the Pi.

### Software (`scripts/charge_cutoff.py`)

Runs on the host OS, not in Docker, since it needs direct GPIO access. Polls the SmartShunt's SOC from InfluxDB (same query pattern as `epaper_dashboard.py`) and drives one GPIO pin:

- Disconnects (drives the pin HIGH, opening the NC contactor) once SOC ≥ `CHARGE_CUTOFF_SOC_HIGH` (default 85%).
- Reconnects (drives the pin LOW) only once SOC drops to ≤ `CHARGE_CUTOFF_SOC_LOW` (default 80%) - the gap is hysteresis, so the contactor doesn't chatter open/closed right at one threshold.
- Enforces a minimum dwell time between transitions (`CHARGE_CUTOFF_MIN_DWELL`, default 300s) as a second guard against noisy readings.
- Defaults to *not* energizing the coil on startup and on any InfluxDB read failure - same fail-closed direction as the hardware, so software and hardware agree on which way to fail.
- Writes a `charge_cutoff` point (relay state + the SOC it was evaluated against) back to the `power` bucket on every poll - both a record of relay behavior and a heartbeat: if this stream goes stale, the daemon (or the Pi) is down, worth alerting on in Grafana even though it's not unsafe given the fail-closed default.

Uses `gpiozero` with the `lgpio` backend rather than `RPi.GPIO` directly, so it works unmodified on a Raspberry Pi 5's RP1 GPIO chip as well as older Pi models - no Pi5-detection branch needed here, unlike the e-paper driver (§10), because this script owns its own GPIO code rather than depending on a third-party library that hardcodes `RPi.GPIO`. Both `gpiozero` and `lgpio` come from apt (`python3-gpiozero`, `python3-lgpio`), not pip - the `lgpio` PyPI package ships no prebuilt wheels and fails to build from source without the native `liblgpio` C library, which apt's `python3-lgpio` already bundles precompiled. `install-charge-cutoff-deps.sh` creates `.venv-charge-cutoff/` with `--system-site-packages` so it can see them.

Configuration, all via `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `CHARGE_CUTOFF_GPIO_PIN` | *(required, no default)* | BCM pin driving the coil's opto-isolated driver |
| `CHARGE_CUTOFF_SOC_HIGH` | `85.0` | Disconnect threshold (%) |
| `CHARGE_CUTOFF_SOC_LOW` | `80.0` | Reconnect threshold (%) - must be lower than `SOC_HIGH` |
| `CHARGE_CUTOFF_POLL_INTERVAL` | `60` | Seconds between SOC polls |
| `CHARGE_CUTOFF_MIN_DWELL` | `300` | Minimum seconds between contactor state changes |

### Install

Host-side deps (venv + `gpio`-group membership):
```bash
scripts/install-charge-cutoff-deps.sh
```

Systemd service - **skips** (doesn't fail) until `CHARGE_CUTOFF_GPIO_PIN` is set in `.env`, since it's specific to hardware you may not have wired yet:
```bash
scripts/install-charge-cutoff-service.sh
```

Both are already wired into `scripts/deploy.sh` (steps 10-11), so once the relay is built and `CHARGE_CUTOFF_GPIO_PIN` is set, re-running `scripts/deploy.sh` picks it up automatically.

---

## 13. Grafana Alerting (Discord)

Discord notifications for five conditions: `Battery SOC Critical` (soc < 15%), `Starlink Alert Active` (any of the ~20 dish alert flags true), `Internet Fully Down` (every `PING_TARGETS_INTERNET` host at 100% packet loss), `Disk Usage Critical` (root filesystem > 90%), `CPU Temp Critical` (Pi SoC > 80°C). Each rule is a 3-node pipeline (Flux query -> `reduce` last -> `threshold`), evaluated every 1 minute, with a `for:` pending duration (2-10 minutes depending on the rule) so one noisy sample doesn't page you. Thresholds are a starting point, not tuned to your specific battery bank/hardware - see `scripts/init_grafana_alerting.py`'s `RULES` list to change them.

### Why a script, not committed YAML

Every other piece of Grafana config in this repo (datasources, the dashboard) is a file mounted read-only under `grafana/provisioning/`, with `$ENV_VAR` interpolated in by Grafana itself on load. Alerting resources (contact points, notification policies, alert rules) support the same `provisioning/alerting/*.yaml` file format - but Grafana has a confirmed, still-open bug where `$ENV_VAR` expansion silently doesn't happen for them ([grafana/grafana#54984](https://github.com/grafana/grafana/issues/54984), [#56437](https://github.com/grafana/grafana/issues/56437)). This isn't hypothetical: committing a `contactpoints.yaml` with `url: $DISCORD_WEBHOOK_URL` was tried first, and it didn't just fail to interpolate - Grafana **crash-looped on startup** ("could not find webhook url property in settings"), taking the whole dashboard down with it until the file was pulled back out. Confirmed against this exact deployment (Grafana 13.1.1).

So instead, `scripts/init-grafana-alerting.sh` (thin bash wrapper, same shape as `init-influx-buckets.sh`: checks `.env` exists, sources it, requires `DISCORD_WEBHOOK_URL` to be set or skips without failing) calls `scripts/init_grafana_alerting.py`, which provisions the same three resources - contact point, default notification policy, alert rules - via Grafana's REST API instead. It's idempotent (GET-then-POST-or-PUT for everything, keyed by fixed `uid`s), so re-running it after changing a threshold or rotating the webhook URL is safe and is how you apply changes:

```bash
scripts/init-grafana-alerting.sh
```

**Tradeoff worth knowing**: because these are API-created rather than file-mounted, they show up as editable in the Grafana UI without this script silently overwriting UI changes the way `dashboards.yml`'s `allowUiUpdates` does for the dashboard - but they also don't self-heal from git if the `grafana_data` docker volume is ever wiped. Re-run the script after any fresh volume, the same way `init-influx-buckets.sh` needs a re-run for InfluxDB.

### Setup

1. Create a webhook in Discord: **Server Settings -> Integrations -> Webhooks -> New Webhook**, pick a channel, **Copy Webhook URL**.
2. Set `DISCORD_WEBHOOK_URL` in `.env` to that URL.
3. Set `GRAFANA_ROOT_URL` in `.env` (see [§4](#4-secrets)) to whatever's actually reachable from wherever you'll be reading notifications - a Tailscale hostname works from anywhere on your tailnet, a LAN IP only works at home. Without this, the "Source"/"Silence" links Grafana puts in each Discord notification default to `localhost`, which is useless from a phone. Confirmed by testing: unset, the links were dead; set to this deployment's Tailscale hostname (`altair-metrics-host.tail48bec.ts.net`), they resolve correctly from a phone off the home network.
4. Run `scripts/init-grafana-alerting.sh` (or just re-run `scripts/deploy.sh`, which calls it as step 7 and skips it cleanly if `DISCORD_WEBHOOK_URL` is still unset).

Validated end-to-end against the real Discord webhook during setup: created the contact point, pointed the default policy at it, and force-fired a real alert rule (`CPU Temp Critical`, by temporarily changing its threshold to `> 0` so it evaluates true) to confirm actual delivery - not just that Grafana accepted the config - then reverted the threshold back. `/api/v1/provisioning/*` proved considerably more reliable for this than Grafana 13's newer `/apis/notifications.alerting.grafana.app/v1beta1/.../test` endpoint, which returned a generic `unknown integration type` error regardless of request body shape; a real firing rule sidesteps it entirely and is arguably better proof anyway.
