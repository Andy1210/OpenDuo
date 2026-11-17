import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import safetimer  # noqa: E402


class KeepRunning(unittest.TestCase):
    def test_result_passes_through(self):
        @safetimer.keep_running
        def stop(x):
            return x

        self.assertIs(stop(False), False)
        self.assertIs(stop(True), True)

    def test_an_exception_keeps_the_timer(self):
        @safetimer.keep_running
        def boom():
            raise OSError("sensor gone")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIs(boom(), True)
        self.assertIn("sensor gone", err.getvalue())

    def test_exit_is_not_swallowed(self):
        @safetimer.keep_running
        def quit_():
            raise SystemExit(0)

        with self.assertRaises(SystemExit):
            quit_()


class OrDefault(unittest.TestCase):
    def test_result_passes_through(self):
        self.assertEqual(safetimer.or_default(lambda x: x + 1)(1), 2)

    def test_an_exception_gives_the_default(self):
        def boom(_path):
            raise ValueError("bad image")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(safetimer.or_default(boom)("x"))
            self.assertEqual(safetimer.or_default(boom, 7)("x"), 7)
        self.assertIn("bad image", err.getvalue())

    def test_exit_is_not_swallowed(self):
        def quit_():
            raise SystemExit(0)

        with self.assertRaises(SystemExit):
            safetimer.or_default(quit_)()


if __name__ == "__main__":
    unittest.main()
