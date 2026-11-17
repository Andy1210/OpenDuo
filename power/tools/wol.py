#!/usr/bin/python3 -I
"""wol.py MAC ADDR...: send a Wake-on-LAN magic packet (UDP port 9) to each ADDR
(a broadcast or the sleeping phone's own address)."""

import socket
import sys

mac = bytes.fromhex(sys.argv[1].replace(":", ""))
pkt = b"\xff" * 6 + mac * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for dst in sys.argv[2:]:
    s.sendto(pkt, (dst, 9))
print("sent to", sys.argv[2:])
