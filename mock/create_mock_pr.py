import json
import subprocess
from pathlib import Path
import shutil
import os
import stat
import time

REPO_DIR = Path(__file__).resolve().parent / "mock_repo"


def _force_remove_readonly(func, path, exc_info):
    """Handles two distinct Windows-only rmtree failures:
    - WinError 5 (read-only attribute git sets on .git/objects files): clear it and retry.
    - WinError 32 (file locked by another process, commonly Defender's real-time scanner
      briefly holding a freshly-written file): retry a few times with a short pause, since
      the is almost always transient.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    for attempt in range(5):
        try:
            func(path)
            return
        except PermissionError:
            if attempt == 4:
                raise 
            time.sleep(0.3)

GOOD_CALCULATOR = '''\
def average(numbers):
    """Return the arithmetic mean of a non-empty list of numbers."""
    if not numbers:
        raise ValueError("average() requires at least one number")
    return sum(numbers) / len(numbers)


def divide(a, b):
    """Safe division; raises a clear error instead of crashing on b == 0."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def clamp(value, low, high):
    """Clamp value into [low, high]."""
    return max(low, min(value, high))
'''

TEST_CALCULATOR = '''\
from calculator import average, divide, clamp

def test_average():
    assert average([1, 2, 3]) == 2

def test_average_empty_raises():
    try:
        average([])
        assert False, "expected ValueError"
    except ValueError:
        pass

def test_divide():
    assert divide(10, 2) == 5

def test_divide_by_zero_raises():
    try:
        divide(1, 0)
        assert False, "expected ValueError"
    except ValueError:
        pass

def test_clamp():
    assert clamp(15, 0, 10) == 10
    assert clamp(-5, 0, 10) == 0
    assert clamp(5, 0, 10) == 5
'''

BUGGY_CALCULATOR = '''\
def average(numbers):
    """Return the arithmetic mean of a non-empty list of numbers."""
    if not numbers:
        raise ValueError("average() requires at least one number")
    return sum(numbers) / (len(numbers) - 1)


def divide(a, b):
    return a / b


def clamp(value, low, high):
    """Clamp value into [low, high]."""
    return max(high, min(value, low))
'''


def run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def main():
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR, onexc=_force_remove_readonly)
    REPO_DIR.mkdir(parents=True)

    run(["git", "init", "-q", "-b", "main"], REPO_DIR)
    run(["git", "config", "user.email", "mock@example.com"], REPO_DIR)
    run(["git", "config", "user.name", "Mock Author"], REPO_DIR)

    (REPO_DIR / "calculator.py").write_text(GOOD_CALCULATOR)
    (REPO_DIR / "test_calculator.py").write_text(TEST_CALCULATOR)
    run(["git", "add", "."], REPO_DIR)
    run(["git", "commit", "-q", "-m", "Add calculator with average/divide/clamp + tests"], REPO_DIR)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR, capture_output=True, text=True).stdout.strip()

    run(["git", "checkout", "-q", "-b", "feature/bad-average"], REPO_DIR)
    (REPO_DIR / "calculator.py").write_text(BUGGY_CALCULATOR)
    run(["git", "add", "."], REPO_DIR)
    run(["git", "commit", "-q", "-m", "Simplify divide(), tweak average() and clamp()"], REPO_DIR)
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR, capture_output=True, text=True).stdout.strip()

    metadata = {
        "owner": "local-mock", "repo": "calculator", "pr_number": 1,
        "title": "Simplify divide(), tweak average() and clamp()",
        "body": "Cleans up the calculator module a bit.",
        "base_branch": "main", "base_sha": base_sha,
        "head_branch": "feature/bad-average", "head_sha": head_sha,
    }
    (REPO_DIR / ".mock_pr.json").write_text(json.dumps(metadata, indent=2))
    run(["git", "checkout", "-q", "feature/bad-average"], REPO_DIR)

    print(f"Mock repo built at: {REPO_DIR}")
    print(f"base_sha={base_sha}  head_sha={head_sha}")
    print("\nRun the agent against it with:")
    print(f"  python main.py local:{REPO_DIR}")


if __name__ == "__main__":
    main()
