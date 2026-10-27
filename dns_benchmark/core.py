"""DNS queries and statistics, independent of presentation and export."""

from dataclasses import dataclass
import ipaddress
import math
import time

import dns.exception
import dns.name
import dns.rdatatype
import dns.resolver


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("must be a finite number greater than zero")
    return number


def parse_lines(text):
    """Read comment-friendly lists, preserving order and removing duplicates."""
    return list(dict.fromkeys(
        line.split("#", 1)[0].strip()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ))


def validate_servers(servers):
    return list(dict.fromkeys(str(ipaddress.ip_address(server)) for server in servers))


def validate_domains(domains):
    result = []
    for domain in domains:
        if not domain or any(c.isspace() for c in domain) or "://" in domain or "/" in domain:
            raise ValueError(f"Invalid DNS name: {domain!r}; enter a name such as example.com")
        try:
            name = dns.name.from_text(domain)
            name.to_wire()  # Check DNS label and total wire-length limits.
        except (dns.exception.DNSException, UnicodeError) as exc:
            raise ValueError(f"Invalid DNS name: {domain!r}") from exc
        normalized = name.to_text(omit_final_dot=True).lower()
        if normalized not in result:
            result.append(normalized)
    return result


def system_dns_servers():
    """Use dnspython's native Windows/Unix resolver configuration support."""
    try:
        return validate_servers(dns.resolver.Resolver().nameservers)
    except (dns.exception.DNSException, OSError, ValueError):
        return []


@dataclass(frozen=True)
class Measurement:
    latency_ms: float | None
    status: str
    answer: str


def query(server, domain, record_type="A", timeout=2.0, tcp=False, port=53):
    """Measure an explicit absolute DNS query with no client cache or search suffix."""
    timeout = positive_float(timeout)
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = validate_servers([server])
    resolver.port = port
    resolver.timeout = timeout
    resolver.lifetime = timeout
    record_type = dns.rdatatype.from_text(record_type.upper())
    started = time.perf_counter()
    try:
        answer = resolver.resolve(domain, record_type, tcp=tcp, search=False)
        return Measurement((time.perf_counter() - started) * 1000, "OK",
                           ", ".join(str(record) for record in answer))
    except dns.resolver.NXDOMAIN as exc:
        return Measurement(None, "NXDOMAIN", str(exc))
    except dns.resolver.NoAnswer as exc:
        return Measurement(None, "NOANSWER", str(exc))
    except dns.exception.Timeout as exc:
        return Measurement(None, "TIMEOUT", str(exc))
    except (dns.exception.DNSException, OSError) as exc:
        return Measurement(None, "ERROR", str(exc))


def summarize(history):
    """Report successful-query timings alongside *all* attempts' failure rate."""
    values = sorted(sample.latency_ms for sample in history if sample.latency_ms is not None)
    attempts = len(history)
    failures = attempts - len(values)

    def percentile(fraction):
        if not values:
            return None
        position = (len(values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "attempts": attempts, "failures": failures,
        "failure_pct": failures * 100 / attempts if attempts else 0.0,
        "min_ms": values[0] if values else None,
        "max_ms": values[-1] if values else None,
        "avg_ms": sum(values) / len(values) if values else None,
        "median_ms": percentile(0.5), "p95_ms": percentile(0.95),
    }
