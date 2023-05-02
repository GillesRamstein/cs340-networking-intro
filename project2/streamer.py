from bisect import insort_left
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from queue import Queue
from socket import INADDR_ANY
from struct import pack, unpack
from time import sleep, time
from typing import Iterator

from lossy_socket import MAX_MSG_LENGTH, LossyUDP, _print

HASH_LENGTH = 16  # bytes
SEGMENT_ID_LENGTH = 4  # bytes
IS_ACK_LENGTH = 1  # bytes
IS_FIN_LENGTH = 1  # bytes

HEADER_LENGTH = HASH_LENGTH + SEGMENT_ID_LENGTH + IS_ACK_LENGTH + IS_FIN_LENGTH
BODY_LENGTH = MAX_MSG_LENGTH - HEADER_LENGTH

ACK_TIMEOUT = 0.5  # seconds

MAX_IN_FLIGHT_PACKETS = 20  # Limits send queue only


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
        # just for logging
        self.name = name

        # socket settings
        self.sock = LossyUDP()
        self.sock.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port

        # inter thread communication
        self.rcv_queue = Queue()  # listener thread -> main thead (recv)
        self.snd_queue = Queue()  # main thread (send) -> sender thread
        self.ack_queue = Queue()  # listener thread -> sender thread
        self.fin_queue = Queue()  # sender thread -> main thread (close)

        # global message buffer
        self.rcv_buff = deque()

        # global segment ids
        self.seg_id_next_pkt_sent = 0  # segment id of next packet to be sent
        self.seg_id_next_outp = 0  # segment id of next packet to be returned
        self.seg_id_ackd_until = -1  # up until this segment id all packets are ACKd

        # global flags
        self.fin_received = False
        self.closed = False

        # start worker threads
        listener_thread = ThreadPoolExecutor(max_workers=1)
        listener_thread.submit(self._listener)
        sender_thread = ThreadPoolExecutor(max_workers=1)
        sender_thread.submit(self._sender)

    def send(self, data_bytes: bytes, fin_flag: bool = False) -> None:
        """
        Chunk and pack input data_bytes into packets and make them available
        to the sender via the snd_queue.
        """
        while self.snd_queue.qsize() >= MAX_IN_FLIGHT_PACKETS:
            print("SND QUE FULL")
            sleep(0.1)

        if data_bytes == b"":
            segment_id = self.seg_id_next_pkt_sent
            packet = build_packet(b"", segment_id, ack_flag=False, fin_flag=fin_flag)
            self.snd_queue.put((0.0, segment_id, packet))
            self.seg_id_next_pkt_sent += 1
            # _print(
            #     f"{self.name}: SND-QUE PUT({segment_id}) len:{self.snd_queue.qsize()}"
            # )
        else:
            for msg_body in split_bytes(data_bytes, BODY_LENGTH):
                segment_id = self.seg_id_next_pkt_sent
                packet = build_packet(
                    msg_body, segment_id, ack_flag=False, fin_flag=fin_flag
                )
                self.snd_queue.put((0.0, segment_id, packet))
                self.seg_id_next_pkt_sent += 1
                # _print(
                #     f"{self.name}: SND-QUE PUT({segment_id}) len:{self.snd_queue.qsize()}"
                # )

    def _sender(self):
        """
        Handle sending packets.
        Pops MSGs from the snd_queue into snd_buff, sends MSGs, keeps track
        of their ACK timeouts and retransmitts MSGs if necessary.
        Gets latests ACKs from listener via ack_queue and removes ACKd messages
        from snd_buff accordingly.
        Messages the segment id up until which all segments are ACKd to the
        close function via fin_queue.
        """
        _print(f"{self.name}: Started Sender\n", "-" * 50)
        snd_buff = deque()
        ack_buff = deque()
        ack_seg_id = -1
        prev_ack_seg_id = -2

        while not self.closed:
            try:
                # loop until connection gets closed
                sleep(0.01)

                # check if there's a new packet to be sent
                if not self.snd_queue.empty():
                    # pop packet from send queue
                    _, seg_id, packet = self.snd_queue.get()
                    # _print(
                    #     f"{self.name}: SND-QUE POP({seg_id}) len:{self.snd_queue.qsize()}"
                    # )
                    # initial transmission of packet
                    self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                    # _print(
                    #     f"{self.name}: [SND MSG] ({seg_id}) "
                    # )
                    # put into send buffer
                    insort_left(snd_buff, (time(), seg_id, packet))
                    # _print(
                    #     f"{self.name}: SND-BUF PUT({seg_id}) len:{len(snd_buff)}"
                    # )

                if not snd_buff:
                    # if ack_id changed, message it to close()
                    if ack_seg_id >= 0 and ack_seg_id != prev_ack_seg_id:
                        # only ever keep 1 message in this queue
                        if not self.fin_queue.empty():
                            _ = self.fin_queue.get()
                        self.fin_queue.put(ack_seg_id)
                    continue

                # get oldest packet in send buffer
                send_time, seg_id, packet = snd_buff[0]

                # get lastest ACKs from listener
                while not self.ack_queue.empty():
                    _ack_seg_id = self.ack_queue.get()
                    insort_left(ack_buff, _ack_seg_id)
                    # _print(
                    #     f"{self.name}: ACK-QUE POP({_ack_seg_id}) len:{self.ack_queue.qsize()}"
                    # )
                    # update ack buffer and id
                    while len(ack_buff):
                        _seg_id = ack_buff[0]
                        if _seg_id == ack_seg_id + 1:
                            ack_seg_id = _seg_id
                            _ = ack_buff.popleft()
                        else:
                            break

                # packet got ACK
                if seg_id <= ack_seg_id or seg_id in ack_buff:
                    # remove packet from send buffer
                    snd_buff.popleft()
                    # _print(
                    #     f"{self.name}: SND-BUF POP({seg_id}) len:{len(snd_buff)}"
                    # )
                    continue

                # packet ACK timed out
                if time() - send_time >= ACK_TIMEOUT:
                    # retransmit packet
                    self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                    # update send time in send buffer
                    snd_buff.popleft()
                    insort_left(snd_buff, (time(), seg_id, packet))
                    # _print(
                    #     f"{self.name}: [SND-MSG-RTM] ({seg_id}) "
                    # )
                    continue

            except Exception as e:
                _print(f"{self.name}: Sender died!", e)
        _print(f"{self.name}: Stopped Sender")

    def recv(self) -> bytes:
        """
        Output received messages.
        Pop MSGs from queue into buffer. Return contiguous messages from the buffer.
        """
        # pop some new messages from listener into receive buffer
        max_msgs_per_call = 5
        cnt_msgs_per_call = 0
        while not self.rcv_queue.empty():
            seg_id, _, _, body = self.rcv_queue.get()
            # _print(
            #     f"{self.name}: RCV-QUE POP({seg_id}) len:{self.rcv_queue.qsize()}"
            # )
            if (seg_id, body) in self.rcv_buff or seg_id < self.seg_id_next_outp:
                continue
            if cnt_msgs_per_call >= max_msgs_per_call:
                break
            cnt_msgs_per_call += 1
            insort_left(self.rcv_buff, (seg_id, body))
            # _print(
            #     f"{self.name}: RCV-BUF PUT({seg_id}) len:{len(self.rcv_buff)}"
            # )

        # return contiguous data to application
        str_out = b""
        while self.rcv_buff:
            seg_id, packet = self.rcv_buff[0]
            if seg_id == self.seg_id_next_outp:
                seg_id, body = self.rcv_buff.popleft()
                self.seg_id_next_outp += 1  # len(body)
                str_out += body
                # _print(
                #     f"{self.name}: RCV-BUF POP({seg_id}) len:{len(self.rcv_buff)}"
                # )
            else:
                break
        return str_out

    def _listener(self):
        """
        Handle incoming packets.
        Put ACKs and MSGs into their respective queues.
        Send ACKs for received MSGs.
        """
        _print(f"{self.name}: Started listener")
        while not self.closed:
            try:
                # get packet
                packet, _ = self.sock.recvfrom()

                # check hash / data integrity
                if packet_is_corrupt(packet):
                    _print(f"{self.name}: Discarding corrupted packet")
                    continue

                # unpack packet
                seg_id, is_ack, is_fin, n_bytes, body = open_packet(packet)

                if is_ack:
                    # unpack ACKd segment id
                    ackd_seg_id = int(body.decode().split(":")[-1])
                    # send ACKd segment id to sender thread
                    self.ack_queue.put(ackd_seg_id)
                    # _print(
                    #     f"{self.name}: [ACK-QUE] PUT({ackd_seg_id}) "
                    # )
                else:
                    # send packet to main thread
                    self.rcv_queue.put((seg_id, is_ack, is_fin, body))
                    # _print(
                    #     f"{self.name}: [RCV-QUE] PUT({seg_id}) "
                    # )
                    # send ACK
                    packet = build_packet(
                        f"ACK:{seg_id}".encode(),
                        self.seg_id_next_pkt_sent,  # (segment_id of ACKs are not unique)
                        ack_flag=True,
                        fin_flag=False,
                    )
                    self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                    # _print(
                    #     f"{self.name}: [SND-ACK] ({seg_id}) "
                    # )
                    # set fin received flag
                    if is_fin:
                        self.fin_received = True

            except Exception as e:
                _print(f"{self.name}: Listener died!", e)

        _print(f"{self.name}: Stopped listener")

    def close(self) -> None:
        """
        Close connection in orderly manner.
        """
        _print(f"{self.name}: init connection teardown")

        # wait for all messages to be ACKd
        while self.seg_id_ackd_until < self.seg_id_next_pkt_sent - 1:
            if not self.fin_queue.empty():
                self.seg_id_ackd_until = self.fin_queue.get()
            sleep(0.01)
        _print(f"{self.name}: All MSGs got ACKd")

        # send FIN
        _print(f"{self.name}: SENDING FIN ({self.seg_id_next_pkt_sent})")
        self.send(b"", fin_flag=True)

        # wait for FIN to be ACKd
        while self.seg_id_ackd_until < self.seg_id_next_pkt_sent - 1:
            if not self.fin_queue.empty():
                self.seg_id_ackd_until = self.fin_queue.get()
            sleep(0.01)
        _print(f"{self.name}: FIN got ACKd")

        # wait for FIN
        while not self.fin_received:
            sleep(0.1)
        _print(f"{self.name}: FIN RECEVIED")

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
