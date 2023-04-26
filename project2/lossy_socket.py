import random
from socket import AF_INET, SOCK_DGRAM, socket, timeout
from threading import Lock, Timer
from time import sleep, time
from datetime import datetime
from typing import Tuple

# constant seed makes the random number generator deterministic during testing
random.seed(0)
# random.seed(398120)

MAX_MSG_LENGTH=1472

def _print(*args, **kwargs):
    print(f"[{datetime.now().strftime('%H:%M:%S:%f')}]", *args, **kwargs)


class SimulationParams:
    def __init__(self, loss_rate: float=0.0, corruption_rate: float=0.0,
                 max_delivery_delay: float=0.0, become_reliable_after: float=100000.0):
        self.start_time = time()
        self.loss_rate = loss_rate
        self.corruption_rate = corruption_rate
        self.max_delivery_delay = max_delivery_delay
        self.become_reliable_after = become_reliable_after

    def forced_reliable(self) -> bool:
        return time() - self.start_time > self.become_reliable_after

class SimulationStats:
    def __init__(self):
        self.packets_sent = 0
        self.packets_recv = 0
        self.bytes_sent = 0
        self.bytes_recv = 0
        self.lock = Lock()

    def __del__(self):
        # print some stats
        print("PACKETS_SENT=%d" % self.packets_sent)
        print("UDP_BYTES_SENT=%d" % self.bytes_sent)
        print("ETH_BYTES_SENT=%d" % (self.bytes_sent + (18+20+8) * self.packets_sent))
        print("PACKETS_RECV=%d" % self.packets_recv)
        print("UDP_BYTES_RECV=%d" % self.bytes_recv)
        print("ETH_BYTES_RECV=%d" % (self.bytes_recv + (18+20+8) * self.packets_recv))


# global simulation parameters
sim = SimulationParams()
stats = SimulationStats()


HEADER_LENGTH = 22

class LossyUDP(socket):
    def __init__(self):
        self.stopped = False
        super().__init__(AF_INET, SOCK_DGRAM)
        # make calls to socket.recvfrom timeout after one second, so that self.stopped is checked
        self.settimeout(1)

    def __del__(self):
        # for our purposes, we always want to unbind the port when the app stops
        super().close()

    def _super_sendto(self, msg, dst, msg_header):
        # _print("TEST-SIM: Sending Now:", msg_header)
        return lambda: super(self.__class__, self).sendto(msg, dst)

    def sendto(self, message: bytes, dst: Tuple[str, int]):
        """Unlike the sendto method provided by the BSD socket lib,
           this method never blocks (because it schedules the transmission on a thread)."""
        start = time()
        msg_header = message[HEADER_LENGTH:].decode()[:50]

        if len(message) > MAX_MSG_LENGTH:
            raise RuntimeError(f"You are trying to send more than {MAX_MSG_LENGTH} bytes in one UDP packet!")
        header_time = round(time() - start, 3)

        stats_start = time()
        with stats.lock:
            stats.packets_sent += 1
            stats.bytes_sent += len(message)
        stats_time = round(time() - stats_start, 3)

        # sleep() spaces out the requests enough to eliminate reordering caused by the OS process/thread scheduler.
        # It also limits the peak throughput of the socket :(
        sleep(0.01)

        if random.random() < sim.loss_rate and not sim.forced_reliable():
            # drop the packet
            _print("TEST-SIM: Outgoing UDP packet was dropped by the simulator.", msg_header)
            return

        noise_start = time()
        if not sim.forced_reliable():
            # flip an arbitrary number of bits in the packet:
            bits_flipped = 0
            for bit_to_flip in range(len(message) * 8):
                # probability of corrupting the packet in at least one bit is ~ sim.corruption_rate
                if random.random() < sim.corruption_rate / (len(message) * 8):
                    bits_flipped += 1
                    # corrupt the packet
                    byte_to_be_flipped = message[int(bit_to_flip / 8)]
                    flipped_byte = byte_to_be_flipped ^ (1 << (bit_to_flip % 8))
                    # bytes type is not mutable, but bytearray is:
                    msg_array = bytearray(message)
                    msg_array[int(bit_to_flip / 8)] = flipped_byte
                    message = bytes(msg_array)
                    _print("TEST-SIM: Outgoing UDP packet's bit number %d was flipped by the simulator."
                          % bit_to_flip, msg_header)
            if bits_flipped > 0:
                _print("TEST-SIM: Total of %d bits flipped in the packet" % bits_flipped, msg_header)
        noise_time = round(time() - noise_start, 3)

        timer_start = time()
        # send message after a random delay.  The randomness will reorder packets
        interval = 0 if sim.forced_reliable() else random.random() * sim.max_delivery_delay
        # _print("TEST: Dispatching Timer with delay", round(interval, 3), msg_header)
        Timer(interval, self._super_sendto(message, dst, msg_header)).start()
        # Timer(interval, lambda: super(self.__class__, self).sendto(message, dst)).start()
        timer_time = round(time() - timer_start, 3)
        # _print("                          sock.sendto()", round(time()-start, 3), stats_time, noise_time, timer_time, header_time)

    def recvfrom(self, bufsize: int=2048) -> (bytes, (str, int)):
        """Blocks until a packet is received or self.stoprecv() is called.
           returns (data, (source_ip, source_port))"""
        while not self.stopped:
            try:
                # wait for a packet, but timeout after one second
                data, addr = super().recvfrom(bufsize)
                with stats.lock:
                    stats.packets_recv += 1
                    stats.bytes_recv += len(data)
            except InterruptedError:
                # note that on Python >= 3.5, this exception will not happen:
                # https://www.python.org/dev/peps/pep-0475/
                continue
            except timeout:
                continue
            else:
                return data, addr
        return b'', ("", 0)

    def stoprecv(self) -> None:
        self.stopped = True
