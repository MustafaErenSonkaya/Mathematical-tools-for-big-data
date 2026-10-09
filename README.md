# Streaming Outlier Detection with Smart Alerts

A small, readable system that watches three simulated sensors (temp, cpu,
pressure), spots unusual values as they arrive, and raises **one** alert per
real incident instead of a flood of noisy ones.

Curious how it was built? [CHAT.md](CHAT.md) has the full conversation with
Claude Code, including plain-English explanations of suppressed alerts and of
the alert-spam problem this project solves.

```
 stream.py           detector.py              alerts.py               notifier.py
+-----------+  value +---------------+  z    +----------------+ event +-------------------+
| simulated | -----> | OnlineDetector| ----> |  AlertManager  | ----> | console (colors)  |
| sensors   |        | (per sensor)  |       | state machine  |       | alerts.jsonl      |
+-----------+        +---------------+       +----------------+       | (email, Telegram) |
                                                                       +-------------------+
```

## Run everything with Docker (app + Prometheus + Grafana)

```bash
cp .env.example .env        # then set GRAFANA_ADMIN_PASSWORD in .env
docker compose up --build
```

| What | URL | Notes |
|---|---|---|
| Grafana dashboard | http://localhost:3000 | opens on "Outlier Detection"; anonymous users can view, and you log in as `admin` with the password from `.env` to edit |
| Prometheus | http://localhost:9090 | try `sensor_zscore` or `sensor_alert_state` in the query box; **Status -> Targets** shows the scrape health |
| Raw app metrics | http://localhost:8000/metrics | the plain-text page Prometheus reads |

Stop with `docker compose down`. Data lives in named volumes (`app-data`,
`prometheus-data`, `grafana-data`) and survives restarts. `docker compose down -v`
wipes it. Grafana applies `GRAFANA_ADMIN_PASSWORD` only on its **first**
start. To change it later, use the Grafana UI or wipe `grafana-data`.

Run the tests inside the image (Python 3.11):
`docker compose run --rm app python -m pytest -p no:cacheprovider`

### How data flows

```
 app container                  prometheus container             grafana container
+---------------------+  pull  +----------------------+  query  +---------------------+
| stream -> detector  | <----- | every 5 s: GET       | <------ | dashboard panels    |
| -> alerts -> Metrics|        | app:8000/metrics,    |  PromQL | run PromQL every 5 s|
| serves /metrics     |        | stores samples (TSDB)|         | and draw charts     |
+---------------------+        +----------------------+         +---------------------+
```

1. **App**: after each reading, `src/metrics.py` updates in-memory gauges and
   counters. `prometheus_client` serves their *current* values as text on
   port 8000. The app doesn't store history and doesn't push anything.
2. **Prometheus** *pulls* (scrapes) that page every 5 seconds
   (`prometheus/prometheus.yml`) and saves each number with a timestamp in
   its time-series database. That's where the history lives.
3. **Grafana** stores no metrics. Each panel sends a PromQL query to Prometheus
   (e.g. `sensor_zscore{sensor="cpu"}`) and draws the result. The datasource
   and dashboard come from `grafana/provisioning/`, so nothing is clicked
   together by hand.

| Metric | Type | Meaning |
|---|---|---|
| `sensor_value{sensor}` | gauge | latest raw reading |
| `sensor_zscore{sensor}` | gauge | latest z-score (NaN during warmup, shown as a gap) |
| `sensor_is_outlier{sensor}` | gauge | 1 if the latest reading is an outlier |
| `sensor_alert_state{sensor}` | gauge | 0 = NORMAL, 1 = ALERTING |
| `alerts_fired_total{sensor}` | counter | ALERT events |
| `alerts_suppressed_total{sensor}` | counter | alerts swallowed by the cooldown |
| `points_processed_total{sensor}` | counter | readings processed |

**The dashboard** has an overview state timeline for all sensors. Below it
is one row per sensor (a repeated row driven by the `Sensor` variable), with:
the value line, with outliers as red dots (`sensor_value and sensor_is_outlier == 1`);
the z-score, with dashed lines at +/-2 and +/-3; the alert state as a state
timeline; and stat panels for alerts fired vs suppressed in the selected time
range (`increase(...[$__range])`).

**Sampling caveat:** the app makes 1 reading per second, but Prometheus only
sees the value present at each 5-second scrape. A one-point spike can therefore
fall between scrapes and never show as a red dot. Alert state and the counters
don't have this problem: state lasts for many seconds, and counters add up
everything. For the full point-by-point record, use `data/alerts.jsonl` (in the
`app-data` volume), or lower `scrape_interval`.

## Quick start (without Docker)

```bash
pip install -r requirements.txt
python -m pytest                         # run the tests
python -m src.main                       # real time: 1 reading/sensor/second, Ctrl+C to stop
python -m src.main --fast --ticks 5000   # simulate 5000 seconds instantly
python -m src.main --fast --ticks 300 --verbose   # see every value and z-score
python -m src.main --metrics             # also serve http://localhost:8000/metrics
```

Run the commands from this folder. All tuning lives in `config.yaml`.

## Components and why they exist

### `src/stream.py`: the data source
A **generator** that yields `Reading(sensor, timestamp, value, anomaly)` objects.
Each value is `baseline + slow sine drift + Gaussian noise`, and sometimes an
anomaly is added:

| anomaly | what it looks like | why it is interesting |
|---|---|---|
| spike | 1 point, 6 to 10 sigma away | should **not** page anyone (K=3 filters it) |
| shift | level jumps 5 to 8 sigma for 10 to 30 points | the classic "something broke" |
| drift | level ramps up to 8 to 12 sigma over 30 to 60 points | slow problems that a fixed threshold catches late |

