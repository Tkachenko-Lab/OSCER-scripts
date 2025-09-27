#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_status_with_cancel.py — Student-friendly Slurm job status with resource info + SSH helper + CANCEL menu.

New:
- --cancel-menu       : interactively choose one or more of YOUR jobs (RUNNING/PENDING/CONFIGURING/...)
                        to cancel with scancel; shows a confirmation summary before proceeding.

Existing:
- --ssh-menu          : interactively choose a RUNNING job and SSH into its node; cd to /lscratch/<JOBID>
                        (fallbacks: /tmp/<JOBID>, /scratch/<JOBID>), then open a login shell there.
- --nodeinfo [NODE]   : colorful CPU model / topology / freq / mem / current load for local or a remote node (via SSH).
- --watch [N]         : refresh every N seconds (default 3 if no N is given). Press 'q' to quit, 'c' to toggle color.
- --color/--no-color  : force enable/disable ANSI colors.

Default:
- Show your running/pending jobs (table).
- --job ID            : show details for one job (squeue/scontrol, sstat, sacct).
- --history 24h       : include jobs that ended within the last 24 hours.

Only uses Slurm CLI and standard Unix tools; degrades gracefully if some are missing.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from shutil import which
import select
try:
    import termios, tty
    _HAS_TERMIOS = True
except Exception:
    _HAS_TERMIOS = False

# ---------- ANSI helpers ----------
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def visible_len(s):
    return len(ANSI_RE.sub("", s))

def colorize(enabled, code, s):
    if not enabled or not code:
        return s
    return "\033[" + code + "m" + s + "\033[0m"

def color_state(use_color, state):
    raw = (state or "")
    s = raw.upper()
    # normalize variants like CANCELLED+
    if s.startswith("CANCELLED"):
        key = "CANCELLED"
    elif s.startswith("RUNNING"):
        key = "RUNNING"
    elif s.startswith("PENDING"):
        key = "PENDING"
    elif s.startswith("COMPLETED"):
        key = "COMPLETED"
    elif "TIMEOUT" in s:
        key = "TIMEOUT"
    elif "OUT_OF_MEMORY" in s or "OOM" in s:
        key = "OUT_OF_MEMORY"
    elif s.startswith("FAILED"):
        key = "FAILED"
    elif s.startswith("SUSPENDED"):
        key = "SUSPENDED"
    elif s.startswith("COMPLETING"):
        key = "COMPLETING"
    elif s.startswith("CONFIGURING"):
        key = "CONFIGURING"
    else:
        key = s

    colors = {
        "RUNNING": "32",       # green
        "COMPLETED": "36",     # cyan
        "PENDING": "33",       # yellow
        "COMPLETING": "36",    # cyan
        "CONFIGURING": "33",   # yellow
        "FAILED": "31",        # red
        "CANCELLED": "35",     # magenta
        "TIMEOUT": "31",       # red
        "OUT_OF_MEMORY": "31", # red
        "SUSPENDED": "35",     # magenta
    }
    code = colors.get(key)
    return colorize(use_color, code, raw) if code else raw

# ---------- subprocess helpers ----------
def run(cmd, text=True):
    try:
        if text:
            return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, universal_newlines=True)
        else:
            return subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except Exception:
        return ""

def have(cmd):
    return which(cmd) is not None

# ---------- Slurm helpers ----------
def first_node(nodelist):
    """
    Convert Slurm NodeList strings into a single concrete hostname.
    Examples:
      'c1028' -> 'c1028'
      'c[1028-1030,1040]' -> 'c1028'
      '(null)' or 'None assigned' -> ''
    """
    if not nodelist:
        return ""
    val = nodelist.strip()
    if val in {"(null)", "None", "None assigned"}:
        return ""
    if "[" not in val or "]" not in val:
        return val.split(",")[0].strip()
    prefix = val.split("[", 1)[0]
    inside = val.split("[", 1)[1].rsplit("]", 1)[0]
    first = inside.split(",")[0]
    if "-" in first:
        start = first.split("-", 1)[0]
        return prefix + start
    return prefix + first

def parse_squeue(user_override=None):
    # Custom format: JobID,Name,State,Elapsed,CPUS,Mem,Partition,NodeList/Reason
    fmt = "%i|%j|%T|%M|%C|%m|%P|%R"
    user = (user_override or os.getenv("USER", "").strip() or run(["whoami"]).strip())
    out = run(["squeue", "-h", "-u", user, "-o", fmt])
    rows = []
    if not out:
        return rows
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 8:
            continue
        rows.append(parts)
    return rows

