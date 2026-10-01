"""Console output and elapsed-time helpers for the interactive workflow."""

import time


# Console output and elapsed-time helpers.
class CoastTime:
    def __init__(self):
        self.t = 0

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"> Coast Time:{time.perf_counter() - self.t:.8f} s")