**Simulated clock.** The timestamps are `start + tick * interval`. Normal
mode sleeps between ticks, and fast mode skips the sleep. Everything
downstream uses the timestamps, never `time.time()`. So fast mode (and the
tests) behave exactly like real time, including the 60 s cooldown.

The `anomaly` label is the ground truth, for debugging only. The detector
never sees it.

### `src/detector.py`: "is this point weird?"
One `OnlineDetector` per sensor keeps a rolling window (default 100) of
recent normal values and computes:

```
z = (x - center) / spread
```

- **zscore** mode: mean and standard deviation.
- **robust** mode: median and `1.4826 * MAD`. A few extreme values barely move
  these numbers, which makes this mode safer when junk leaks into the window.

Three rules make it trustworthy:
1. **Score before adding.** Otherwise the point would be compared against
   statistics it already influenced, which makes it look less extreme.
2. **Warmup** (default 30 points). Statistics from 3 points are meaningless,
   so the detector returns `z=None` until it has enough data.
3. **Outliers stay out of the window.** If the 20 points of a shift were added,
   the window would learn the anomaly as the new normal and the alert would
   end too early. The trade-off: a *permanent* level change keeps alerting
   until a human looks at it. For alerting, that is usually the right choice.

The slow sine drift is *not* flagged, because normal points keep entering the
window and the mean follows the drift.

### `src/alerts.py`: "should a human hear about it?"
A per-sensor state machine with two states:

```
           K consecutive |z| > 3.0                 (fires ALERT)
  NORMAL -------------------------> ALERTING
     ^                                  |
     +----------------------------------+
           M consecutive |z| < 2.0                 (fires RESOLVED)
```

| rule | default | problem it solves |
|---|---|---|
| K consecutive outliers to enter | 3 | single spikes and random noise |
| exit only below 2.0, not 3.0 (**hysteresis**) | 2.0 | flapping when z hovers around 3 |
| M consecutive calm points to exit | 5 | one lucky calm point ending an incident early |
| cooldown after RESOLVED | 60 s | the same problem paging again a minute later |

- Exactly one `ALERT` when entering and one `RESOLVED` when leaving, nothing
  in between.
- `RESOLVED` includes `duration_seconds` (measured from the first outlier of
  the streak) and `peak_z` (the most extreme z, keeping its sign).
- Each incident gets a unique id like `cpu-3f9a1c2b` (from `uuid4`).
- During cooldown, a would-be alert is **counted** in `suppressed_count`
  (once per streak, not once per point) instead of being fired. If the streak
  is still going when the cooldown ends, an alert opens. At that point the
  problem is clearly persistent.

### `src/notifier.py`: delivery
All notifiers share one method, `send(event)`:
- `ConsoleNotifier`: red ALERT and green RESOLVED lines.
- `JsonlNotifier`: appends one JSON object per line to `alerts.jsonl`. It is
  easy to grep and to load with pandas, and appending never corrupts old lines.
- `NotifierGroup`: fans out to several notifiers. If one fails (say, the email
  server is down), the others still get the event.

**Adding a channel:** subclass `Notifier`, implement `send`, and add it in
`build_notifiers()` in `main.py`. The module docstring has a Telegram example.

### `src/metrics.py`: monitoring
Wraps the 7 Prometheus metrics in a `Metrics` class with its own registry,
so tests can inspect values without opening a port. All labelled series are
created at 0 on startup. Otherwise a counter would only appear on its first
increment, and `increase()` in Grafana would miss that event.

### `src/main.py`: wiring
Loads `config.yaml`, creates one detector per sensor, one `AlertManager`, the
notifiers and the metrics, then loops over the stream. `--fast`, `--ticks`,
`--verbose` and `--metrics` override the config. A summary prints at the end,
including on Ctrl+C.

### Deployment files
- `Dockerfile`: `python:3.11-slim`. Dependencies are installed in their own
  cached layer, and the app runs as the non-root `appuser`, which can write
  only to `/app/data`.
- `docker-compose.yml`: the three services, named volumes, and the Grafana
  settings (anonymous Viewer, admin password from `.env`).
- `prometheus/prometheus.yml`: the scrape job.
- `grafana/provisioning/`: the datasource, dashboard provider and dashboard JSON.

### `tests/`
- `test_alerts.py`
  - a **20-point sustained anomaly** produces exactly 1 ALERT and 1 RESOLVED
    with the same incident id;
  - a **single spike** produces no alert when K=3;
  - **cooldown** suppresses a repeat (and counts it once);
  - plus unit tests for hysteresis/flapping, consecutive-ness, signed peak,
    new ids after cooldown, and independence between sensors.
- `test_detector.py`: warmup, score-before-add, no window pollution, robust
  vs classic under contamination, constant signals.
- `test_stream_and_main.py`: fast mode, determinism with a seed, all anomaly
  types appear, JSONL output, a full end-to-end run from `config.yaml` whose
  metrics must match the events that happened.
- `test_metrics.py`: all 7 metrics are exposed, and they track outliers,
  alert state, fired and suppressed alerts correctly.

## Ideas to explore next
- Plot `alerts.jsonl` against the stream's ground-truth labels to measure
  precision and recall, then tune K, M and the thresholds.
- Add an "accept new normal" rule: after N minutes of a stable shift, let the
  window re-learn.
- Replace `np.mean`/`np.std` over the window with running sums (add the new
  value, subtract the one leaving the window) for O(1) updates.
