# do not import anything else from loss_socket besides LossyUDP
# do not import anything else from socket except INADDR_ANY
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
        """Default values listen on all network interfaces, chooses a random source port,
           and does not introduce any simulated packet loss."""
        self.socket = LossyUDP()
        self.socket.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port
        self.segment_id = 0
        self.recv_buff = dict()
        self.recv_segment_id = -1

    def send(self, data_bytes: bytes) -> None:
        """Note that data_bytes can be larger than one packet."""
        # Part1 Chunking: split data_bytes in smaller packets
        for data_chunk in split_bytes(data_bytes, BODY_LENGTH):
            # Part2 Reordering: Add header with segment id
            header = pack(">I", self.segment_id) # 32 bit unsigned integer
            message = header + data_chunk
            self.socket.sendto(message, (self.dst_ip, self.dst_port))
            self.segment_id += 1

    def recv(self) -> bytes:
        """Blocks (waits) if no data is ready to be read from the connection."""
        # Part2: reordering
        data, addr = self.socket.recvfrom()

        # separate headers and body from data
        header = unpack(">I", data[:4])
        segment_id = header[0]
        body = data[4:]

        # received packet is the expected next one
        if segment_id - 1 == self.recv_segment_id:
            self.recv_segment_id = segment_id
            return body

        # received packet is not expected next one
        self.recv_buff[segment_id] = body

        # check if received packet filled a hole in recv_buff and return
        # all valid packets or an empty byte string if no packet is valid.
        _body = b""
        for i in range(self.recv_segment_id + 1, max(self.recv_buff) + 1):
            if i not in self.recv_buff:
                continue
            if i - 1 != self.recv_segment_id:
                break
            self.recv_segment_id = i
            _body += self.recv_buff.pop(i)
        return _body

    def close(self) -> None:
        """Cleans up. It should block (wait) until the Streamer is done with all
           the necessary ACKs and retransmissions"""
        # your code goes here, especially after you add ACKs and retransmissions.
        pass
