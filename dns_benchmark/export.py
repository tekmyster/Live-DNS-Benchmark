"""Excel export that preserves missing samples and the previous file on failure."""

import os
from pathlib import Path
import re
import tempfile


def worksheet_names(domains):
    used = set()
    result = {}
    for domain in domains:
        base = re.sub(r"[\[\]:*?/\\]", "_", domain).strip("'") or "Domain"
        if base.casefold() == "history":
            base = "History_"
        name = base[:31]
        index = 2
        while name.casefold() in used:
            suffix = f"_{index}"
            name = base[:31 - len(suffix)] + suffix
            index += 1
        used.add(name.casefold())
        result[domain] = name
    return result


def write_excel(path, data, domains, servers, include_rtt=False):
    import xlsxwriter

    path = Path(path).resolve()
    names = worksheet_names(domains)
    handle, temporary = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(handle)
    try:
        with xlsxwriter.Workbook(temporary, {"strings_to_formulas": False, "strings_to_urls": False}) as workbook:
            for domain in domains:
                name = names[domain]
                sheet = workbook.add_worksheet(name)
                headers = ["Iteration", "Timestamp"]
                for server in servers:
                    headers.extend([f"{server} Latency (ms)", f"{server} IPs", f"{server} RTT (ms)"]
                                   if include_rtt else [f"{server} Latency (ms)"])
                sheet.write_row(0, 0, headers)
                sheet.freeze_panes(1, 2)
                sheet.set_column(1, 1, 26)
                rows = data[domain]
                for number, row in enumerate(rows, start=1):
                    sheet.write_row(number, 0, row)
                if not rows:
                    continue
                chart = workbook.add_chart({"type": "line"})
                for index, server in enumerate(servers):
                    col = 2 + index * (3 if include_rtt else 1)
                    chart.add_series({"name": server, "categories": [name, 1, 0, len(rows), 0],
                                      "values": [name, 1, col, len(rows), col]})
                chart.show_blanks_as("gap")
                chart.set_title({"name": f"DNS latency for {domain}"})
                chart.set_x_axis({"name": "Iteration"})
                chart.set_y_axis({"name": "Latency (ms)"})
                sheet.insert_chart(1, len(headers) + 1, chart)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
