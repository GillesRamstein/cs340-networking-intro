import sys

import lossy_socket
from streamer import Streamer

NUMS=1000


def receive(s):
    expected = 0
    str_buf = ""
    while expected < NUMS:
        data = s.recv()
        if not data:
            continue

        print("recv returned {%s}" % data.decode('utf-8'))
        str_buf += data.decode('utf-8')
        for t in str_buf.split(" "):
            if len(t) == 0:
                # there could be a "" at the start or the end, if a space is there
                continue
            if int(t) == expected:
                print("got %d!" % expected)
                expected += 1
                str_buf = ''
            elif int(t) > expected:
                print("ERROR: got %s but was expecting %d" %(t, expected))
                sys.exit(-1)
            else:
                # we only received the first part of the number at the end
                # we must leave it in the buffer and read more.
                str_buf = t
                break

    
def host1(listen_port, remote_port):
    s = Streamer(dst_ip="localhost", dst_port=remote_port,
                 src_ip="localhost", src_port=listen_port)
    receive(s)
    print("STAGE 1 TEST PASSED!")
    # send large chunks of data
    i = 0
    buf = ""
    while i < NUMS:
        buf += ("%d " % i)
        if len(buf) > 12345 or i == NUMS-1:
            print("sending {%s}" % buf)
            s.send(buf.encode('utf-8'))
            buf = ""
        i += 1
    s.close()
    print("CHECK THE OTHER SCRIPT FOR STAGE 2 RESULTS.")

        
def host2(listen_port, remote_port):
    s = Streamer(dst_ip="localhost", dst_port=remote_port,
                 src_ip="localhost", src_port=listen_port)
    # send small pieces of data
    for i in range(NUMS):
        buf = ("%d " % i)
        print("sending {%s}" % buf)
        s.send(buf.encode('utf-8'))
    receive(s)
    s.close()
    print("STAGE 2 TEST PASSED!")


def main():
    lossy_socket.sim = lossy_socket.SimulationParams(
        loss_rate=0.0,
        corruption_rate=0.0,
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
