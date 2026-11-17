import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from duoshell import lockwatch as lw  # noqa: E402


class RelockWatch(unittest.TestCase):
    def test_never_while_unlocked(self):
        w = lw.RelockWatch()
        for t in range(0, 100, 2):
            self.assertFalse(w.tick(t, False, False))

    def test_a_live_client_is_left_alone(self):
        w = lw.RelockWatch()
        for t in range(0, 100, 2):
            self.assertFalse(w.tick(t, True, True))

    def test_a_dead_client_is_replaced_after_the_grace(self):
        w = lw.RelockWatch()
        self.assertFalse(w.tick(0, True, False))
        self.assertFalse(w.tick(lw.GRACE_S - 0.1, True, False))
        self.assertTrue(w.tick(lw.GRACE_S + 0.1, True, False))

    def test_a_normal_unlock_is_not_a_crash(self):
        # the client exited, duo-wc's "unlocked" arrives inside the grace
        w = lw.RelockWatch()
        self.assertFalse(w.tick(0, True, False))
        self.assertFalse(w.tick(1, False, False))
        self.assertFalse(w.tick(5, False, False))
        self.assertFalse(w.tick(10, False, False))

    def test_backoff_doubles_to_a_cap(self):
        w = lw.RelockWatch()
        t = 0.0
        gaps = []
        last = None
        for _ in range(4000):
            t += 0.5
            if w.tick(t, True, False):
                if last is not None:
                    gaps.append(round(t - last))
                last = t
            if len(gaps) == 6:
                break
        self.assertEqual(gaps[:4], [5, 10, 20, 40])
        self.assertLessEqual(max(gaps), lw.BACKOFF_MAX_S + 1)
        self.assertGreaterEqual(gaps[-1], 55)

    def test_a_stable_client_resets_the_backoff(self):
        w = lw.RelockWatch()
        self.assertFalse(w.tick(0, True, False))
        self.assertTrue(w.tick(4, True, False))  # started
        self.assertFalse(w.tick(5, True, True))
        self.assertFalse(w.tick(5 + lw.STABLE_S + 1, True, True))
        self.assertEqual(w.fails, 0)
        # it dies later: the first-failure timing again
        self.assertFalse(w.tick(100, True, False))
        self.assertTrue(w.tick(100 + lw.GRACE_S + 0.1, True, False))


if __name__ == "__main__":
    unittest.main()
