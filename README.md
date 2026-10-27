# Live DNS Benchmark

Compare DNS resolvers over repeated queries. The CLI reports response time and reliability,
and exports measurements for later analysis. The Tkinter desktop interface uses the
same query engine and supports live charts, failure statistics, and Excel export.

## Install the CLI

Python 3.10 or newer is required.

```powershell
git clone https://github.com/tekmyster/Live-DNS-Benchmark.git
cd Live-DNS-Benchmark
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[excel]"
```

On Linux/macOS, activate with `source .venv/bin/activate`. Excel support is optional;
`python -m pip install -e .` installs only the CLI and its DNS dependency.

## Run a comparison

```powershell
dns-benchmark-cli -d example.com -s 1.1.1.1 -s 8.8.8.8 -n 20 -i 1 --csv samples.csv --save-excel
dns-benchmark-cli -d example.com --record-type AAAA --tcp -n 5
dns-benchmark-cli -f domains.txt --dns-file resolvers.txt --timeout 1 -n 10
```

Without installing, run `python -m dns_benchmark.cli` from the repository root.
The original entry point also works:

```powershell
python "CLI/live_dns_benchmark CLI.py" --help
```

- Repeat `--domain` and `--server` to compare multiple names and resolvers.
- If no resolver is supplied, use the system's configured DNS servers.
- With no explicit domain input, read `servers.ls` in the current working directory,
  falling back to `google.com` if that list is missing or empty. An explicit `-d`
  takes precedence over that default file. Explicit `-f` and `-d` inputs combine.
- List files support blank lines, `#` comments, and duplicate removal. Resolver
  addresses must be IPv4 or IPv6 literals. Enter DNS names without URL schemes or paths.
- Supported record types: A, AAAA, CNAME, MX, NS, TXT, SOA. UDP is the default;
  dnspython may retry a truncated UDP response over TCP. `--tcp` forces TCP.
- `--timeout` controls each query's lifetime independently of the sampling interval.
- `--interval` is the wait **after** a round completes. No extra wait follows the last round.
- `--workers` bounds concurrency (default 16, maximum 256).
- `--iterations 0` runs until Ctrl+C. Completed CSV rows are flushed as they arrive.

## Reading results

The table shows attempts, failure percentage, minimum, maximum, average, median,
95th percentile, and the latest outcome for each resolver and domain. Statistics
cover the full run. Median/P95 use linear interpolation between sorted samples.
Failed queries contribute to failure percentage and are excluded from latency
statistics. Compare failure rates alongside timings: a resolver that times out
often should not look better simply because only its fastest replies succeeded.

The last 20 outcomes appear as a trend: `x` failed, `.` initial success or recovery,
`+` slower than the previous success, and `-` equal or faster.

Queries use a monotonic high-resolution timer, explicit resolvers, no client-side
DNS cache, and no search suffix expansion. Resolver/upstream caches still apply;
this is a repeated-query benchmark, not a guaranteed cold-cache test. Latency
measures successful DNS resolution, not ICMP ping time or web-page load time.

CSV includes UTC timestamps, domain, resolver, record type, requested transport,
status, latency, and the answer/error. Failures have blank latency. Statuses include
OK, NXDOMAIN, NOANSWER, TIMEOUT, and ERROR.

`--save-excel` writes `dns_benchmark.xlsx` at the end of a run, with one worksheet
and chart per domain. Failed samples are gaps, rather than zero-millisecond results.
Long worksheet names are made unique. A replacement workbook is completed before
replacing an existing file; if Excel locks the destination, the old file is retained
and the CLI reports an export failure. Use CSV for incremental capture during long runs.

## Desktop interface

```powershell
python -m pip install -e ".[gui,excel]"
python GUI/live_dns_benchmark_GUI.py
```

Tkinter must be available in your Python installation. Windows Python normally
includes it; some Linux distributions package it separately.

Enter domains and resolver IP addresses, choose record type, timeout and transport,
then press **Start**. Each domain gets a results tab with latency statistics,
failure rate, separate ICMP ping timing, and the last 20 outcomes. **Live chart**
opens the DNS latency plot. Double-click a row or press Enter for the full response.
**About measurements** explains the statistics and integration status.

**Stop** finishes the current round and wakes an interval wait immediately. Start
stays disabled until the worker exits; finite runs restore it automatically.
Closing during a run also finishes the round before the application exits. Query
settings apply to the next run; valid interval changes take effect between rounds.

Domain/resolver lists and the live workbook are stored in the `GUI` directory when
launched using the command above. Saved lists are validated before being written.
The live workbook is replaced atomically after each round; an export failure leaves
measurements running. **Export Excel** creates a fresh snapshot of the current data,
including after a run stops, rather than copying a file being rewritten by the worker.

Failures remain blank in exports and gaps in the live chart. In the small trend
images, red crosses mark failures and amber squares mark changes between successful
address answers. Resolver IPs mentioned in errors are never recorded as answers.

The project's `AGENTS.md` records the recovered default product UX contract:
concise labels, readable tables, purposeful charts, clear states and progressive
disclosure. Desktop behavior is covered by integration tests. **Rendered visual
inspection has not been completed**: the Windows capture helper failed to capture
the running window. Appearance, narrow-window readability and full keyboard usability
therefore remain unverified; passing behavior tests does not establish visual approval.

### Remaining limitations

- The legacy SNMP integration is optional. Missing legacy imports no longer prevent
  the GUI from opening. Duplicate implementations have been consolidated, but SNMP
  polling and custom MIB behavior have **not** been repaired or validated. Modern
  `pysnmp` releases do not provide its `asyncore` transport. The default dependency
  sets intentionally do not install this old integration.
- Continuous runs retain samples in memory for full-run statistics and exports.
  Use finite runs for long comparisons.
- Exporting a large snapshot can briefly occupy the UI thread.
- Linux/macOS GUI appearance and frozen executable builds have not been verified.

## Development

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

Tests use local DNS responders for real UDP/TCP exchanges and inspect generated
workbooks. They do not depend on public resolver availability or modify system DNS,
firewall rules, credentials, or access permissions. GUI tests use temporary files,
mocked query results, and withdrawn windows; they skip when GUI dependencies or a
desktop display are unavailable. Install `.[test,gui]` to run them locally.
CI covers the CLI on Windows/Linux with Python 3.10/3.13 and the GUI on Windows 3.13.
