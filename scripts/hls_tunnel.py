import os, sys, argparse, subprocess, signal, json, re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

PID_FILE = "/tmp/hls_tunnel.pid"
INFO_FILE = "/tmp/hls_tunnel.info"

DEFAULT_HEAD_NODE = "zijd@vastlab.cs.ucla.edu"
DEFAULT_LOCAL_PORT = 8884
DEFAULT_HEAD_PORT = 8884
DEFAULT_REMOTE_PORT = 8891
DEFAULT_SSH_KEY = os.path.expanduser("~/.ssh/id_rsa_vast")
DEFAULT_ENV_FILE = os.path.join(PROJECT_ROOT, ".env")


def is_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def update_env_file(env_file, local_port):
    url = f"http://127.0.0.1:{local_port}"
    lines = []
    found = False
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                if line.strip().startswith("HLS_SERVER_URL="):
                    lines.append(f"HLS_SERVER_URL=\"{url}\"\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"\nHLS_SERVER_URL=\"{url}\"\n")
    with open(env_file, "w") as f:
        f.writelines(lines)
    print(f"Updated {env_file}: HLS_SERVER_URL=\"{url}\"")


def start_tunnel(args):
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        if is_running(pid):
            print(f"Tunnel already running (PID {pid}). Use --stop first.")
            sys.exit(1)

    ssh_cmd = [
        "ssh",
        "-i",
        args.ssh_key,
        "-L",
        f"127.0.0.1:{args.local_port}:localhost:{args.head_port}",
        "-o",
        "ExitOnForwardFailure=yes",
        "-f",
        args.head_node,
        "ssh",
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        f"{args.head_port}:localhost:{args.remote_port}",
        args.compute_node,
    ]

    print(' '.join(ssh_cmd))

    print(f"Starting tunnel: local:{args.local_port} -> {args.head_node}:{args.head_port} -> {args.compute_node}:{args.remote_port}")

    proc = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.wait()

    result = subprocess.run(
        ["pgrep", "-f", f"ssh.*-L.*127.0.0.1:{args.local_port}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("Failed to start tunnel")
        sys.exit(1)

    pid = int(result.stdout.strip().split('\n')[0])

    with open(PID_FILE, "w") as f:
        f.write(str(pid))

    info = {
        "pid": pid,
        "local_port": args.local_port,
        "head_port": args.head_port,
        "remote_port": args.remote_port,
        "head_node": args.head_node,
        "compute_node": args.compute_node,
    }
    with open(INFO_FILE, "w") as f:
        json.dump(info, f)

    update_env_file(args.env_file, args.local_port)
    print(f"Tunnel started (PID {pid})")


def stop_tunnel(args):
    if not os.path.exists(PID_FILE):
        print("No tunnel running")
        return

    with open(PID_FILE, "r") as f:
        pid = int(f.read().strip())

    if is_running(pid):
        os.kill(pid, signal.SIGTERM)
        print(f"Tunnel stopped (PID {pid})")
    else:
        print(f"Tunnel process (PID {pid}) not found")

    for f in [PID_FILE, INFO_FILE]:
        if os.path.exists(f):
            os.remove(f)


def status_tunnel(args):
    if not os.path.exists(PID_FILE):
        print("No tunnel running")
        return

    with open(PID_FILE, "r") as f:
        pid = int(f.read().strip())

    if not is_running(pid):
        print(f"Tunnel process (PID {pid}) not running")
        return

    if os.path.exists(INFO_FILE):
        with open(INFO_FILE, "r") as f:
            info = json.load(f)
        print(f"Tunnel running (PID {pid})")
        print(f"  local:{info['local_port']} -> {info['head_node']}:{info['head_port']} -> {info['compute_node']}:{info['remote_port']}")
    else:
        print(f"Tunnel running (PID {pid})")


def main():
    parser = argparse.ArgumentParser(
        description="SSH tunnel manager for HLS remote server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--head-node", default=DEFAULT_HEAD_NODE, help="SSH target for head node (user@host)")
    parser.add_argument("--compute-node", help="Compute node name (required for start)")
    parser.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT, help="Local port to bind")
    parser.add_argument("--head-port", type=int, default=DEFAULT_HEAD_PORT, help="Intermediate port on head node")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT, help="HLS server port on compute node")
    parser.add_argument("--ssh-key", default=DEFAULT_SSH_KEY, help="SSH identity file")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Path to .env file to update")
    parser.add_argument("--stop", action="store_true", help="Stop running tunnel")
    parser.add_argument("--status", action="store_true", help="Check tunnel status")
    args = parser.parse_args()

    if args.stop:
        stop_tunnel(args)
    elif args.status:
        status_tunnel(args)
    else:
        if not args.compute_node:
            parser.error("--compute-node is required when starting tunnel")
        start_tunnel(args)


if __name__ == "__main__":
    main()