def print_table(rows, use_color=False):
    headers = ["JOBID","NAME","STATE","ELAPSED","CPUS","MEM","PARTITION","NODE"]
    colw = []
    for i, h in enumerate(headers):
        max_cell = 0
        for r in rows:
            if i < len(r):
                max_cell = max(max_cell, visible_len(r[i]))
        colw.append(max(visible_len(h), max_cell))

    head_cells = [colorize(use_color, "1;36", h) for h in headers]
    out = []
    for i, cell in enumerate(head_cells):
        pad = colw[i] - visible_len(cell)
        out.append(cell + (" " * pad if pad > 0 else ""))
    print("  ".join(out))

    for r in rows:
        cells = list(r)
        if len(cells) >= 3:
            cells[2] = color_state(use_color, cells[2])
        if len(cells) >= 8 and cells[7] and cells[7] not in ("-", ""):
            cells[7] = colorize(use_color, "36", cells[7])  # cyan node

        out = []
        for i, cell in enumerate(cells[:len(headers)]):
            pad = colw[i] - visible_len(cell)
            out.append(cell + (" " * pad if pad > 0 else ""))
        print("  ".join(out))

def details_job(jobid):
    print("== Job {} ==".format(jobid))
    if have("scontrol"):
        print("-- scontrol show job --")
        print(run(["scontrol", "show", "job", str(jobid)]).strip())
    if have("sstat"):
        print("-- sstat (live averages) --")
        print(run(["sstat", "-j", "{}.batch".format(jobid), "--format=AveCPU,AveRSS,MaxRSS,MaxVMSize,AllocCPUS"]).strip())
    if have("sacct"):
        print("-- sacct (accounting) --")
        print(run(["sacct", "-j", str(jobid), "--format=JobID,State,Elapsed,MaxRSS,MaxVMSize,AveRSS,CPUTimeRAW"]).strip())

def include_history(hours, user_override=None):
    if not have("sacct"):
        return []
    user = (user_override or os.getenv("USER", "").strip() or run(["whoami"]).strip())
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    out = run(["sacct", "-u", user, "-S", since, "--parsable2", "--noheader",
               "--format=JobID,JobName,State,Elapsed,ReqMem,AllocCPUS"])
    rows = []
    if not out:
        return rows
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 6:
            jobid, jobname, state, elapsed, reqmem, alloccpus = parts[:6]
            if "." in jobid:
                continue
            rows.append([jobid, jobname, state, elapsed, alloccpus, reqmem, "-", "-"])
    return rows

# ---------- helpers for jobid & dedup ----------
def _base_jobid(jid):
    """Normalize job id (strip step suffix like .batch/.extern/.0). Keep array index (12345_7)."""
    return jid.split('.', 1)[0] if jid else jid

def merge_rows_live_and_history(live_rows, hist_rows):
    """Deduplicate by base jobid: prefer live squeue rows; drop sacct step rows and any history rows whose job still appears live."""
    live_idx = {_base_jobid(r[0]): r for r in live_rows if r and r[0]}
    merged = []
    merged.extend(live_rows)
    for r in hist_rows:
        if not r or not r[0]:
            continue
        jid = r[0]
        if "." in jid:
            continue
        if _base_jobid(jid) in live_idx:
            continue
        merged.append(r)
    return merged

# ---------- node info (local or remote) ----------
def _runcmd_local_or_remote(remote, c):
    """Single-attempt SSH to avoid multiple password prompts.
    If `remote` lacks a domain, append .oscer.ou.edu. If it lacks a user, prefix $USER@.
    """
    if not remote:
        return run(["bash", "-lc", c])
    user = os.getenv("USER", "").strip() or run(["whoami"]).strip()
    host = remote
    if "." not in host:
        host = f"{host}.oscer.ou.edu"
    if "@" not in host:
        host = f"{user}@{host}"
    out = run(["ssh",
               "-T",
               "-o", "BatchMode=no",
               "-o", "StrictHostKeyChecking=no",
               "-o", "ConnectTimeout=5",
               "-o", "LogLevel=ERROR",
               "-o", "ControlMaster=auto",
               "-o", "ControlPersist=60",
               "-o", "ControlPath=~/.ssh/cm-%r@%h:%p",
               "-o", "NumberOfPasswordPrompts=1",
               "-o", "PreferredAuthentications=publickey,password",
               "-o", "KbdInteractiveAuthentication=no",
               host, c])
    return out or ""

