"""CSS-15 scrypt cost probe; not an age implementation or Pi timing benchmark."""

import argparse
import hashlib
import statistics
import time


def main():
    parser = argparse.ArgumentParser(description="Measure one age-compatible scrypt factor")
    parser.add_argument("log_n", type=int, choices=range(14, 19))
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1 or args.repetitions > 10:
        parser.error("repetitions must be between 1 and 10")

    elapsed = []
    for _ in range(args.repetitions):
        started = time.perf_counter()
        hashlib.scrypt(
            b"css15-public-dummy",
            salt=b"age-encryption.org/v1/scrypt" + b"css15-dummy-salt",
            n=1 << args.log_n,
            r=8,
            p=1,
            dklen=32,
            maxmem=1024 * 1024 * 1024,
        )
        elapsed.append(time.perf_counter() - started)
    memory_mib = 128 * 8 * (1 << args.log_n) / (1024 * 1024)
    print(
        f"logN={args.log_n} scrypt array={memory_mib:.0f} MiB "
        f"median={statistics.median(elapsed):.3f}s "
        f"range={min(elapsed):.3f}-{max(elapsed):.3f}s "
        f"({args.repetitions} runs, current host only)"
    )


if __name__ == "__main__":
    main()
