"""
https://stevetarzia.com/teaching/340/projects/project-1.html
Part 1: a simple curl clone
"""

import re
import sys
from argparse import ArgumentParser
from socket import socket, AF_INET, SOCK_STREAM
from time import time

HTTP_PORT = 80
HTTPS_PORT = 443


def print_err(*a):
    print(*a, file=sys.stderr)

def parse_address(address):
    port = None

    # default ports
    if address.startswith("http://"):
        address = address.replace("http://", "")
        port = HTTP_PORT
    if address.startswith("https://"):
        address = address.replace("https://", "")
        port = HTTPS_PORT

    # port specified in address
    if match:=re.search(r":\d{1,5}", address):
        port = int(match.group()[1:])
        address = address.replace(match.group(), "")

    if port == 443:
        print_err("Exiting: https is not supported")
        sys.exit(1)
    if port is None:
        print_err("Exiting: could not determine port")
        sys.exit(1)

    if "/" in address:
        host, page = address.split("/", 1)
    else:
        host = address
        page = ""
    return host, port, "/"+page


def http_get_header(host, page):
    return (
        f"GET {page} HTTP/1.0"
        "\r\n"
        f"Host: {host}"
        "\r\n\r\n"
    )


def http_get(url, port, page):
    header = http_get_header(url, page)
    print_err("Request Header:")
    print_err("-----------------------")
    print_err(f"{header}")
    print_err("-----------------------")

    with socket(AF_INET, SOCK_STREAM) as s:
        s.connect((url, port))
        socket_send(s, header)
        s.shutdown(1)
        response = socket_receive(s)

    try:
        response_header, response_body = response.decode("utf-8").split("\r\n\r\n")
    except UnicodeDecodeError:
        response_header, response_body = response.decode("ISO-8859-1").split("\r\n\r\n")

    print_err("\nResponse Header:")
    print_err("-----------------------")
    print_err(f"{response_header}")
    print_err("-----------------------")

    print_err(f"\nResponse Body: ({len(response_body)} characters)")
    print_err("-----------------------")
    if len(response_body) > 250:
        print_err(f"{response_body[:100]}")
        print_err("...")
        print_err(f"{response_body[-100:]}")
    else:
        print_err(f"{response_body}")
    print_err("-----------------------")

    code = response_header.split(" ")[1]
    print_err(f"\nCode: {code}")
    return int(code), response_header, response_body


def socket_send(s, payload):
    print_err("\nSending data...")
    totalsent = 0
    cnt = 0
    while totalsent < len(payload):
        sent = s.send(payload.encode()[totalsent:])
        if sent == 0:
            break
        print_err(f" > chunk-{cnt}: {sent} bytes:")
        totalsent += sent
        cnt += 1
    print_err("Sent", totalsent, "of", len(payload.encode()), "bytes")


def socket_receive(s):
    print_err("\nReceiving data...")
    chunks = []
    cnt = 0
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        print_err(f" > chunk-{cnt}: {len(chunk)} bytes:")
        chunks.append(chunk)
        cnt += 1
    response = b"".join(chunks)
    print_err(f"Received {len(response)} bytes")
    return response


def main(address):
    host, port, page = parse_address(address)
    code, header, body = http_get(host, port, page)

    MAX_REDIRECTS = 10
    num_redirects = 0
    while code in {301, 302}:
        if num_redirects > MAX_REDIRECTS:
            print_err("Exiting: redirect limit (10)")
            sys.exit(1)
        num_redirects += 1
        header_lines = header.split("\r\n")
        location_line = [l for l in header_lines if "Location:" in l][0]
        redirect_address = location_line.replace("Location: ", "")
        print_err(f"\nRedirection:\n{address} -> {redirect_address}\n")
        address = redirect_address
        host, port, page = parse_address(address)
        code, header, body = http_get(host, port, page)

    if code == 200:
        print(body)
        sys.exit(0)

    if code >= 400:
        print(body)
        sys.exit(1)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("address", type=str)
    args = parser.parse_args()

    main(args.address)
