"""Measure byte delivery cadence from a MiCast HTTP stream."""

from __future__ import annotations

import statistics
import math
import sys
import time
import urllib.request


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure_stream.py STREAM_URL")

    response = urllib.request.urlopen(sys.argv[1], timeout=3)
    gaps: list[float] = []
    sizes: list[int] = []
    started = previous = time.monotonic()
    timed_out = False
    try:
        while time.monotonic() - started < 15:
            data = response.read(1024)
            if not data:
                break
            now = time.monotonic()
            gaps.append(now - previous)
            sizes.append(len(data))
            previous = now
    except TimeoutError:
        timed_out = True
    finally:
        response.close()

    elapsed = max(time.monotonic() - started, 1e-9)
    ordered = sorted(gaps)
    print(response.status, response.headers.get("content-type"))
    print(
        f"seconds={elapsed:.3f} bytes={sum(sizes)} "
        f"kbps={sum(sizes) * 8 / elapsed / 1000:.1f}"
    )
    if gaps:
        print(
            "gap_ms "
            f"min={min(gaps) * 1000:.2f} "
            f"median={statistics.median(gaps) * 1000:.2f} "
            f"p95={ordered[math.ceil(len(ordered) * 0.95) - 1] * 1000:.2f} "
            f"max={max(gaps) * 1000:.2f}"
        )
    print(
        f"gaps_gt_100ms={sum(gap > 0.1 for gap in gaps)} "
        f"gaps_gt_250ms={sum(gap > 0.25 for gap in gaps)}"
    )
    print(f"timed_out={timed_out}")


if __name__ == "__main__":
    main()
