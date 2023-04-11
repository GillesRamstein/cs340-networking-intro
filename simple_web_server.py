"""
https://stevetarzia.com/teaching/340/projects/project-1.html
Part 2: a simple web server
"""

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
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
        "Connection: Closed",
    ]
    if body:
        header.insert(-2, f"Content-Length: {len(body.encode())}")
    if mime:
        header.insert(-2, f"Content-type: {mime}")
    if body:
        return f"{header}\r\n\r\n{body}"
    return f"{header}\r\n\r\n"



def main(port):

    with socket(AF_INET, SOCK_STREAM) as accept_socket:
        """
        - 'localhost' or '127.0.0.1' -> only visible within same machine
        - '""' -> reachable by any address the machine happens to have
        - 'socket.gethostname()' -> visible to outside world
        """
        accept_socket.bind(("", port))  # bind socket to address:port
        accept_socket.listen(MAX_BACKLOG)  # queue up MAX_BACKLOG requests
        print_err("Server socket running:", accept_socket)

        while True:
            connection_socket, address = accept_socket.accept()
            print_err("New connection:", address)

            request_bytes = socket_receive(connection_socket)
            try:
                request = request_bytes.decode("utf-8")
            except:
                request = request_bytes.decode("ISO-8859-1")
            print_err("\nIncoming request:")
            print_err(request)

            if not request.startswith("GET"):
                print_err("Malformed request")
                connection_socket.close()
                continue

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

            response = http_response(code, mime, data)
            print_err("\nGenerated response:")
            print_err(response)

            socket_send(connection_socket, response)
            connection_socket.close()


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("port", type=int)
    args = parser.parse_args()

    main(args.port)
