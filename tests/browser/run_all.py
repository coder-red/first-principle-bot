"""Run every browser suite against a locally running server.

    python main.py                      # in one shell
    python tests/browser/run_all.py     # in another

Exits non-zero if any suite fails. These are plain scripts rather than pytest
cases because each one drives a full browser session end to end; keeping them
separate makes a failure point at one behaviour instead of one giant test.
"""

import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("FP_BASE_URL", "http://127.0.0.1:8000")

SUITES = [
    ("deck UI, themes and breakpoints", "test_deck_ui.py"),
    ("quiz, drill trail and persistence", "test_quiz_and_thread.py"),
    ("verified vs unverified rendering", "test_verification_states.py"),
    ("regenerate against persistence", "test_regenerate.py"),
    ("question reframing", "test_reframe.py"),
    ("standalone skill template", "test_skill_template.py"),
    ("access gate", "test_access_gate.py"),
]


def server_is_up():
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def main():
    if not server_is_up():
        print(f"No server at {BASE}. Start one with:  python main.py")
        return 1

    failed = []
    for label, script in SUITES:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        result = subprocess.run([sys.executable, os.path.join(HERE, script)])
        if result.returncode != 0:
            failed.append(label)

    print("\n" + "=" * 70)
    if failed:
        print(f"{len(failed)} suite(s) failed: " + ", ".join(failed))
        return 1
    print(f"all {len(SUITES)} browser suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
