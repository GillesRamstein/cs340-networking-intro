# do not import anything else from loss_socket besides LossyUDP
# do not import anything else from socket except INADDR_ANY
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from socket import INADDR_ANY
from struct import pack, unpack
from time import sleep, time
from typing import Iterator

from lossy_socket import MAX_MSG_LENGTH, LossyUDP

# Headers:
#        md5: 16 bytes
# segment_id:  4 bytes
#     is_ack:  1 byte
#     is_fin:  1 byte
HASH_LENGTH = 16
HEADER_LENGTH = HASH_LENGTH + 4 + 1 + 1
BODY_LENGTH = MAX_MSG_LENGTH - HEADER_LENGTH


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
        self.name = name
        self.sock = LossyUDP()
        self.sock.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port
        self.send_segment_id = 0
        self.recv_segment_id = -1
        self.receive_buffer = dict()
        self.ack_received = False
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
            try:
                packet, _ = self.sock.recvfrom()
                # if packet == b"":
                #     print(f"{self.name}: discarding a message with an invalid header")
                #     continue

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
                    self.ack_received = True
                    # discard ACK message - dont put into receive_buffer

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
                        ack_body = f"{self.name}-ack-{self.recv_segment_id}"
                        self.send(ack_body.encode("utf-8"), is_ack=True)
                    else:
                        # received a new message -> add to receive_buffer
                        print(
                            f"{self.name}: [MSG Rcvd] "
                            f"SegId:{segment_id} "
                            f"Bytes:{len(packet)} "
                            f"Body:'{body.decode()}'"
                        )
                        self.receive_buffer[segment_id] = body

                print(f"{self.name}: recv-buff", self.receive_buffer)

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
                print(
                    f"{self.name}: [MSG Sent] "
                    f"SegId:{self.send_segment_id} "
                    f"Bytes:{len(packet)} "
                    f"Body:'{data_chunk.decode()}'"
                )
                # increment send segment id
                self.send_segment_id += 1

            # wait/block until message is ACKd
            ACK_TIMEOUT = 0.5  # seconds
            self.ack_received = False
            start = time()
            while not self.ack_received:
                print(f"{self.name}: waiting for ACK", end="\r")
                sleep(0.1)

                # resend message
                if time() - start >= ACK_TIMEOUT:
                    print(
                        f"{self.name}: ACK Timout: resending",
                        unpack(">I??", packet[HASH_LENGTH:HEADER_LENGTH]),
                        packet[HEADER_LENGTH:].decode(),
                    )
                    self.sock.sendto(packet, (self.dst_ip, self.dst_port))
                    start = time()


    def recv(self) -> bytes:
        """
        Blocks (waits) if no data is ready to be read from the connection
        """
        # return empty bytes if receive_buffer is empty
        if not self.receive_buffer:
            return b""

        recvd = []
        # find contiguous messages from next expected segment_id to
        # highest segment_id in recv_buffer
        for i in range(self.recv_segment_id + 1, max(self.receive_buffer) + 1):

            # skip if segment_id is not in receive_buffer
            if i not in self.receive_buffer:
                continue

            # stop if i is not expected next segment_id
            if i != self.recv_segment_id + 1:
                break

            # message is next expected: prepare to return
            self.recv_segment_id = i
            recvd.append(self.receive_buffer.pop(i))

            # send ACK for accepted message
            ack_body = f"ACK-{self.recv_segment_id}"
            self.send(ack_body.encode("utf-8"), is_ack=True)

        # return empty or accepted bytes
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
        print(f"{self.name}: init connection teardown")

        # wait for last message ACK
        while not self.ack_received:
            sleep(0.01)
            print(f"{self.name}: waiting for ACK before FIN", end="\r")

        # send FIN (needs to be ACKd)
        fin_body = "FIN"
        self.send(fin_body.encode("utf-8"), is_fin=True)

        # wait for other end FIN
        while not self.fin_received:
            sleep(0.01)
            print(f"{self.name}: waiting for FIN", end="\r")

        # close connection after grace period
        print(f"{self.name}: closing connection in 2 seconds...")
        sleep(2)
        self.closed = True
        self.sock.stoprecv()
