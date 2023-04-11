"""
https://stevetarzia.com/teaching/340/projects/project-1.html
Part 2: a simple web server
"""

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from select import select
from socket import socket, AF_INET, SOCK_STREAM

from simple_curl import socket_send, socket_receive, print_err

MAX_BACKLOG = 5
PHRASES = {
    200: "OK",
    301: "Moved Permanently",
    302: "Found",
    400: "Bad Request",
    401: "Not Authorized",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
}


def http_response(code, mime, body):
    header = [
        f"HTTP/1.0 {code} {PHRASES[code]}",
        f"Date: {datetime.now()}",
        f"Server: Skynet",
        "Connection: Uncertain",
    ]
    if body:
        header.insert(-2, f"Content-Length: {len(body.encode())}")
    if mime:
        header.insert(-2, f"Content-type: {mime}")
    msg = "\r\n".join(header) + "\r\n\r\n"
    if body:
        msg += body
    return msg


def main(port):

    with socket(AF_INET, SOCK_STREAM) as server_socket:
        """
        - 'localhost' or '127.0.0.1' -> only visible within same machine
        - '""' -> reachable by any address the machine happens to have
        - 'socket.gethostname()' -> visible to outside world
        """
        server_socket.bind(("", port))  # bind socket to address:port
        server_socket.listen(MAX_BACKLOG)  # queue up MAX_BACKLOG requests
        server_socket.setblocking(False)
        print_err("Server socket running:", server_socket)

        input_sockets = [server_socket]
        output_sockets = []
        messages = {}

        while True:
            # let OS check which socket has data or is ready for data
            read_sockets, write_sockets, error_sockets = select(
                input_sockets, output_sockets, input_sockets, 0.5
            )

            for sock in read_sockets:
                if sock is server_socket:
                    # Create new client connection
                    client_socket, address = server_socket.accept()
                    client_socket.setblocking(False)
                    print_err("New connection:", address)
                    input_sockets.append(client_socket)
                    messages[client_socket] = b""
                else:
                    # append new data
                    if request_bytes := sock.recv(1024):
                        print_err("Reading data from:", sock)
                        print_err(request_bytes)
                        messages[sock] += request_bytes

                    # ignore bad or not GET requests
                    try:
                        is_get = request_bytes[:5].decode("utf-8").startswith("GET")
                    except:
                        is_get = request_bytes[:5].decode("ISO-8859-1").startswith("GET")
                    if not request_bytes or not is_get:
                        print_err("Malformed request, closing:", sock)
                        if sock in output_sockets:
                            output_sockets.remove(sock)
                        input_sockets.remove(sock)
                        sock.close()
                        continue

                    # assume all data is send: create response
                    if len(request_bytes) < 1024:
                        input_sockets.remove(sock)
                        output_sockets.append(sock)

                        print_err("Creating Response")
                        try:
                            request = messages[sock].decode("utf-8")
                        except:
                            request = messages[sock].decode("ISO-8859-1")

                        uri = request.split(" ")[1]
                        p = Path(uri[1:])
                        print_err("Resource Path:", p)
                        # serve html
                        if p.is_file() and uri.endswith(".html"):
                            mime = "text/html"
                            code = 200
                            with open(p, "r") as f:
                                data = f.read()

                        # serve htm
                        elif p.is_file() and uri.endswith(".htm"):
                            mime = "text/htm"
                            code = 200
                            with open(p, "r") as f:
                                data = f.read()

                        # 403 Forbidden
                        elif p.is_file():
                            mime = None
                            code = 403
                            data = None

                        # 404 Not found
                        else:
                            mime = None
                            code = 404
                            data = None

                        response = http_response(code, mime, data).encode()
                        print_err("\nGenerated response:", len(response), "bytes")
                        messages[sock] = response

            for sock in write_sockets:
                print_err("Sending data to:", sock)
                sent_bytes = sock.send(messages[sock][:1024])
                print_err(sent_bytes)
                if sent_bytes >= len(messages[sock]):
                    print_err("Complete Message Sent")
                    print_err("Closing:", sock)
                    output_sockets.remove(sock)
                    messages.pop(sock)
                    sock.close()
                else:
                    messages[sock] = messages[sock][1024:]

            for sock in error_sockets:
                print_err("Socket Error:", sock)
                input_sockets.remove(sock)
                if sock in output_sockets:
                    output_sockets.remove(sock)
                sock.close()
                messages.pop(sock)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("port", type=int)
    args = parser.parse_args()

    main(args.port)
