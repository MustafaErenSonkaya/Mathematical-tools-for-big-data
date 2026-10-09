"""
Entry point: wires stream -> detectors -> alert manager -> notifiers.

    reading --> OnlineDetector (one per sensor) --> z-score
            --> AlertManager (state machine per sensor) --> AlertEvent or None
            --> NotifierGroup (console + alerts.jsonl)
    and after every reading: Metrics (served at :8000/metrics for Prometheus)

Run from the project folder:
    python -m src.main                 # real time, 1 reading/sensor/second
    python -m src.main --fast --ticks 5000
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from src.alerts import AlertManager, State
from src.detector import OnlineDetector
from src.metrics import Metrics
from src.notifier import ConsoleNotifier, JsonlNotifier, Notifier, NotifierGroup
from src.stream import specs_from_config, stream_readings


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_notifiers(cfg: dict) -> Notifier:
    notifiers: list[Notifier] = [ConsoleNotifier(use_colors=cfg.get("console_colors", True))]
    if cfg.get("jsonl_path"):
        notifiers.append(JsonlNotifier(cfg["jsonl_path"]))
    # Later: if cfg.get("telegram"): notifiers.append(TelegramNotifier(**cfg["telegram"]))
    return NotifierGroup(notifiers)


def run(
    cfg: dict,
    fast: bool | None = None,
    max_ticks: int | None = None,
    verbose: bool = False,
    serve_metrics: bool | None = None,
):
    stream_cfg, det_cfg, alert_cfg = cfg["stream"], cfg["detector"], cfg["alerts"]
    metrics_cfg = cfg.get("metrics", {})

    # Command-line flags override the config file when given.
    fast = stream_cfg.get("fast_mode", False) if fast is None else fast
    max_ticks = stream_cfg.get("max_ticks") if max_ticks is None else max_ticks

    specs = specs_from_config(stream_cfg["sensors"])
    readings = stream_readings(
        specs,
        interval_seconds=stream_cfg["interval_seconds"],
        fast=fast,
        max_ticks=max_ticks,
        anomaly_probability=stream_cfg["anomaly_probability"],
        seed=stream_cfg.get("seed"),
    )

    # One detector per sensor: each sensor has its own idea of "normal".
    detectors = {spec.name: OnlineDetector(**det_cfg) for spec in specs}
    manager = AlertManager(**alert_cfg)
    notifier = build_notifiers(cfg["notifier"])
    events = Counter()

    # Metrics are always collected (cheap); the HTTP endpoint is optional.
    metrics = Metrics([spec.name for spec in specs])
    serve_metrics = metrics_cfg.get("enabled", False) if serve_metrics is None else serve_metrics
    if serve_metrics:
        port = metrics_cfg.get("port", 8000)
        metrics.serve(port)
        print(f"Metrics at http://localhost:{port}/metrics", flush=True)

    try:
        for r in readings:
            d = detectors[r.sensor].update(r.value)
            if verbose:
                z = "warmup" if d.z is None else f"{d.z:+6.2f}"
                print(f"  {r.sensor:<9} {r.value:10.3f}  z={z}  {r.anomaly or ''}")
            event = manager.process(r.sensor, r.timestamp, d.z, r.value)
            metrics.observe(r.sensor, d, manager.state_of(r.sensor), event.kind if event else None)
            if event is not None:
                events[event.kind] += 1
                notifier.send(event)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        notifier.close()

    print_summary(manager, events)
    return manager, events, metrics


def print_summary(manager: AlertManager, events: Counter) -> None:
    print("\n--- summary ---")
    print(f"ALERT events:    {events['ALERT']}")
    print(f"RESOLVED events: {events['RESOLVED']}")
    for name, s in manager.sensors.items():
        still_open = " (incident still open)" if s.state is State.ALERTING else ""
        print(f"  {name:<9} suppressed during cooldown: {s.suppressed_count}{still_open}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Streaming outlier detection with smart alerts")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--fast", action="store_true", default=None, help="no sleeping between ticks")
    parser.add_argument("--ticks", type=int, default=None, help="stop after N ticks")
    parser.add_argument("--verbose", action="store_true", help="print every reading and z-score")
    parser.add_argument("--metrics", action="store_true", default=None, help="serve Prometheus metrics")
    args = parser.parse_args(argv)

    run(
        load_config(args.config),
        fast=args.fast,
        max_ticks=args.ticks,
        verbose=args.verbose,
        serve_metrics=args.metrics,
    )


if __name__ == "__main__":
    main()
