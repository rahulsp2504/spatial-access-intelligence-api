#!/usr/bin/env python3
"""
Benchmark script — measures p50/p95 latency for each endpoint and
prints a Markdown table ready to paste into README.md.

Usage:
    python scripts/benchmark.py [--base-url http://localhost:8000] [--runs 10]

Expects the API to be running and seeded before execution.
"""

import argparse
import statistics
import time

import httpx

BASE_URL = "http://localhost:8000"
RUNS = 10

# Orange County, CA — UCI coordinates
OC_LAT = 33.6405
OC_LON = -117.8443

SCENARIOS = [
    {
        "name": "GET /facilities/nearest",
        "method": "GET",
        "path": "/facilities/nearest",
        "params": {
            "lat": OC_LAT,
            "lon": OC_LON,
            "facility_type": "grocery",
            "k": 5,
            "radius_km": 5.0,
        },
    },
    {
        "name": "POST /isochrone (walk, 15min) — warm",
        "method": "POST",
        "path": "/isochrone",
        "json": {"lat": OC_LAT, "lon": OC_LON, "travel_time_minutes": 15, "mode": "walk"},
    },
    {
        "name": "POST /isochrone (drive, 15min) — warm",
        "method": "POST",
        "path": "/isochrone",
        "json": {"lat": OC_LAT, "lon": OC_LON, "travel_time_minutes": 15, "mode": "drive"},
    },
    {
        "name": "POST /coverage/gap (grocery, walk, 15min)",
        "method": "POST",
        "path": "/coverage/gap",
        "json": {
            "minx": -117.90,
            "miny": 33.62,
            "maxx": -117.80,
            "maxy": 33.68,
            "facility_type": "grocery",
            "travel_time_minutes": 15,
            "mode": "walk",
        },
    },
]


def measure(client: httpx.Client, scenario: dict, runs: int) -> dict:
    latencies = []
    for i in range(runs):
        start = time.perf_counter()
        if scenario["method"] == "GET":
            r = client.get(scenario["path"], params=scenario.get("params"))
        else:
            r = client.post(scenario["path"], json=scenario.get("json"))
        elapsed_ms = (time.perf_counter() - start) * 1000

        if r.status_code != 200:
            print(f"  [run {i+1}] ERROR {r.status_code}: {r.text[:120]}")
            continue
        latencies.append(elapsed_ms)

    if not latencies:
        return {"p50": "ERR", "p95": "ERR", "min": "ERR", "max": "ERR"}

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else max(latencies)
    return {
        "p50": f"{p50:.0f} ms",
        "p95": f"{p95:.0f} ms",
        "min": f"{min(latencies):.0f} ms",
        "max": f"{max(latencies):.0f} ms",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--runs", type=int, default=RUNS)
    args = parser.parse_args()

    print(f"Benchmarking {args.base_url} — {args.runs} runs per endpoint\n")

    rows = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        # Warm-up pass (populates graph cache)
        print("Warming up graph cache …")
        for sc in SCENARIOS:
            if sc["method"] == "POST":
                client.post(sc["path"], json=sc.get("json"))
        print("Warm-up done.\n")

        for sc in SCENARIOS:
            print(f"Measuring: {sc['name']}")
            stats = measure(client, sc, args.runs)
            rows.append((sc["name"], stats))

    # ── Markdown table ─────────────────────────────────────────────────────────
    header = "| Endpoint | p50 | p95 | min | max |"
    sep    = "|---|---|---|---|---|"
    lines  = [header, sep]
    for name, s in rows:
        lines.append(f"| {name} | {s['p50']} | {s['p95']} | {s['min']} | {s['max']} |")

    print("\n## Benchmark Results (paste into README)\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