def fetch_node_info(remote):
    """Collect CPU/mem/load info locally or via ssh <remote> using a SINGLE command."""
    info = {
        "host": remote or run(["hostname"]).strip(),
        "model": "",
        "sockets": "",
        "coresper": "",
        "threadsper": "",
        "cpus": "",
        "mhz": "",
        "mhzmax": "",
        "mem_total": "",
        "load": "",
        "uptime": ""
    }

    one_shot = (
        "LC_ALL=C lscpu; "
        "echo __SEP1__; "
        "LC_ALL=C free -h | awk '/^Mem:/ {print $2}'; "
        "echo __SEP2__; "
        "cat /proc/loadavg; "
        "echo __SEP3__; "
        "uptime -p || true"
    )

    if remote:
        out = _runcmd_local_or_remote(remote, one_shot)
    else:
        out = run([
            "bash", "-lc",
            "LC_ALL=C lscpu; echo __SEP1__; "
            "LC_ALL=C free -h | awk '/^Mem:/ {print $2}'; echo __SEP2__; "
            "cat /proc/loadavg; echo __SEP3__; uptime -p || true"
        ])

    if not out:
        return info

    lines = out.splitlines()
    blocks = []
    cur = []
    for line in lines:
        if line.strip() == "__SEP1__":
            blocks.append("\n".join(cur)); cur = []
        elif line.strip() == "__SEP2__":
            blocks.append("\n".join(cur)); cur = []
        elif line.strip() == "__SEP3__":
            blocks.append("\n".join(cur)); cur = []
        else:
            cur.append(line)
    blocks.append("\n".join(cur))

    if blocks:
        lscpu = blocks[0]
        def g(rx):
            m = re.search(rx, lscpu, re.M)
            return m.group(1).strip() if m else ""
        info["model"]      = g(r"Model name:\s*(.+)")
        info["sockets"]    = g(r"Socket\(s\):\s*(\d+)")
        info["coresper"]   = g(r"Core\(s\) per socket:\s*(\d+)")
        info["threadsper"] = g(r"Thread\(s\) per core:\s*(\d+)")
        info["cpus"]       = g(r"^CPU\(s\):\s*(\d+)")
        info["mhz"]        = g(r"CPU MHz:\s*([\d\.]+)")
        info["mhzmax"]     = g(r"CPU max MHz:\s*([\d\.]+)")

    if len(blocks) > 1:
        mem_line = blocks[1].strip()
        info["mem_total"] = (mem_line.split()[0] if mem_line else "")

    if len(blocks) > 2:
        info["load"] = blocks[2].strip()

    if len(blocks) > 3:
        info["uptime"] = blocks[3].strip()

    return info

def print_node_info(remote, use_color):
    i = fetch_node_info(remote)
    host = i['host'] or (remote or 'unknown')
    title = "Node Info — {}".format(host)
    print(colorize(use_color, "1;36", title))
    print(colorize(use_color, "2", "─" * len(title)))
    print("{}    {}".format(colorize(use_color, "1;33", "CPU Model:"), i['model'] or 'N/A'))
    topo = "Sockets={}  Cores/Socket={}  Threads/Core={}  Logical CPUs={}".format(
        i['sockets'] or '?', i['coresper'] or '?', i['threadsper'] or '?', i['cpus'] or '?'
    )
    print("{}     {}".format(colorize(use_color, "1;33", "Topology:"), topo))
    freq = "Base(?) MHz={}  Max MHz={}".format(i['mhz'] or '?', i['mhzmax'] or '?')
    print("{}    {}".format(colorize(use_color, "1;33", "Frequency:"), freq))
    print("{}       Total={}".format(colorize(use_color, "1;33", "Memory:"), i['mem_total'] or 'N/A'))
    print("{}     {}".format(colorize(use_color, "1;33", "CPU Load:"), i['load'] or 'N/A'))
    if i["uptime"]:
        print("{}       {}".format(colorize(use_color, "1;33", "Uptime:"), i['uptime']))

