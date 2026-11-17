import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duosettings import safetimer  # noqa: E402


def quiet(fn, *args):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = fn(*args)
    return result, err.getvalue()


class SafeTimer(unittest.TestCase):
    def test_after_answers_true_whatever_the_call_returns(self):
        self.assertIs(safetimer.keep_running_after(lambda: None)(), True)
        self.assertIs(safetimer.keep_running_after(lambda: False)(), True)

    def test_after_survives_an_exception(self):
        def boom():
            raise OSError("nmcli gone")

        result, err = quiet(safetimer.keep_running_after(boom))
        self.assertIs(result, True)
        self.assertIn("nmcli gone", err)

    def test_keep_running_passes_the_answer_and_survives(self):
        self.assertIs(safetimer.keep_running(lambda: False)(), False)

        def boom():
            raise ValueError("bad")

        self.assertIs(quiet(safetimer.keep_running(boom))[0], True)

    def test_guarded_swallows_a_worker_failure(self):
        def boom(_x):
            raise ValueError("bad image")

        result, err = quiet(safetimer.guarded(boom), 1)
        self.assertIsNone(result)
        self.assertIn("bad image", err)

    def test_exit_is_not_swallowed(self):
        def quit_():
            raise SystemExit(0)

        for wrap in (safetimer.keep_running_after, safetimer.keep_running, safetimer.guarded):
            with self.assertRaises(SystemExit):
                wrap(quit_)()


if __name__ == "__main__":
    unittest.main()
