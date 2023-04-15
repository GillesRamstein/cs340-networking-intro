# do not import anything else from loss_socket besides LossyUDP
# do not import anything else from socket except INADDR_ANY
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from socket import INADDR_ANY
from struct import pack, unpack
from typing import Iterator

from lossy_socket import LossyUDP, MAX_MSG_LENGTH


# Headers:
# segment_id:  4 bytes
#     is_ack:  1 byte
HEADER_LENGTH = 5
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
        self.ack_received = True  # First message shall not wait for an ACK

        self.closed = False
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(self.listener)

    def listener(self):
        """
        Receive data and feed receive_buffer in a background thread
        """
        print("Start listening")
        while not self.closed:
            try:
                data, _ = self.socket.recvfrom()
                header = unpack(">I?", data[:5])
                segment_id, is_ack = header
                if is_ack:
                    self.ack_received = True
                body = data[5:]

                if is_ack:
                    print(f"ACK Recvd SegId:{segment_id} Bytes:{len(data)} Body:'{body.decode()}'")
                    # discard ACK message - dont put into receive_buffer
                else:
                    print(f"MSG Recvd SegId:{segment_id} Bytes:{len(data)} Body:'{body.decode()}'")
                    self.receive_buffer[segment_id] = body

                # # limit buffer size
                # MAX_RECV_BUF_SIZE = 5
                # max_segment_id = segment_id - MAX_RECV_BUF_SIZE
                # if max_segment_id in self.receive_buffer:
                #     discarded = self.receive_buffer.pop(max_segment_id)
                #     print("RecvBufLimit: Dropped:", max_segment_id, discarded)

                print("recv-buff", self.receive_buffer)

            except Exception as e:
                print("listener died!\n", e)
        print("Stop listening")

    def send(self, data_bytes: bytes, is_ack: bool = False) -> None:
        """
        Note that data_bytes can be larger than one packet
        """
        for data_chunk in split_bytes(data_bytes, BODY_LENGTH):
            header = pack(">I?", self.send_segment_id, is_ack) # 32 bit unsigned integer
            message = header + data_chunk

            while not self.ack_received:
                print("waiting for ACK", end="\r")
                sleep(0.01)

            self.socket.sendto(message, (self.dst_ip, self.dst_port))

            if is_ack:
                print(f"ACK Sent SegId:{self.send_segment_id} Bytes:{len(message)} Body:'{data_chunk.decode()}'")
                # do not change ack_received
            else:
                print(f"MSG Sent SegId:{self.send_segment_id} Bytes:{len(message)} Body:'{data_chunk.decode()}'")
                # wait for next ACK again
                self.ack_received = False
                self.send_segment_id += 1



    def recv(self) -> bytes:
        """
        Blocks (waits) if no data is ready to be read from the connection
        """
        if not self.receive_buffer:
            return b""

        recvd = []
        # find contiguous messages from next expected segment_id to highest segment_id in recv_buffer
        for i in range(self.recv_segment_id + 1, max(self.receive_buffer) + 1):
            if i not in self.receive_buffer:
                # skip if segment_id is not in receive_buffer
                continue
            if i != self.recv_segment_id + 1:
                # stop if i is not expected next segment_id
                break
            self.recv_segment_id = i
            recvd.append(self.receive_buffer.pop(i))
            # send ACK
            ack_body = f"ack-{self.recv_segment_id}"
            self.send(ack_body.encode("utf-8"), is_ack=True)
        if recvd:
            return b" ".join(recvd)
        else:
            return b""

    def close(self) -> None:
        """
        Cleans up.
        It should block (wait) until the Streamer is done with all
        the necessary ACKs and retransmissions
        """
        self.closed = True
        self.socket.stoprecv()