# ---------- ssh menu ----------
def ssh_menu(use_color):
    # SSH menu uses *current* user, not --user override
    rows = parse_squeue(None)
    running = []
    for r in rows:
        jobid, name, state, elapsed, cpus, mem, part, nodelist = r
        node = first_node(nodelist)
        if state.upper().startswith("RUN") and node:
            running.append((jobid, name, node, elapsed, cpus, mem, part))

    if not running:
        print("[INFO] No RUNNING jobs with assigned nodes were found for your user.")
        return 1

    print(colorize(use_color, "1;36", "Select a job to SSH into its node"))
    for idx, (jobid, name, node, elapsed, cpus, mem, part) in enumerate(running, 1):
        line = "[{}] job={}  node={}  name={}  elapsed={}  cpus={}  mem={}  part={}".format(
            idx, jobid, node, name, elapsed, cpus, mem, part
        )
        print(colorize(use_color, "32", line))

    try:
        choice = int(input(colorize(use_color, "1;35", "Enter number: ")).strip())
    except Exception:
        print("[ERROR] Invalid input.")
        return 1
    if choice < 1 or choice > len(running):
        print("[ERROR] Choice out of range.")
        return 1

    jobid, name, node, _elapsed, _cpus, _mem, _part = running[choice - 1]

    remote_cmd = (
        'cd /lscratch/{jid} 2>/dev/null || '
        'cd /tmp/{jid} 2>/dev/null || '
        'cd /scratch/{jid} 2>/dev/null || '
        'echo "[WARN] No job dir found under /lscratch|/tmp|/scratch for {jid} on $(hostname)"; '
        'pwd; exec $SHELL -l'
    ).format(jid=jobid)

    print(colorize(use_color, "1;36", "SSH → {}  (starting in /lscratch/{} if it exists)".format(node, jobid)))
    os.execvp("ssh", ["ssh", "-t", "-o", "StrictHostKeyChecking=no", node, remote_cmd])

# ---------- cancel menu ----------
def _parse_selection(s, n):
    """Parse a selection string like '1,3-5,7' into sorted unique indices (1..n)."""
    sel = set()
    for tok in s.replace(" ", "").split(","):
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                a = int(a); b = int(b)
            except Exception:
                continue
            lo, hi = (a, b) if a <= b else (b, a)
            for i in range(lo, hi+1):
                if 1 <= i <= n:
                    sel.add(i)
        else:
            try:
                i = int(tok)
            except Exception:
                continue
            if 1 <= i <= n:
                sel.add(i)
    return sorted(sel)

