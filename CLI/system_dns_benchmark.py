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

# How many samples to show in the live graph column.
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
        # Join returned answers (IP addresses) into a string.
        result_text = ", ".join([str(r) for r in answer])
    except Exception as e:
        latency = None
        result_text = str(e)
    return (latency, result_text)

def compute_history_symbols(history, tolerance=0.01):
    """
    Given a history list (each element is a tuple: (latency, result)),
    compute a symbol for each sample based on comparing the current latency to the previous one:
      - If the current measurement is None: show an underscore "_" in red.
      - For the first successful measurement, show 'o' in yellow.
      - For subsequent measurements:
          * If current > previous (by more than tolerance): show "O" in orange.
          * If current < previous (by more than tolerance): show "." in green.
          * Otherwise, show "o" in yellow.
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

def update_display(results_history, domain):
    """
    Clears the screen and prints an updated table.
    The table includes:
      No. | DNS Server | Result                           | MIN (ms) | MAX (ms) | AVG (ms) | Latest (ms) | Graph
    Also, the active test domain is displayed above the table.
    """
    clear_screen()
    header = f"Testing: {domain}\n"
    header += (
        f"{'No.':<4} | {'DNS Server':<18} | {'Result':<40} | "
        f"{'MIN (ms)':>10} | {'MAX (ms)':>10} | {'AVG (ms)':>10} | {'Latest (ms)':>12} | Graph"
    )
    print(header)
    print("-" * len(header))
    
    for idx, (ip, history) in enumerate(results_history.items(), start=1):
        successes = [lat for lat, _ in history if lat is not None]
        if successes:
            min_val = min(successes)
            max_val = max(successes)
            avg_val = sum(successes) / len(successes)
            latest_latency = history[-1][0]
            latest_result = history[-1][1]
            min_str = f"{min_val:10.2f}"
            max_str = f"{max_val:10.2f}"
            avg_str = f"{avg_val:10.2f}"
            latest_str = f"{latest_latency:12.2f}" if latest_latency is not None else f"{'Failed':>12}"
        else:
            min_str = max_str = avg_str = latest_str = f"{'N/A':>10}"
            latest_result = "N/A"
        
        # Truncate result text if too long.
        result_str = (latest_result[:37] + "...") if len(latest_result) > 40 else latest_result
        graph = compute_history_symbols(history)
        print(
            f"{idx:<4} | {ip:<18} | {result_str:<40} | "
            f"{min_str} | {max_str} | {avg_str} | {latest_str} | {graph}"
        )

def save_to_excel(excel_rows, servers):
    """
    Saves the collected log data into an Excel file with a line chart.
    The Excel file ("dns_benchmark.xlsx") will contain a worksheet "Data" with:
      - Headers: Iteration, Timestamp, then one column per DNS server.
      - One row per iteration with the latency measurements.
    A chart is inserted that plots latency over iterations for each DNS server.
    """
    try:
        import xlsxwriter
    except ImportError:
        print("xlsxwriter is not installed. Please install it via 'pip install xlsxwriter'")
        return

    workbook = xlsxwriter.Workbook("dns_benchmark.xlsx")
    worksheet = workbook.add_worksheet("Data")
    
    # Write header row.
    headers = ["Iteration", "Timestamp"] + servers
    for col, header in enumerate(headers):
        worksheet.write(0, col, header)
    
    # Write log data rows.
    for row_num, row in enumerate(excel_rows, start=1):
        for col, value in enumerate(row):
            worksheet.write(row_num, col, value)
    
    # Create a line chart.
    chart = workbook.add_chart({'type': 'line'})
    num_rows = len(excel_rows)
    
    # Each DNS server has its own series.
    for i, server in enumerate(servers):
        col = 2 + i  # columns start at index 2 for server data.
        chart.add_series({
            'name':       ['Data', 0, col],
            'categories': ['Data', 1, 0, num_rows, 0],
            'values':     ['Data', 1, col, num_rows, col],
        })
    chart.set_title({'name': 'DNS Benchmark Latency'})
    chart.set_x_axis({'name': 'Iteration'})
    chart.set_y_axis({'name': 'Latency (ms)'})
    
    # Insert the chart into the worksheet.
    worksheet.insert_chart('H2', chart)
    workbook.close()

def main():
    parser = argparse.ArgumentParser(
        description="Live DNS Benchmark: test DNS servers, display live trends, and optionally log to Excel."
    )
    parser.add_argument("--interval", "-i", type=float, default=5.0,
                        help="Time in seconds between tests (default: 5.0 seconds).")
    parser.add_argument("--iterations", "-n", type=int, default=0,
                        help="Number of iterations to run (0 for continuous until interrupted).")
    parser.add_argument("--domain", "-d", type=str, default="google.com",
                        help="FQDN to test on the DNS servers (default: google.com).")
    parser.add_argument("--save-excel", action="store_true",
                        help="If provided, log the output to dns_benchmark.xlsx with a chart.")
    args = parser.parse_args()
    interval = args.interval
    iterations = args.iterations
    domain = args.domain
    save_excel_flag = args.save_excel

    dns_servers = get_system_dns_servers()
    if not dns_servers:
        print("No valid system DNS servers found.")
        return

    # Initialize history for each server. Each history is a list of tuples: (latency, result)
    results_history = {ip: [] for ip in dns_servers}
    # For Excel logging, store rows: each row is [iteration, timestamp, latency_for_server1, latency_for_server2, ...]
    excel_rows = []
    servers_order = list(results_history.keys())

    print("System DNS servers found:")
    for ip in servers_order:
        print("  ", ip)
    print(f"\nBenchmarking DNS query for domain: {domain}")
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
            # Run tests concurrently for each DNS server.
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_to_ip = {
                    executor.submit(resolve_domain, ip, domain): ip for ip in servers_order
                }
                for future in concurrent.futures.as_completed(future_to_ip):
                    ip = future_to_ip[future]
                    try:
                        latency, result_text = future.result()
                    except Exception as exc:
                        latency, result_text = None, str(exc)
                    results_history[ip].append((latency, result_text))
            iteration_count += 1
            # Append a row for Excel logging: iteration, timestamp, then latency for each server.
            row = [iteration_count, timestamp_str]
            for ip in servers_order:
                # If no measurement available, write None.
                if results_history[ip]:
                    row.append(results_history[ip][-1][0])
                else:
                    row.append(None)
            excel_rows.append(row)
            
            update_display(results_history, domain)
            if save_excel_flag:
                save_to_excel(excel_rows, servers_order)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nBenchmarking interrupted by user.")
        update_display(results_history, domain)
        if save_excel_flag:
            save_to_excel(excel_rows, servers_order)

if __name__ == "__main__":
    main()
