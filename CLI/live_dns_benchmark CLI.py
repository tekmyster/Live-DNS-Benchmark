import argparse
import subprocess
import re
import time
import platform
import dns.resolver
import concurrent.futures
import ipaddress
import os
from datetime import datetime

# Number of samples to show in the live graph column.
HISTORY_LEN = 20

# ANSI color codes for colored output
RESET   = "\033[0m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
ORANGE  = "\033[38;5;208m"  # Requires 256-color support

def get_system_dns_servers():
    """
    Reads the system's DNS servers by running 'ipconfig /all' (Windows only)
    and returns a list of valid IP addresses.
    """
    try:
        output = subprocess.check_output(["ipconfig", "/all"], text=True)
    except subprocess.CalledProcessError as e:
        print("Error running ipconfig:", e)
        return []
    
    dns_servers = []
    capture = False
    for line in output.splitlines():
        if "DNS Servers" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                candidate = parts[1].strip()
                try:
                    ipaddress.ip_address(candidate)
                    dns_servers.append(candidate)
                except ValueError:
                    pass
            capture = True
        elif capture:
            if line.startswith(" "):
                candidate = line.strip()
                try:
                    ipaddress.ip_address(candidate)
                    dns_servers.append(candidate)
                except ValueError:
                    pass
            else:
                capture = False
    return list(dict.fromkeys(dns_servers))

def resolve_domain(server_ip, domain):
    """
    Uses dnspython to resolve the given domain using server_ip.
    Returns a tuple (latency_in_ms, result_text).
    In case of failure, latency is None and result_text holds the error.
    """
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server_ip]
    start_time = time.time()
    try:
        answer = resolver.resolve(domain, "A")
        latency = (time.time() - start_time) * 1000  # in ms
        result_text = ", ".join([str(r) for r in answer])
    except Exception as e:
        latency = None
        result_text = str(e)
    return (latency, result_text)

def compute_history_symbols(history, tolerance=0.01):
    """
    Given a history list (each element is a tuple: (latency, result)),
    compute a symbol for each sample based on comparing the current latency to the previous one:
      - For a failed measurement (latency is None): show a red "_" (underscore).
      - For the first successful measurement: show "o" (in yellow).
      - For subsequent samples:
          * If current latency is higher (by more than tolerance) than the previous sample: show an orange "O".
          * If lower: show a green ".".
          * If nearly the same: show a yellow "o".
    Returns a string built from the last HISTORY_LEN symbols.
    """
    symbols = []
    for i, (lat, _) in enumerate(history):
        if lat is None:
            symbol = f"{RED}_{RESET}"
        else:
            if i == 0:
                symbol = f"{YELLOW}o{RESET}"
            else:
                prev = history[i-1][0]
                if prev is None:
                    symbol = f"{YELLOW}o{RESET}"
                else:
                    if abs(lat - prev) < tolerance:
                        symbol = f"{YELLOW}o{RESET}"
                    elif lat > prev:
                        symbol = f"{ORANGE}O{RESET}"
                    else:
                        symbol = f"{GREEN}.{RESET}"
        symbols.append(symbol)
    return "".join(symbols[-HISTORY_LEN:])

