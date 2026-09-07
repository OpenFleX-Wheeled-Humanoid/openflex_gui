import argparse
import os
from pathlib import Path
import shlex
import signal
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--installer-args", nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    output_path = state_dir / "output.log"
    status_path = state_dir / "exit.status"
    pid_path = state_dir / "process.pid"

    if not args.installer_args:
        output_path.write_text("missing installer arguments\n", encoding="utf-8")
        status_path.write_text("2\n", encoding="ascii")
        return 2

    print("[部署] 正在启动安装任务，请在此终端完成授权或交互操作。", flush=True)
    command = shlex.join(args.installer_args)
    child = subprocess.Popen(
        ["/usr/bin/script", "-qefc", command, str(output_path)],
        cwd=os.environ.get("OPENFLEX_WORKSPACE", None),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid_path.write_text(f"{child.pid}\n", encoding="ascii")
    try:
        code = child.wait()
    except KeyboardInterrupt:
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            child.terminate()
        code = child.wait()
    finally:
        pid_path.unlink(missing_ok=True)
    temporary_status = status_path.with_suffix(".tmp")
    temporary_status.write_text(f"{code}\n", encoding="ascii")
    temporary_status.replace(status_path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
