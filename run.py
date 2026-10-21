

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PROCS = {
    "backend": [sys.executable, "-m", "uvicorn", "backend.server:app", "--port", "8000", "--log-level", "warning"],
    "agent": [sys.executable, "-m", "backend.agent", "dev"],
}


def main() -> None:
    running = {name: subprocess.Popen(cmd, cwd=ROOT) for name, cmd in PROCS.items()}
    print("Backend + AI agent started. Open http://localhost:5173 once the frontend is running. Ctrl+C to stop.")
    try:
        while all(p.poll() is None for p in running.values()):
            time.sleep(0.5)
        for name, p in running.items():
            if p.poll() is not None:
                print(f"\n[{name}] stopped with exit code {p.returncode}. Stopping the rest.")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        # Ctrl+C already reached both programs: give them a moment to save the call (WAV, summary) first
        for p in running.values():
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pass
        for p in running.values():
            if p.poll() is None:
                p.terminate()
        for p in running.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
