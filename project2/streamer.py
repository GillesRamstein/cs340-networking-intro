from bisect import insort_left
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from socket import INADDR_ANY
from struct import pack, unpack
from threading import Lock
from time import sleep, time
from typing import Iterator

from lossy_socket import MAX_MSG_LENGTH, LossyUDP, _print

HASH_LENGTH = 16  # bytes
SEGMENT_ID_LENGTH = 4  # bytes
IS_ACK_LENGTH = 1  # bytes
IS_FIN_LENGTH = 1  # bytes

HEADER_LENGTH = HASH_LENGTH + SEGMENT_ID_LENGTH + IS_ACK_LENGTH + IS_FIN_LENGTH
BODY_LENGTH = MAX_MSG_LENGTH - HEADER_LENGTH

ACK_TIMEOUT = 0.75  # seconds

SEND_BUFF_SIZE = 1000
RECV_BUFF_SIZE = 1000


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
        self.name = name

        self.sock = LossyUDP()
        self.sock.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port

        self.send_segment_id = 0
        self.send_buffer = deque()
        self.send_lock = Lock()

        self.recv_segment_id = -1
        self.recv_buffer = deque()
        self.recv_lock = Lock()

        self.ack_segment_id = -1
        self.ack_buffer = deque()
        self.ack_lock = Lock()

        self.fin_received = False

        self.closed = False

        listener_thread = ThreadPoolExecutor(max_workers=1)
        listener_thread.submit(self._listener)
        sender_thread = ThreadPoolExecutor(max_workers=1)
        sender_thread.submit(self._sender)

    def send(
        self, data_bytes: bytes, ack_flag: bool = False, fin_flag: bool = False
    ) -> None:
        """
        Take input data_bytes, split into chunks if data_bytes does not fit into maximum
        packet size, create packets and put them into the send buffer.
        """
        for data_chunk in split_bytes(data_bytes, BODY_LENGTH):
            while len(self.send_buffer) + 1 >= SEND_BUFF_SIZE:
                # _print(f"{self.name}: SNDBUF Full", end="\r")
                sleep(0.01)

            with self.send_lock:
                segment_id = self.send_segment_id
                packet = build_packet(data_chunk, segment_id, ack_flag, fin_flag)
                insort_left(self.send_buffer, (0.0, segment_id, packet))
                self.send_segment_id += 1
                # _print(
                #     f"{self.name}: SNDBUF PUT({segment_id}) "
                #     f"-> {[p[:-1] for p in self.send_buffer]}",
                # )

            sleep(0.005)

    def _send_ack(self, data: bytes, segment_id: int):
        """
        Send an ACK message.
        """
        packet = build_packet(data, segment_id, ack_flag=True, fin_flag=False)
        self.sock.sendto(packet, (self.dst_ip, self.dst_port))
        _print(
            f"{self.name}: [ACK Sent] "
            f"SegId:{segment_id} "
            f"Bytes:{len(packet)} "
            f"Body:'{packet[HEADER_LENGTH:].decode()}'"
        )

    def _sender(self):
        """
        Handle sending packets from send_buffer,
        and trigger retransmissions when ACK times out.
        """
        _print(f"{self.name}: Started Sender\n", "-" * 50)
        while not self.closed:
            try:
                if not self.send_buffer:
                    sleep(0.01)
                    continue

                # get packet to send from buffer:
                # priority is first by send_time and then segment_id
                with self.send_lock:
                    send_time, segment_id, packet = self.send_buffer.popleft()

                    # packet was sent before and got already acknowledged: dont send again
                    with self.ack_lock:
                        if (
                            segment_id <= self.ack_segment_id
                            or segment_id in self.ack_buffer
                        ):
                            # do not put packet back into send_buffer
                            # _print(
                            #     f"{self.name}: [SNDBUF] POP({segment_id}) "
                            #     f"-> {[p[:-1] for p in self.send_buffer]}",
                            # )
                            sleep(0.005)
                            continue

                    # packet was not sent before: send it now
                    if send_time == 0.0:
                        self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                        # put packet back into send_buffer with updated send_time
                        insort_left(self.send_buffer, (time(), segment_id, packet))
                        _print(
                            f"{self.name}: [MSG Sent] [INITIAL TRANSMISSION] "
                            f"SegId:{segment_id} "
                            f"Bytes:{len(packet)} "
                            f"Body:'{packet[HEADER_LENGTH:].decode()[:25]}'"
                        )
                        sleep(0.005)
                        continue

                    # packet was sent before and ACK has timed out: resend it
                    if time() - send_time >= ACK_TIMEOUT:
                        self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                        # put packet back into send_buffer with updated send_time
                        insort_left(self.send_buffer, (time(), segment_id, packet))
                        _print(
                            f"{self.name}: [MSG Sent] [ACK TIMEOUT RETRANSMISSION] "
                            f"SegId:{segment_id} "
                            f"Bytes:{len(packet)} "
                            f"Body:'{packet[HEADER_LENGTH:].decode()[:25]}'"
                        )
                        sleep(0.005)
                        continue

                    # packet was sent and ACK has not timed out yet: put packet back into send_buffer
                    insort_left(self.send_buffer, (send_time, segment_id, packet))
                    sleep(0.005)

            except Exception as e:
                _print(f"{self.name}: Sender died!", e)
        _print(f"{self.name}: Stopped Sender")

    def recv(self) -> bytes:
        """
        Return available valid data from receive_buffer.
        """
        contiguous_data = b""

        while self.recv_buffer:
            with self.recv_lock:
                # test if lowest segment id packet is the next expected one
                segment_id, _, _, _, data = self.recv_buffer[0]
                if self.recv_segment_id + 1 == segment_id:
                    # if yes, append data and update recv_segment_id
                    contiguous_data += data
                    self.recv_segment_id = segment_id
                    # pop packet from buffer
                    _ = self.recv_buffer.popleft()
                    continue
                break
            sleep(0.001)

        return contiguous_data

    def _listener(self):
        """
        Handle incoming messages:
        - verify data integrity
        - send ACK messages
        - put non-ACK/FIN messages the into the receive buffer
        """
        _print(f"{self.name}: Started Listener")
        while not self.closed:
            try:
                # This call is blocking
                packet, _ = self.sock.recvfrom()

                if packet_is_corrupt(packet):
                    _print(f"{self.name}: Discarding corrupted packet")
                    continue

                segment_id, ack_flag, fin_flag, n_bytes, data = open_packet(packet)
                ddata = data.decode("utf-8")[:25]

                # Received MSG is an ACK
                if ack_flag:
                    _print(
                        f"{self.name}: [ACK Rcvd] "
                        f"SegId:{segment_id} "
                        f"Bytes:{n_bytes} "
                        f"Body:'{ddata}'"
                    )

                    # Update ack_buffer and ack_segment_id
                    with self.ack_lock:
                        ackd_segment_id = int(ddata.split(":")[-1])
                        if ackd_segment_id not in self.ack_buffer:
                            insort_left(self.ack_buffer, ackd_segment_id)
                        while self.ack_buffer:
                            if self.ack_segment_id + 1 == self.ack_buffer[0]:
                                self.ack_segment_id = self.ack_buffer.popleft()
                                continue
                            break
                    continue

                # Received MSG is a FIN
                if fin_flag:
                    _print(
                        f"{self.name}: [FIN Rcvd] "
                        f"SegId:{segment_id} "
                        f"Bytes:{n_bytes} "
                        f"Body:'{ddata}'"
                    )

                    # Send ACK for received FIN
                    self._send_ack(
                        f"ACK-FIN:{segment_id}".encode("utf-8"),
                        self.send_segment_id,
                    )

                    self.fin_received = True
                    continue

                # Received MSG is a retransmission: Resend ACK
                with self.recv_lock:
                    is_retransmission = (
                        segment_id <= self.recv_segment_id
                        or segment_id in [p[0] for p in self.recv_buffer]
                    )
                if is_retransmission:
                    _print(
                        f"{self.name}: [MSG Rcvd] [RETRANSMISSION] "
                        f"SegId:{segment_id} "
                        f"Bytes:{n_bytes} "
                        f"Body:'{ddata}'"
                    )
                    self._send_ack(
                        f"ACK-RTM:{segment_id}".encode("utf-8"),
                        self.send_segment_id,
                    )

                    # Do not add packet to receive_buffer
                    continue

                # Received MSG is a new one: Send ACK, put into recv_buffer
                _print(
                    f"{self.name}: [MSG Rcvd] [NEW MSG] "
                    f"SegId:{segment_id} "
                    f"Bytes:{n_bytes} "
                    f"Body:'{ddata}'"
                )
                self._send_ack(
                    f"ACK:{segment_id}".encode("utf-8"),
                    self.send_segment_id,
                )

                # Put packet into recv_buffer
                with self.recv_lock:
                    insort_left(
                        self.recv_buffer,
                        (segment_id, ack_flag, fin_flag, n_bytes, data),
                    )
                    # _print(
                    #     f"{self.name}: [RCVBUF] PUT({segment_id}) "
                    #     f"-> {[p[:-1] for p in self.recv_buffer]}",
                    # )

            except Exception as e:
                _print(f"{self.name}: Listener died!", e)
                # import traceback
                # with open(f"log-{time()}.txt", "w") as f:
                #     traceback._print_exc(file=f)

        _print(f"{self.name}: Stopped listener")

    def close(self) -> None:
        """
        Cleans up.
        It should block (wait) until the Streamer is done with all
        necessary ACKs and retransmissions
        """
        _print(f"{self.name}: init connection teardown")

        # wait for all messages to be sent
        while True:
            with self.send_lock:
                if not self.send_buffer:
                    break
            print(f"{self.name}: Waiting for empty SNDBUF", end="\r")
            sleep(0.01)
        _print(f"{self.name}: SNDBUF is empty           ")

        # wait for all messages to be ACKd
        while True:
            with self.send_lock, self.ack_lock:
                if self.ack_segment_id == self.send_segment_id - 1:
                    break
            print(f"{self.name}: Waiting for all ACKs", end="\r")
            sleep(0.01)
        _print(f"{self.name}: All MSGs got ACKd           ")

        # send FIN
        _print(f"{self.name}: SENDING FIN")
        self.send(b"FIN", ack_flag=False, fin_flag=True)

        # wait for FIN to be ACKd
        while True:
            with self.send_lock, self.ack_lock:
                if self.ack_segment_id == self.send_segment_id - 1:
                    break
            print(f"{self.name}: Waiting for FIN to be ACKd", end="\r")
            sleep(0.01)
        _print(f"{self.name}: RECEVIED FIN ACK                ")

        # wait 2 secs grace period
        _print(f"{self.name}: Closing connection in 2 seconds...")
        sleep(2)


        self.closed = True
        self.sock.stoprecv()


def split_bytes(data_bytes: bytes, chunk_size: int) -> Iterator[bytes]:
    """
    Split a bytes array into bytes arrays of size chunk_size.
    """
    for i in range(0, len(data_bytes), chunk_size):
        yield data_bytes[i : i + chunk_size]


def build_packet(data: bytes, segment_id: int, ack_flag: bool, fin_flag: bool):
    """
    Build a packet: Hash, Headers, Data.
    """
    header = pack(">I??", segment_id, ack_flag, fin_flag)
    packet = header + data
    packet_hash = md5(packet).digest()
    packet = packet_hash + packet
    return packet


def packet_is_corrupt(packet):
    """
    Check if packet hash is valid.
    """
    if md5(packet[HASH_LENGTH:]).digest() != packet[:HASH_LENGTH]:
        return True
    return False


def open_packet(packet):
    """
    Unpack packet: Headers, data.
    """
    n_bytes = len(packet)
    header = unpack(">I??", packet[HASH_LENGTH:HEADER_LENGTH])
    segment_id, is_ack, is_fin = header
    body = packet[HEADER_LENGTH:]
    return segment_id, is_ack, is_fin, n_bytes, body
