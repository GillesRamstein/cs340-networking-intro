# do not import anything else from loss_socket besides LossyUDP
# do not import anything else from socket except INADDR_ANY
from bisect import insort_left
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from socket import INADDR_ANY
from struct import pack, unpack
from time import sleep, time
from threading import Lock
from typing import Iterator

from lossy_socket import MAX_MSG_LENGTH, LossyUDP

HASH_LENGTH = 16  # bytes
SEGMENT_ID_LENGTH = 4  # bytes
IS_ACK_LENGTH = 1  # bytes
IS_FIN_LENGTH = 1  # bytes

HEADER_LENGTH = HASH_LENGTH + SEGMENT_ID_LENGTH + IS_ACK_LENGTH + IS_FIN_LENGTH
BODY_LENGTH = MAX_MSG_LENGTH - HEADER_LENGTH

ACK_TIMEOUT = 0.5  # seconds

RECV_WINDOW_SIZE = 10


def split_bytes(data_bytes: bytes, chunk_size: int) -> Iterator[bytes]:
    for i in range(0, len(data_bytes), chunk_size):
        yield data_bytes[i : i + chunk_size]


class Streamer:
    def __init__(
        self,
        name,
        dst_ip,
        dst_port,
        src_ip=INADDR_ANY,
        src_port=0,
    ):
        """
        Default values listen on all network interfaces, chooses a random
        source port, and does not introduce any simulated packet loss
        """
        self.lock = Lock()
        self.name = name
        self.sock = LossyUDP()
        self.sock.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port
        self.send_segment_id = 0
        self.send_buffer = dict()
        self.recv_segment_id = -1
        self.recv_buffer = dict()
        self.ack_segment_id = -1
        self.ack_buffer = deque()
        self.fin_received = False

        self.closed = False
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(self.listener)

    def listener(self):
        """
        Receive data and feed receive_buffer in a background thread
        """
        print(f"{self.name}: Start listening\n")
        while not self.closed:
            print(f"{self.name}: listening")
            try:
                packet, _ = self.sock.recvfrom()

                # check data integrity
                if md5(packet[HASH_LENGTH:]).digest() != packet[:HASH_LENGTH]:
                    print(f"{self.name}: discarding corrupted packet")
                    continue

                # unpack header
                header = unpack(">I??", packet[HASH_LENGTH:HEADER_LENGTH])
                segment_id, is_ack, is_fin = header

                # unpack body
                body = packet[HEADER_LENGTH:]

                if is_ack:
                    print(
                        f"{self.name}: [ACK Rcvd] "
                        f"SegId:{segment_id} "
                        f"Bytes:{len(packet)} "
                        f"Body:'{body.decode()}'"
                    )
                    # extract ack_seg_id from body
                    try:
                        ack_seg_id = int(body.decode("utf-8")[4:])
                    except ValueError:
                        if body.decode("utf-8") == "ACK-FIN":
                            print(f"{self.name}: FIN got ACKd!")
                            continue
                        else:
                            assert False, "Unreachable"

                    # ACK arrived in expected order
                    if self.ack_segment_id + 1 == ack_seg_id:
                        with self.lock:
                            self.ack_segment_id = ack_seg_id
                            # purge from send_buffer
                            self.send_buffer.pop(ack_seg_id)
                            # check if ACKs from buffer push frontier forward
                            while self.ack_buffer and self.ack_segment_id + 1 == self.ack_buffer[0]:
                                self.ack_segment_id = self.ack_buffer.popleft()
                                self.send_buffer.pop(self.ack_segment_id)

                    # ACK arrived out of order
                    else:
                        # TODO: this should never happen ?!
                        if ack_seg_id <= self.ack_segment_id:
                            print(f"{self.name}: Received ACK for retransmission {ack_seg_id}")
                        elif ack_seg_id > self.ack_segment_id + 1:
                            insort_left(self.ack_buffer, ack_seg_id)
                        else:
                            assert False, "Unreachable"

                elif is_fin:
                    print(
                        f"{self.name}: [FIN Rcvd] "
                        f"SegId:{segment_id} "
                        f"Bytes:{len(packet)} "
                        f"Body:'{body.decode()}'"
                    )
                    self.fin_received = True
                    # discard FIN message - dont put into receive_buffer
                    # # ACK FIN message
                    ack_body = "ACK-FIN"
                    self.send(ack_body.encode("utf-8"), is_ack=True)

                else:
                    if segment_id <= self.recv_segment_id:
                        # received a retransmission -> resend ACK
                        print(f"{self.name}: Resending ACK for received retransmission {segment_id}")
                        ack_body = f"ACK-{segment_id}"
                        self.send(ack_body.encode("utf-8"), is_ack=True)
                    else:
                        with self.lock:
                            # received a new message -> add to receive_buffer
                            _body = body.decode()
                            _body = _body if len(_body) < 50 else f"{_body[:15]}...{_body[-15:]}"
                            print(
                                f"{self.name}: [MSG Rcvd] "
                                f"SegId:{segment_id} "
                                f"Bytes:{len(packet)} "
                                f"Body:'{_body}'"
                            )
                            self.recv_buffer[segment_id] = body
                            # send ACK for accepted message
                            ack_body = f"ACK-{segment_id}"
                            self.send(ack_body.encode("utf-8"), is_ack=True)


                print(f"{self.name}: SND: {self.send_segment_id} {list(self.send_buffer.keys())}")
                print(f"{self.name}: ACK: {self.ack_segment_id} {list(self.ack_buffer)}")
                print(f"{self.name}: RCV: {self.recv_segment_id} {list(self.recv_buffer.keys())}")

            except Exception as e:
                print(f"{self.name}: listener died!\n", e)
                breakpoint()

        print(f"{self.name}: Stop listening")

    def send(self, data_bytes: bytes, is_ack: bool = False, is_fin: bool = False) -> None:
        """
        Note that data_bytes can be larger than one packet
        """

        # split message to fit into max packet size
        for data_chunk in split_bytes(data_bytes, BODY_LENGTH):

            # create header
            header = pack(">I??", self.send_segment_id, is_ack, is_fin)

            # assemble packet
            packet = header + data_chunk

            # prepend hash
            packet = md5(packet).digest() + packet

            # send packet
            self.sock.sendto(packet, (self.dst_ip, self.dst_port))

            if is_ack:
                print(
                    f"{self.name}: [ACK Sent] "
                    f"SegId:{self.send_segment_id} "
                    f"Bytes:{len(packet)} "
                    f"Body:'{data_chunk.decode()}'"
                )
                return

            if is_fin:
                print(
                    f"{self.name}: [FIN Sent] "
                    f"SegId:{self.send_segment_id} "
                    f"Bytes:{len(packet)} "
                    f"Body:'{data_chunk.decode()}'"
                )

            else:
                _body = data_chunk.decode()
                _body = _body if len(_body) < 50 else f"{_body[:15]}...{_body[-15:]}"
                print(
                    f"{self.name}: [MSG Sent] "
                    f"SegId:{self.send_segment_id} "
                    f"Bytes:{len(packet)} "
                    f"Body:'{_body}'"
                )

            # store message and ack_timer in send_buffer for possible retransmissions
            with self.lock:
                self.send_buffer[self.send_segment_id] = (packet, time())

            # Wait if max num messages are in flight
            while self.send_segment_id >= self.ack_segment_id + RECV_WINDOW_SIZE:
                print(f"{self.name}: waiting for ACK", self.ack_segment_id + 1, end="\r")
                sleep(0.1)

                with self.lock:
                    if (self.ack_segment_id + 1) not in self.send_buffer:
                        break

                    # check if oldest message got ACK before timeout, else resend message
                    if time() - self.send_buffer[self.ack_segment_id + 1][1] >= ACK_TIMEOUT:
                        lost_packet = self.send_buffer[self.ack_segment_id + 1][0]
                        print(
                            f"{self.name}: ACK Timout: resending: ",
                            unpack(">I??", lost_packet[HASH_LENGTH:HEADER_LENGTH])[0],
                        )
                        self.sock.sendto(lost_packet, (self.dst_ip, self.dst_port))
                        # reset ack_timer
                        self.send_buffer[self.ack_segment_id + 1] = (lost_packet, time())

            if not is_ack and not is_fin:
                # increment send segment id
                self.send_segment_id += 1


    def recv(self) -> bytes:
        """Return contiguous data from receive_buffer"""
        if not self.recv_buffer:
            return b""

        with self.lock:
            contiguous_data = b""
            while self.recv_segment_id + 1 in self.recv_buffer:
                self.recv_segment_id += 1
                contiguous_data += self.recv_buffer.pop(self.recv_segment_id)
            return contiguous_data


    def close(self) -> None:
        """Cleans up.
        It should block (wait) until the Streamer is done with all
        the necessary ACKs and retransmissions
        """
        print(f"{self.name}: init connection teardown")

        # Wait till all sent messages are ACKd
        while self.send_buffer:
            print(f"{self.name}: waiting for ACK", self.ack_segment_id + 1, end="\r")
            sleep(0.1)

            with self.lock:
                if (self.ack_segment_id + 1) not in self.send_buffer:
                    break

                # check if oldest message got ACK before timeout, else resend message
                if time() - self.send_buffer[self.ack_segment_id + 1][1] >= ACK_TIMEOUT:
                    lost_packet = self.send_buffer[self.ack_segment_id + 1][0]
                    print(
                        f"{self.name}: ACK Timout: resending ",
                        unpack(">I??", lost_packet[HASH_LENGTH:HEADER_LENGTH])[0],
                    )
                    self.sock.sendto(lost_packet, (self.dst_ip, self.dst_port))
                    # reset ack_timer
                    self.send_buffer[self.ack_segment_id + 1] = (lost_packet, time())

        # send FIN (needs to be ACKd)
        fin_body = "FIN"
        self.send(fin_body.encode("utf-8"), is_fin=True)

        # wait for other end FIN
        while not self.fin_received:
            sleep(0.1)
            print(f"{self.name}: waiting for FIN", end="\r")

        # close connection after grace period
        print(f"{self.name}: closing connection in 2 seconds...")
        sleep(2)
        self.closed = True
        self.sock.stoprecv()
