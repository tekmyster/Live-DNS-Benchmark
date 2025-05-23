# Live DNS Benchmark

A real-time DNS resolution benchmarking tool with both CLI and GUI front-ends.  
Test one or more FQDNs against your system (or custom) DNS servers, visualize trends, and export live data to Excel.  

---

## Features

- **CLI mode** (`live_dns_benchmark CLI.py`):  
  - ANSI-colored terminal output  
  - Live sparklines showing trends  
  - Optional Excel export with charts  
- **GUI mode** (`live_dns_benchmark_GUI.py`):  
  - Interactive Tkinter interface  
  - Live graphs per domain and per-server sparklines  
  - Save DNS/FQDN lists, adjust interval & iterations  
  - SNMP agent exposing min/max/avg via custom MIB  
- **Common**:  
  - Concurrent resolution (thread-pool)  
  - High-resolution RTT via `ping3`  
  - Automatic fallback to system DNS servers  
  - History length configurable (default 20 samples)  

---

## 🛠️ Installation & Setup

1. **Clone the repo**  
   ```bash
   git clone https://github.com/<your-org>/live_dns_benchmark.git
   cd live_dns_benchmark
````

2. **Create a virtual environment** *(strongly recommended)*

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # on Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   <!--  
     ❗ **Security note**:  
     - Pin exact versions in `requirements.txt` to avoid supply-chain risks.  
     - Regularly run `pip-audit` or `safety` to check for known vulnerabilities.  
   -->

4. **(Optional) Install for easy CLI use**

   ```bash
   pip install .
   ```

   This creates console scripts:

   * `dns-benchmark-cli` → `live_dns_benchmark CLI.py`
   * `dns-benchmark-gui` → `live_dns_benchmark_GUI.py`

---

## ⚙️ Configuration

* **FQDN list**: `servers.ls` (one domain per line)
* **DNS servers list**: `dns.ls` (one resolver IP per line; autogenerated if missing)
* **SNMP**: `snmp_config.json`

  ```json
  {
    "version": "v3",
    "community": "public",      // for v1/v2c
    "v3": {
      "user": "snmpuser",
      "authProtocol": "SHA",    // MD5 | SHA
      "authKey": "...",         // strong passphrase!
      "privProtocol": "AES",    // DES | AES
      "privKey": "...",
      "securityLevel": "authPriv"
    },
    "port": 1161
  }
  ```

  <!--  
    🔒 **Security best practice**:  
    - Use SNMPv3 with `authPriv` for encryption.  
    - Store credentials in a protected vault or environment variables instead of plaintext.  
  -->

---

## 🚀 Usage

### CLI

```bash
# list help
python "live_dns_benchmark CLI.py" --help

# basic test
python "live_dns_benchmark CLI.py" -d example.com -i 2.5 -n 10

# use your own servers.fqdn file and save Excel
python "live_dns_benchmark CLI.py" -f custom_servers.ls --save-excel
```

### GUI

```bash
python live_dns_benchmark_GUI.py
```

1. Set **Interval** (seconds)
2. Set **Iterations** (0 for infinite)
3. Edit or save FQDN & DNS server lists
4. Click **Start Benchmark** → view live sparklines & graphs
5. **Save Excel As…** to capture results

---

## 📈 Extending & Best Practices

* **Logging & Monitoring**

  * Integrate Python’s `logging` module instead of `print()` for adjustable log levels.
  * Push metrics to Prometheus (via a custom exporter) for centralized monitoring.
* **Testing**

  * Write unit tests for DNS parsing, SNMP config loading, sparkline generation, etc.
  * Use CI (GitHub Actions) to auto-run linting (`flake8`), type checks (`mypy`), and tests.
* **Packaging**

  * Provide a `setup.py` / `pyproject.toml` for proper distribution.
  * Consider publishing to PyPI or building Docker images.

---

## 📜 License & Contributing

1. **Choose a license** (MIT, Apache 2.0, GPLv3, …)
2. Fork, add issues, open pull requests
3. Follow a branching strategy (e.g., GitFlow)

---

## ❓ Questions & Next Steps

1. **License**: Which open-source license would you like to apply?
2. **Platform support**: Are you targeting Windows only, or also Linux/macOS?
3. **Docker**: Interested in a Dockerfile for containerized benchmarking?
4. **CI/CD**: Would you like GitHub Actions templates for linting, testing, and releases?
5. **Security**: Any requirements for secrets management (e.g., SNMP credentials)?

---

*Thank you for reviewing! Feel free to suggest additions or ask if anything’s unclear.*

```
```
