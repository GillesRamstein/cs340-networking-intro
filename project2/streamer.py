# do not import anything else from loss_socket besides LossyUDP
# do not import anything else from socket except INADDR_ANY
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from socket import INADDR_ANY
from struct import pack, unpack
from typing import Iterator

from lossy_socket import LossyUDP, MAX_MSG_LENGTH


HEADER_LENGTH = 4  # 32 bits = 4 bytes
BODY_LENGTH = MAX_MSG_LENGTH - HEADER_LENGTH


def split_bytes(data_bytes: bytes, chunk_size: int) -> Iterator[bytes]:
    for i in range(0, len(data_bytes), chunk_size):
        yield data_bytes[i: i+chunk_size]


class Streamer:
    def __init__(self, dst_ip, dst_port,
                 src_ip=INADDR_ANY, src_port=0):
        """
        Default values listen on all network interfaces, chooses a random source port,
        and does not introduce any simulated packet loss
        """
        self.socket = LossyUDP()
        self.socket.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port
        self.send_segment_id = 0
        self.recv_segment_id = -1
        self.receive_buffer = dict()

        self.closed = False
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(self.listener)

    def listener(self):
        """
        Receive data and feed receive_buffer in a background thread
        """
        while not self.closed:
            try:
                data, addr = self.socket.recvfrom()
                header = unpack(">I", data[:4])
                segment_id = header[0]
                body = data[4:]
                self.receive_buffer[segment_id] = body
            except Exception as e:
                print("listener died!\n", e)

    def send(self, data_bytes: bytes) -> None:
        """
        Note that data_bytes can be larger than one packet
        """
        for data_chunk in split_bytes(data_bytes, BODY_LENGTH):
            header = pack(">I", self.send_segment_id) # 32 bit unsigned integer
            message = header + data_chunk
            self.socket.sendto(message, (self.dst_ip, self.dst_port))
            self.send_segment_id += 1

    def recv(self) -> bytes:
        """
        Blocks (waits) if no data is ready to be read from the connection
        """
        _body = b""
        if not self.receive_buffer:
            return _body

        for i in range(self.recv_segment_id + 1, max(self.receive_buffer) + 1):
            if i not in self.receive_buffer:
                continue
            if i - 1 != self.recv_segment_id:
                break
            self.recv_segment_id = i
            _body += self.receive_buffer.pop(i)
        return _body

    def close(self) -> None:
        """
        Cleans up.
        It should block (wait) until the Streamer is done with all
        the necessary ACKs and retransmissions
        """
        self.closed = True
        self.socket.stoprecv()
