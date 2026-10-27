"""Terminal benchmark with explicit queries and durable sample export."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from .core import (parse_lines, positive_float, query, summarize, system_dns_servers,
                   validate_domains, validate_servers)


def build_parser():
    parser = argparse.ArgumentParser(description="Compare DNS latency and reliability.")
    parser.add_argument("-d", "--domain", action="append", help="DNS name; repeat for multiple names")
    parser.add_argument("-f", "--fqdn-file", type=Path, help="File of DNS names, one per line")
    parser.add_argument("-s", "--server", action="append", help="Resolver IP; repeat to compare servers")
    parser.add_argument("--dns-file", type=Path, help="File of resolver IP addresses")
    parser.add_argument("-i", "--interval", type=positive_float, default=5.0,
                        help="Seconds to wait between rounds (default: 5)")
    parser.add_argument("-n", "--iterations", type=int, default=0, help="Rounds; 0 runs until Ctrl+C")
    parser.add_argument("--timeout", type=positive_float, default=2.0, help="Query timeout in seconds")
    parser.add_argument("--record-type", choices=("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA"),
                        default="A")
    parser.add_argument("--tcp", action="store_true", help="Use TCP instead of UDP")
    parser.add_argument("--workers", type=int, default=16, help="Maximum concurrent queries")
    parser.add_argument("--csv", type=Path, help="Write and flush each sample to this CSV file")
    parser.add_argument("--save-excel", action="store_true", help="Export dns_benchmark.xlsx at the end")
    return parser


def load_inputs(args):
    if args.iterations < 0:
        raise ValueError("iterations must be zero or greater")
    if not 1 <= args.workers <= 256:
        raise ValueError("workers must be between 1 and 256")
    domains = list(args.domain or [])
    if args.fqdn_file:
        domains.extend(parse_lines(args.fqdn_file.read_text(encoding="utf-8-sig")))
    elif not domains and Path("servers.ls").exists():
        domains.extend(parse_lines(Path("servers.ls").read_text(encoding="utf-8-sig")))
    domains = validate_domains(domains or ["google.com"])
    servers = list(args.server or [])
    if args.dns_file:
        servers.extend(parse_lines(args.dns_file.read_text(encoding="utf-8-sig")))
    servers = validate_servers(servers) if servers else system_dns_servers()
    if not servers:
        raise ValueError("No resolvers found. Supply --server IP or --dns-file PATH.")
    return domains, servers


def display(history):
    def number(value):
        return "N/A" if value is None else f"{value:.2f}"

    for domain, servers in history.items():
        print(f"\n{domain}")
        print(f"{'Resolver':<39} {'Samples':>7} {'Fail %':>7} {'Min ms':>9} {'Max ms':>9} {'Avg ms':>9} {'Median':>9} {'P95 ms':>9} {'Latest':>10} Trend")
        for server, samples in servers.items():
            stats = summarize(samples)
            latest = samples[-1] if samples else None
            latest_text = number(latest.latency_ms) if latest and latest.status == "OK" else (latest.status if latest else "Pending")
            trend = "".join("x" if sample.latency_ms is None else
                            ("." if index == 0 or samples[index - 1].latency_ms is None else
                             ("+" if sample.latency_ms > samples[index - 1].latency_ms else "-"))
                            for index, sample in enumerate(samples))[-20:]
            print(f"{server:<39} {stats['attempts']:>7} {stats['failure_pct']:>7.1f} "
                  f"{number(stats['min_ms']):>9} {number(stats['max_ms']):>9} "
                  f"{number(stats['avg_ms']):>9} {number(stats['median_ms']):>9} "
                  f"{number(stats['p95_ms']):>9} {latest_text:>10} {trend}")
            if latest and latest.status != "OK":
                print(f"  {latest.answer}")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        domains, servers = load_inputs(args)
        if args.save_excel:
            import xlsxwriter  # Check before running a benchmark.
        csv_file = args.csv.open("w", newline="", encoding="utf-8") if args.csv else None
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))

    history = {domain: {server: [] for server in servers} for domain in domains}
    excel_rows = {domain: [] for domain in domains}
    writer = csv.writer(csv_file) if csv_file else None
    if writer:
        writer.writerow(["iteration", "timestamp_utc", "domain", "resolver", "record_type",
                         "transport", "status", "latency_ms", "answer"])
        csv_file.flush()
    print(f"Testing {args.record_type} over {'TCP' if args.tcp else 'UDP'}; timeout {args.timeout:g}s")
    print("Statistics cover the full run; failed queries are excluded from latency statistics.")
    iteration = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            while args.iterations == 0 or iteration < args.iterations:
                iteration += 1
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                futures = {executor.submit(query, server, domain, args.record_type, args.timeout, args.tcp):
                           (domain, server) for domain in domains for server in servers}
                for future in as_completed(futures):
                    domain, server = futures[future]
                    sample = future.result()
                    history[domain][server].append(sample)
                    if writer:
                        writer.writerow([iteration, timestamp, domain, server, args.record_type,
                                         "TCP" if args.tcp else "UDP", sample.status,
                                         sample.latency_ms, sample.answer])
                        csv_file.flush()
                if args.save_excel:
                    for domain in domains:
                        excel_rows[domain].append([iteration, timestamp] +
                            [history[domain][server][-1].latency_ms for server in servers])
                display(history)
                if args.iterations == 0 or iteration < args.iterations:
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped. Completed samples are retained.")
    finally:
        if csv_file:
            csv_file.close()
        if args.save_excel:
            from .export import write_excel
            try:
                write_excel("dns_benchmark.xlsx", excel_rows, domains, servers)
            except (OSError, xlsxwriter.exceptions.XlsxWriterException) as exc:
                print(f"Excel export failed: {exc}", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
