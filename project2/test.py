import sys

import lossy_socket
from lossy_socket import _print
from streamer import Streamer


NUMS_1 = 125
NUMS_2 = 1000


def receive(s, n):
    expected = 0
    str_buf = ""
    while expected < n:
        data = s.recv()
        if not data:
            continue


        str_buf += data.decode('utf-8')
        tokens = str_buf.split(" ")
        _str_buf = f"{' '.join(tokens[:2])} ... {' '.join(tokens[-3:])}" if len(tokens) > 4 else str_buf
        _print(f"TEST: recv returned {_str_buf}")
        for i, token in enumerate(tokens):
            if len(token) == 0:
                # there could be a "" at the start or the end, if a space is there
                continue
            if int(token) == expected:
                if i < 2 or len(tokens) - i < 4:
                    _print("TEST: got %d!" % expected)
                if i == 2 and len(tokens) > 4:
                    print("  ...")
                expected += 1
                str_buf = ''
            elif int(token) > expected:
                _print("TEST: ERROR: got %s but was expecting %d" %(token, expected))
                sys.exit(-1)
            else:
                # we only received the first part of the number at the end
                # we must leave it in the buffer and read more.
                str_buf = token
                break

 
def host1(listen_port, remote_port):
    s = Streamer("A", dst_ip="localhost", dst_port=remote_port,
                 src_ip="localhost", src_port=listen_port)

    # TEST1
    receive(s, NUMS_1)
    _print("TEST: STAGE 1 TEST PASSED!")

    # TEST2
    # send large chunks of data
    i = 0
    buf = ""
    while i < NUMS_2:
        buf += ("%d " % i)
        if len(buf) > 12345 or i == NUMS_2-1:
            print(f"TEST: sending {buf[:15]}...{buf[-15:]}")
            s.send(buf.encode('utf-8'))
            buf = ""
            print("")
        i += 1
    _print("TEST: FINISHED SENDING FOR TEST2")

    s.close()

 
def host2(listen_port, remote_port):
    s = Streamer("B", dst_ip="localhost", dst_port=remote_port,
                 src_ip="localhost", src_port=listen_port)

    # TEST1
    # send small pieces of data
    for i in range(NUMS_1):
        buf = ("%d " % i)
        _print("TEST: sending {%s}" % buf)
        s.send(buf.encode('utf-8'))
    _print("TEST: FINISHED SENDING FOR TEST1")

    # TEST2
    receive(s, NUMS_2)
    _print("TEST: STAGE 2 TEST PASSED!")

    s.close()


def main():
    lossy_socket.sim = lossy_socket.SimulationParams(
        loss_rate=0.1,
        corruption_rate=0.1,
        max_delivery_delay=0.1,
        become_reliable_after=100000.0,
    )

    if len(sys.argv) < 4:
        print("usage is: python3 test.py [port1] [port2] [1|2]")
        print("First run with last argument set to 1, then with 2 (in two different terminals on the same machine")
        sys.exit(-1)
    port1 = int(sys.argv[1])
    port2 = int(sys.argv[2])

    if sys.argv[3] == "1":
        host1(port1, port2)
    elif sys.argv[3] == "2":
        host2(port2, port1)
    else:
        print("Unexpected last argument: " + sys.argv[2])


if __name__ == "__main__":
    main()
