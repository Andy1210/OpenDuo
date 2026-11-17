import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import trust  # noqa: E402


class FakeFactory(threading.Thread):
    """duo-wc's side of a trust factory: answers `control` / `wayland` with a
    socketpair end, anything else with an error."""

    daemon = True

    def __init__(self, sock):
        super().__init__()
        self.sock = sock
        self.kept = []

    def run(self):
        while True:
            try:
                ask = self.sock.recv(256)
            except OSError:
                return
            if not ask:
                return
            if ask in (b"control", b"wayland"):
                mine, yours = socket.socketpair()
                self.kept.append(mine)
                socket.send_fds(self.sock, [b"ok"], [yours.fileno()])
                yours.close()
            else:
                self.sock.send(b"error ask for wayland or control")


class Trust(unittest.TestCase):
    def setUp(self):
        self.ours, self.theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.factory = FakeFactory(self.ours)
        self.factory.start()
        os.set_inheritable(self.theirs.fileno(), True)
        self.env = {trust.ENV: str(self.theirs.fileno()), "PATH": "/usr/bin"}

    def tearDown(self):
        trust._fd = None
        self.theirs.close()
        self.ours.close()

    def test_parse_fd(self):
        self.assertEqual(trust.parse_fd("3"), 3)
        self.assertIsNone(trust.parse_fd(""))
        self.assertIsNone(trust.parse_fd(None))
        self.assertIsNone(trust.parse_fd("2"))  # never stdin/out/err
        self.assertIsNone(trust.parse_fd("x3"))

    def test_take_hides_the_factory_from_children(self):
        fd = trust.take(self.env)
        self.assertEqual(fd, self.theirs.fileno())
        self.assertNotIn(trust.ENV, self.env)
        self.assertFalse(os.get_inheritable(fd))
        self.assertTrue(trust.available())

    def test_no_factory_no_trust(self):
        self.assertIsNone(trust.take({}))
        self.assertFalse(trust.available())
        with self.assertRaises(OSError):
            trust.ask("control")

    def test_a_trusted_control_connection_comes_from_the_factory(self):
        trust.take(self.env)
        sock = trust.control_socket()
        sock.sendall(b"ping")
        self.assertEqual(self.factory.kept[-1].recv(16), b"ping")
        sock.close()

    def test_a_refusal_is_an_error(self):
        trust.take(self.env)
        with self.assertRaises(OSError):
            trust.ask("factory")

    def test_child_env(self):
        env = trust.child_env(7, {trust.ENV: "3", "WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(env["WAYLAND_SOCKET"], "7")
        self.assertNotIn(trust.ENV, env)
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-1")

    def test_forget_wayland_socket(self):
        env = {"WAYLAND_SOCKET": "5", "WAYLAND_DISPLAY": "wayland-1"}
        trust.forget_wayland_socket(env)
        self.assertEqual(env, {"WAYLAND_DISPLAY": "wayland-1"})


if __name__ == "__main__":
    unittest.main()
