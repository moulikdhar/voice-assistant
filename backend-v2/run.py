import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVEKIT_EXE = ROOT.parent / "tools" / ("livekit-server.exe" if sys.platform == "win32" else "livekit-server")

# v2: ONE Python process (backend + AI voice agent). The local LiveKit server is only the browser's phone line;
# it is started here too if the binary is in ../tools and nothing is listening on port 7880 yet.
BACKEND = [sys.executable, "-m", "uvicorn", "backend.server:app", "--port", "8000", "--log-level", "warning"]


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    running: dict[str, subprocess.Popen] = {}
    if not _port_open(7880):
        if LIVEKIT_EXE.exists():
            running["livekit"] = subprocess.Popen([str(LIVEKIT_EXE), "--dev"], cwd=ROOT,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print(f"LiveKit server is not running and {LIVEKIT_EXE} was not found: start `livekit-server --dev` first.")
    running["backend"] = subprocess.Popen(BACKEND, cwd=ROOT)
    print("Backend v2 (with the AI voice agent) started. Open http://localhost:5173 once the frontend is running. "
          "Ctrl+C to stop.")
    try:
        running["backend"].wait()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        # Ctrl+C already reached the backend: give it a moment to save live calls (WAV, summary) first
        for p in running.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.terminate()


if __name__ == "__main__":
    main()
