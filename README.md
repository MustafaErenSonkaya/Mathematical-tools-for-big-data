# Sensor Anomaly Detection

A small demo of real-time anomaly detection with live dashboards.

Two fake sensors (temperature and pressure) send a reading every 0.5 seconds. The app spots unusual readings, raises alerts without spamming you, and shows everything live in Grafana.

```
app (Python) ──metrics──▶ Prometheus ──queries──▶ Grafana dashboard
 :8000                     :9090                   :3000
```

## Quick start

You only need [Docker](https://www.docker.com/products/docker-desktop/).

```bash
docker compose up --build -d
```

Then open:

| What | URL |
|------|-----|
| Grafana dashboard (no login needed) | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Raw metrics | http://localhost:8000/metrics |

To see the alerts as they happen:

```bash
docker compose logs -f app
```

To stop everything:

```bash
docker compose down
```

## How it works

### 1. Fake sensors with injected problems

Each sensor normally wobbles around a steady value. Sometimes the app breaks it on purpose:

- **Spike:** one single strange reading (3% chance per reading)
- **Sustained anomaly:** the value jumps and stays off for 10–30 readings (1% chance per reading)

### 2. Spotting outliers with a z-score

For each new reading, the app compares it with the last 50 normal readings:

```
z = (reading - average) / standard deviation
```

The z-score says how many "typical wobbles" away from normal a reading is. If `|z| > 3`, the reading counts as an **outlier**.

Outliers are left out of the 50-reading history. Otherwise a long anomaly would slowly become the "new normal" and stop being detected.

### 3. Smart alerts (no spam)

Each sensor is either **NORMAL** or **ALERTING**:

```
            3 outliers in a row
   NORMAL ───────────────────────▶ ALERTING      prints "ALERT"
     ▲                                │
     └────────────────────────────────┘
            5 normal readings in a row           prints "RESOLVED"
```

- A single spike does **not** raise an alert; it takes 3 outliers in a row.
- While a sensor is already alerting, no new alert is printed.
- After an alert is resolved, there's a **30-second cooldown** with no new alerts for that sensor.

Example log:

```
ALERT    temperature: 3 unusual readings in a row (value=19.62, z=-9.70)
RESOLVED temperature: back to normal for 5 readings (no new alerts for 30s)
```

## Metrics

| Metric | Type | Meaning |
|--------|------|---------|
| `sensor_value` | gauge | Latest reading |
| `sensor_zscore` | gauge | How unusual the latest reading is |
| `sensor_alert_state` | gauge | `0` = NORMAL, `1` = ALERTING |
| `alerts_fired_total` | counter | Number of alerts raised so far |

Each metric has a `sensor` label (`temperature` or `pressure`).

## Dashboard

The provisioned Grafana dashboard **Sensor Anomaly Detection** has three panels:

1. **Sensor Value:** the raw readings
2. **Z-Score:** with red bands beyond ±3
3. **Alert State:** a green/red NORMAL/ALERTING timeline

## Project layout

```
app.py                        the simulator, detector and metrics server
Dockerfile, requirements.txt  container for the app
prometheus.yml                Prometheus scrapes app:8000 every 5 s
grafana/provisioning/         Grafana datasource + dashboard, loaded at startup
docker-compose.yml            runs app, Prometheus and Grafana together
```

## Tuning

All the knobs are at the top of [`app.py`](app.py): window size, z-score limit, how many outliers trigger an alert, how many normal readings resolve it, and the cooldown length.

## How it was built

This project was built in a conversation with Claude Code. The full chat is in [`CHAT.md`](CHAT.md).