def clear_screen():
    """Clears the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def update_display_all(results_history):
    """
    Clears the screen and prints a table for each FQDN.
    For each domain (key in results_history), it prints:
      No. | DNS Server | Result                           | MIN (ms) | MAX (ms) | AVG (ms) | Latest (ms) | Graph
    """
    clear_screen()
    for domain, history_dict in results_history.items():
        print(f"Testing: {domain}")
        header = (
            f"{'No.':<4} | {'DNS Server':<18} | {'Result':<40} | "
            f"{'MIN (ms)':>10} | {'MAX (ms)':>10} | {'AVG (ms)':>10} | {'Latest (ms)':>12} | Graph"
        )
        print(header)
        print("-" * len(header))
        for idx, (ip, hist) in enumerate(history_dict.items(), start=1):
            successes = [lat for lat, _ in hist if lat is not None]
            if successes:
                min_val = min(successes)
                max_val = max(successes)
                avg_val = sum(successes) / len(successes)
                latest_latency = hist[-1][0]
                latest_result = hist[-1][1]
                min_str = f"{min_val:10.2f}"
                max_str = f"{max_val:10.2f}"
                avg_str = f"{avg_val:10.2f}"
                latest_str = f"{latest_latency:12.2f}" if latest_latency is not None else f"{'Failed':>12}"
            else:
                min_str = max_str = avg_str = latest_str = f"{'N/A':>10}"
                latest_result = "N/A"
            result_str = (latest_result[:37] + "...") if len(latest_result) > 40 else latest_result
            graph = compute_history_symbols(hist)
            print(f"{idx:<4} | {ip:<18} | {result_str:<40} | {min_str} | {max_str} | {avg_str} | {latest_str} | {graph}")
        print("\n")

def save_all_to_excel(excel_data, domains, servers_order):
    """
    Saves the logged data to an Excel file ("dns_benchmark.xlsx") with one worksheet per domain.
    Each worksheet has columns: Iteration, Timestamp, then one column per DNS server.
    A line chart is created to plot latency over iterations.
    """
    try:
        import xlsxwriter
    except ImportError:
        print("xlsxwriter is not installed. Please install it via 'pip install xlsxwriter'")
        return

    workbook = xlsxwriter.Workbook("dns_benchmark.xlsx")
    for domain in domains:
        # Worksheet names are limited to 31 characters.
        worksheet = workbook.add_worksheet(domain[:31])
        headers = ["Iteration", "Timestamp"] + servers_order
        for col, header in enumerate(headers):
            worksheet.write(0, col, header)
        rows = excel_data[domain]
        for row_num, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                worksheet.write(row_num, col, value)
        # Create a chart for this domain.
        chart = workbook.add_chart({'type': 'line'})
        num_rows = len(rows)
        for i, server in enumerate(servers_order):
            col = 2 + i
            chart.add_series({
                'name':       [domain[:31], 0, col],
                'categories': [domain[:31], 1, 0, num_rows, 0],
                'values':     [domain[:31], 1, col, num_rows, col],
            })
        chart.set_title({'name': f'Latency for {domain}'})
        chart.set_x_axis({'name': 'Iteration'})
        chart.set_y_axis({'name': 'Latency (ms)'})
        worksheet.insert_chart('H2', chart)
    workbook.close()

def read_fqdn_list(file_path, default_domain):
    """
    Reads a file (one FQDN per line) and returns a list of nonempty, stripped FQDNs.
    If the file is missing or empty, returns a list with the default_domain.
    """
    if not os.path.exists(file_path):
        return [default_domain]
    with open(file_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines if lines else [default_domain]

def main():
    parser = argparse.ArgumentParser(
        description="Live DNS Benchmark: test one or more FQDNs on system DNS servers and display live trends."
    )
    parser.add_argument("--interval", "-i", type=float, default=5.0,
                        help="Time in seconds between tests (default: 5.0 seconds).")
    parser.add_argument("--iterations", "-n", type=int, default=0,
                        help="Number of iterations to run (0 for continuous until interrupted).")
    parser.add_argument("--domain", "-d", type=str, default="google.com",
                        help="FQDN to test (used if no fqdn file is provided; default: google.com).")
    parser.add_argument("--fqdn-file", "-f", type=str, default="servers.ls",
                        help="File containing list of FQDNs to test (one per line; default: servers.ls).")
    parser.add_argument("--save-excel", action="store_true",
                        help="If provided, log the output to dns_benchmark.xlsx with charts.")
    args = parser.parse_args()
    interval = args.interval
    iterations = args.iterations
    default_domain = args.domain
    fqdn_file = args.fqdn_file
    save_excel_flag = args.save_excel

    # Read list of FQDNs from file (or use default)
    domains = read_fqdn_list(fqdn_file, default_domain)
    
    dns_servers = get_system_dns_servers()
    if not dns_servers:
        print("No valid system DNS servers found.")
        return
    servers_order = list(dns_servers)

    # Initialize results_history as a dictionary:
    #   Key: domain, Value: dictionary mapping each DNS server IP to its history (list of (latency, result))
    results_history = { domain: { ip: [] for ip in servers_order } for domain in domains }
    # For Excel logging: for each domain, a list of rows.
    excel_data = { domain: [] for domain in domains }

    print("System DNS servers found:")
    for ip in servers_order:
        print("  ", ip)
    print(f"\nBenchmarking DNS queries for FQDNs from: {', '.join(domains)}")
    print(f"Interval between tests: {interval} seconds")
    if iterations:
        print(f"Number of iterations: {iterations}")
    else:
        print("Running continuously until interrupted (Ctrl+C)")
    if save_excel_flag:
        print("Excel logging is ENABLED; output will be saved to dns_benchmark.xlsx.")
    time.sleep(2)

    iteration_count = 0
    try:
        while iterations == 0 or iteration_count < iterations:
            current_time = datetime.now()
            timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            # Build futures for all domains and DNS servers concurrently.
            futures = {}
            with concurrent.futures.ThreadPoolExecutor() as executor:
                for domain in domains:
                    for ip in servers_order:
                        future = executor.submit(resolve_domain, ip, domain)
                        futures[future] = (domain, ip)
                for future in concurrent.futures.as_completed(futures):
                    domain, ip = futures[future]
                    try:
                        latency, result_text = future.result()
                    except Exception as exc:
                        latency, result_text = None, str(exc)
                    results_history[domain][ip].append((latency, result_text))
            iteration_count += 1

            # Append a row for Excel logging for each domain.
            for domain in domains:
                row = [iteration_count, timestamp_str]
                for ip in servers_order:
                    # Write the latest latency (or None) for each DNS server.
                    if results_history[domain][ip]:
                        row.append(results_history[domain][ip][-1][0])
                    else:
                        row.append(None)
                excel_data[domain].append(row)

            update_display_all(results_history)
            if save_excel_flag:
                save_all_to_excel(excel_data, domains, servers_order)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nBenchmarking interrupted by user.")
        update_display_all(results_history)
        if save_excel_flag:
            save_all_to_excel(excel_data, domains, servers_order)

if __name__ == "__main__":
    main()
