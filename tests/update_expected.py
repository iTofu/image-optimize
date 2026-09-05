#!/usr/bin/env python3
"""Regenerate tests/expected/ from tests/cases/ with the current script.

Run this after an intentional output change, then review the diff of tests/expected/ in the
PR: that diff *is* the behaviour change. Pass case names to limit the update.
"""
import os
import sys

import golden


def main(argv):
    wanted = set(argv[1:])
    n = 0
    for case in golden.cases():
        if wanted and golden.case_name(case) not in wanted:
            continue
        golden.write_expected(case, golden.run(case))
        n += 1
    # drop expected files whose case no longer exists
    names = {golden.case_name(c) for c in golden.cases()}
    for f in os.listdir(golden.EXPECTED_DIR):
        stem = f.rsplit(".", 1)[0]
        if stem not in names:
            os.remove(os.path.join(golden.EXPECTED_DIR, f))
            print(f"removed stale {f}")
    print(f"updated {n} case(s) in {os.path.relpath(golden.EXPECTED_DIR)}")


if __name__ == "__main__":
    main(sys.argv)
