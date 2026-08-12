#!/usr/bin/env python3
"""Resolve a single .local mDNS hostname to an IPv4 address.

Sends one mDNS A-record query to 224.0.0.251:5353 and parses the answer
section of replies for a matching A record, retrying a few times since mDNS
is UDP/best-effort. Prints the IP and exits 0 on success, exits 1 otherwise.
"""
import socket
import struct
import sys
import time


def encode_name(name):
    out = bytearray()
    for part in name.split("."):
        out += bytes([len(part)]) + part.encode()
    out += b"\x00"
    return bytes(out)


def parse_name(data, offset):
    labels = []
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            labels.append(parse_name(data, pointer)[0])
            offset += 2
            break
        offset += 1
        labels.append(data[offset:offset + length].decode(errors="replace"))
        offset += length
    return ".".join(labels), offset


def extract_a_record(data, hostname):
    qdcount, ancount = struct.unpack_from(">HH", data, 4)
    offset = 12
    for _ in range(qdcount):
        _, offset = parse_name(data, offset)
        offset += 4  # qtype + qclass
    for _ in range(ancount):
        name, offset = parse_name(data, offset)
        rtype, _rclass, _ttl, rdlength = struct.unpack_from(">HHIH", data, offset)
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        if rtype == 1 and rdlength == 4 and name.rstrip(".").lower() == hostname.rstrip(".").lower():
            return ".".join(str(b) for b in rdata)
    return None


def resolve(hostname, timeout=3.0, attempts=3):
    query = bytearray(12)
    struct.pack_into(">H", query, 4, 1)  # 1 question
    query += encode_name(hostname)
    query += struct.pack(">HH", 1, 1)  # type A, class IN

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 1))
    sock.settimeout(timeout)

    for _ in range(attempts):
        sock.sendto(bytes(query), ("224.0.0.251", 5353))
        deadline = time.time() + timeout
        while time.time() < deadline:
            sock.settimeout(max(deadline - time.time(), 0.01))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            ip = extract_a_record(data, hostname)
            if ip:
                return ip
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: mdns_resolve.py <hostname.local>", file=sys.stderr)
        sys.exit(2)
    result = resolve(sys.argv[1])
    if result:
        print(result)
        sys.exit(0)
    sys.exit(1)