def cancel_menu(use_color):
    if not have("scancel"):
        print("[ERROR] scancel not found in PATH.")
        return 2

    # Use current user ALWAYS for safety
    rows = parse_squeue(None)
    if not rows:
        print("[INFO] No jobs found for your user.")
        return 0

    # Show all not-yet-final states as cancellable
    cancellable = []
    for r in rows:
        jobid, name, state, elapsed, cpus, mem, part, nodelist = r
        st = (state or "").upper()
        if st.startswith(("RUNN","PEND","CONFIG","COMPLET")):
            # Completed jobs can't be cancelled; exclude COMPLETED
            if st.startswith("COMPLET"):
                continue
            cancellable.append(r)

    if not cancellable:
        print("[INFO] No RUNNING/PENDING/CONFIGURING jobs to cancel.")
        return 0

    print(colorize(use_color, "1;36", "Select job(s) to cancel (comma/range, e.g., 1,3-5)"))
    for idx, r in enumerate(cancellable, 1):
        jobid, name, state, elapsed, cpus, mem, part, nodelist = r
        node = first_node(nodelist)
        line = "[{idx}] job={jid}  state={st}  name={nm}  node={nd}  elapsed={el}  cpus={cp}  mem={mm}  part={pt}".format(
            idx=idx, jid=jobid, st=state, nm=name, nd=node or "-", el=elapsed, cp=cpus, mm=mem, pt=part
        )
        print(colorize(use_color, "33", line))

    sel_str = input(colorize(use_color, "1;35", "Enter selection: ")).strip()
    indices = _parse_selection(sel_str, len(cancellable))
    if not indices:
        print("[INFO] Nothing selected; aborting.")
        return 1

    chosen = [cancellable[i-1] for i in indices]
    # Summary
    print(colorize(use_color, "1;36", "\nYou are about to CANCEL the following jobs:"))
    for r in chosen:
        jobid, name, state, elapsed, cpus, mem, part, nodelist = r
        print(colorize(use_color, "31", f"  - {jobid}  ({state})  {name}"))

    sure = input(colorize(use_color, "1;31", "Type 'yes' to confirm cancellation: ")).strip().lower()
    if sure != "yes":
        print("[INFO] Confirmation not given. No jobs were cancelled.")
        return 1

    # Execute scancel; collect per-job status
    errs = 0
    for r in chosen:
        jobid = r[0]
        try:
            rc = subprocess.call(["scancel", str(jobid)])
            if rc == 0:
                print(colorize(use_color, "32", f"[OK] scancel {jobid}"))
            else:
                print(colorize(use_color, "31", f"[ERR] scancel {jobid} → exit {rc}"))
                errs += 1
        except Exception as e:
            print(colorize(use_color, "31", f"[EXC] scancel {jobid}: {e}"))
            errs += 1

    if errs == 0:
        print(colorize(use_color, "1;32", "All selected jobs were sent a cancel request."))
    else:
        print(colorize(use_color, "1;33", f"Cancel requests issued with {errs} error(s). Some jobs may remain."))
    return 0

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Pretty Slurm job status for students (+ SSH + node info + CANCEL menu)")
    ap.add_argument("--watch", nargs="?", const=3, type=int,
                    help="Refresh every N seconds (default 3 if no value supplied)")
    ap.add_argument("--job", type=str, help="Show details for one job id")
    ap.add_argument("--history", type=str, help="Include finished jobs newer than this window, e.g. '24h'")
    ap.add_argument("--ssh-menu", action="store_true", help="Interactively pick a RUNNING job and SSH into its node; cd to /lscratch/<JOBID> (fallback /tmp or /scratch)")
    ap.add_argument("--cancel-menu", action="store_true", help="Interactively pick YOUR jobs to cancel, with a final confirmation")
    ap.add_argument("--nodeinfo", nargs="?", const="", metavar="NODE", help="Show colorful CPU/mem/load for local host or for NODE via SSH")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--color", action="store_true", help="Force ANSI colors even if stdout is not a TTY")
    ap.add_argument("--user", type=str, help="Show jobs for this USER instead of the current user (not used by cancel/ssh menus)")
    args = ap.parse_args()

    target_user = args.user

    use_color = (not args.no_color) and (
        sys.stdout.isatty() or args.color or
        os.getenv("FORCE_COLOR") in ("1","true","TRUE","yes","YES") or
        os.getenv("CLICOLOR_FORCE") == "1"
    )

    if args.nodeinfo is not None:
        node = args.nodeinfo.strip() if args.nodeinfo is not None else ""
        node = node or None
        print_node_info(node, use_color)
        return 0

    if args.ssh_menu:
        return ssh_menu(use_color)

    if args.cancel_menu:
        return cancel_menu(use_color)

    if args.job:
        details_job(args.job)
        return 0

    hist_rows = []
    if args.history:
        harg = args.history.strip().lower()
        if harg.endswith("h"):
            hours = int(harg[:-1] or "0")
        else:
            hours = int(harg)
        hist_rows = include_history(hours, target_user)

    def render_once():
        live = parse_squeue(target_user)
        rows = merge_rows_live_and_history(live, hist_rows)
        if rows:
            print_table(rows, use_color)
        else:
            who = (target_user or (os.getenv("USER", "").strip() or run(["whoami"]).strip()))
            print(f"[INFO] No jobs found for user: {who}.")
        sys.stdout.flush()

    if args.watch:
        interactive = sys.stdin.isatty() and _HAS_TERMIOS
        fd = None
        old_term = None
        try:
            if interactive:
                fd = sys.stdin.fileno()
                old_term = termios.tcgetattr(fd)
                tty.setcbreak(fd)  # char-at-a-time, no Enter needed
            hint = "[watch] press 'q' to quit, 'c' to toggle color"
            while True:
                os.system("clear")
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(colorize(use_color, "1;36", ts), end="  ")
                print(colorize(use_color, "2", hint))
                render_once()

                end_by = time.time() + args.watch
                while True:
                    remaining = end_by - time.time()
                    if remaining <= 0:
                        break
                    if interactive and fd is not None:
                        r, _, _ = select.select([fd], [], [], min(0.2, max(0, remaining)))
                        if r:
                            ch = os.read(fd, 1).decode(errors="ignore")
                            if ch in ("q", "Q"):
                                raise KeyboardInterrupt
                            if ch in ("c", "C"):
                                use_color = not use_color
                                break  # redraw immediately with new color mode
                    else:
                        time.sleep(min(0.2, max(0, remaining)))
        except KeyboardInterrupt:
            print("\n[watch] exited.")
        finally:
            if interactive and fd is not None and old_term is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        return 0
    else:
        render_once()
        return 0

if __name__ == "__main__":
    sys.exit(main())
