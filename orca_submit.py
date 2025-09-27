#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
from pathlib import Path

TEMPLATE = """#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --nodes=1
#SBATCH --ntasks-per-node={ntasks}
#SBATCH --time={time}
#SBATCH --partition={partition}
{nodelist}{exclusive}#SBATCH --mem={memory}
#SBATCH --output=orca_output.log
#SBATCH --error=orca_error.log

set -euo pipefail

############################
# Environment / modules
############################
module purge
module load GCC/12.3.0 UCX/1.14.1-GCCcore-12.3.0 PMIx/4.2.4-GCCcore-12.3.0 libevent/2.1.12-GCCcore-12.3.0 hwloc/2.9.1-GCCcore-12.3.0

export PATH=/home/nvt/Software/openmpi-4.1.8-noAVX/openmpi-4.1.8/install/bin:$PATH
export LD_LIBRARY_PATH=/home/nvt/Software/openmpi-4.1.8-noAVX/openmpi-4.1.8/install/lib:$LD_LIBRARY_PATH
export ORCA_PATH=/home/nvt/Software/ORCA_6_1_no_AVX2

# (Optional) OpenMPI env
export OMPI_MCA_btl=self,tcp
export OMPI_MCA_rmaps_base_oversubscribe=1
export OMPI_MCA_plm_rsh_agent=ssh

############################
# I/O config
############################
INPUT="{input_name}"

{workdir_block}

SUBMIT_DIR="$PWD"
RESULTDIR="$PWD"

echo "[INFO] submit dir:  $SUBMIT_DIR"
echo "[INFO] work dir:    $WORKDIR"
echo "[INFO] result dir:  $RESULTDIR"

# Clean up local workdir on any exit
cleanup() {{
  {cleanup_block}
}}
trap cleanup EXIT

############################
# Stage inputs
############################
{staging_block}

scontrol show hostnames "$SLURM_JOB_NODELIST" > "$WORKDIR/$(basename $INPUT .inp).nodes"

############################
# Run ORCA
############################
{cd_block}

echo "[INFO] launching ORCA on $(hostname) at $(date)"
"$ORCA_PATH/orca" "$INPUT" > "${{INPUT%.inp}}.out"

############################
# Copy results back
############################
{copyback_block}

echo "[INFO] done at $(date)"
"""

def extract_resources(inp_file):
    with open(inp_file, 'r') as f:
        lines = f.readlines()

    # Defaults
    nprocs = 32
    maxcore = 4000

    inside_pal = False
    for line in lines:
        l = line.strip().lower()
        if l.startswith('%pal'):
            inside_pal = True
            continue
        if inside_pal:
            if l.startswith('end'):
                inside_pal = False
            elif 'nprocs' in l:
                try:
                    nprocs = int(l.split()[1])
                except (IndexError, ValueError):
                    pass
        if l.startswith('%maxcore'):
            try:
                maxcore = int(l.split()[1])
            except (IndexError, ValueError):
                pass
        if l.startswith('!') and 'pal' in l.lower():
            m = re.search(r'pal\s*(\d+)', l, re.IGNORECASE)
            if m:
                nprocs = int(m.group(1))

    return nprocs, maxcore

def create_slurm(inp_path, args):
    inp_path = Path(inp_path)
    if not inp_path.exists():
        print(f"[ERROR] File not found: {inp_path}")
        return

    nprocs, maxcore = extract_resources(inp_path)
    mem = nprocs * maxcore
    job_name = args.job_name or f"{os.getenv('USER', 'user')}_ORCA_calc"
    slurm_path = inp_path.with_suffix(".slurm")

    # Workdir-dependent blocks
    if args.workdir == "lscratch":
        workdir_block = """# Preferred fast local scratch; fall back to /tmp if needed
if [[ -d /lscratch ]]; then
  WORKDIR="/lscratch/${SLURM_JOB_ID}"
else
  echo "[WARN] /lscratch not found on $(hostname); using /tmp"
  WORKDIR="/tmp/${SLURM_JOB_ID}"
fi
"""
        staging_block = """mkdir -p "$WORKDIR"

shopt -s nullglob
TO_COPY=( *.inp *.gbw *.xyz *.hess *.engrad *.molden.input *.molden *.wfn *.num *.mkl *.trj *.swag *.tmp )
TO_COPY+=( "$INPUT" )

echo "[INFO] staging inputs to $WORKDIR"
for f in "${TO_COPY[@]}"; do
  [[ -e "$f" ]] && rsync -a "$f" "$WORKDIR/"
done
"""
        if args.clean == "standart":
            cleanup_block = """set +e
if [[ -d "$WORKDIR" ]]; then
  echo "[INFO] cleaning up $WORKDIR"
  rm -rf "$WORKDIR"
fi"""
        elif args.clean == "copy_tmp":
            cleanup_block = """set +e
if [[ -d "$WORKDIR" ]]; then
  echo "[INFO] cleaning up $WORKDIR"
  cp * "$RESULTDIR"
  rm -rf "$WORKDIR"
fi"""
        cd_block = 'cd "$WORKDIR"'
        copyback_block = 'echo "[INFO] copying results to $RESULTDIR"\nrsync -a "$WORKDIR"/ "$RESULTDIR"/'
    elif args.workdir == "scratch":
        workdir_block = """# Use shared scratch
WORKDIR="/scratch/${SLURM_JOB_ID}"
mkdir -p "$WORKDIR"
"""
        staging_block = """shopt -s nullglob
TO_COPY=( *.inp *.gbw *.xyz *.hess *.engrad *.molden.input *.molden *.wfn *.num *.mkl *.trj *.swag *.tmp )
TO_COPY+=( "$INPUT" )

echo "[INFO] staging inputs to $WORKDIR"
for f in "${TO_COPY[@]}"; do
  [[ -e "$f" ]] && rsync -a "$f" "$WORKDIR/"
done
"""
        if args.clean == "standart":
            cleanup_block = """set +e
if [[ -d "$WORKDIR" ]]; then
  echo "[INFO] cleaning up $WORKDIR"
  rm -rf "$WORKDIR"
fi"""
        elif args.clean == "copy_tmp":
            cleanup_block = """set +e
if [[ -d "$WORKDIR" ]]; then
  echo "[INFO] cleaning up $WORKDIR"
  cp * "$RESULTDIR"
  rm -rf "$WORKDIR"
fi"""
        cd_block = 'cd "$WORKDIR"'
        copyback_block = 'echo "[INFO] copying results to $RESULTDIR"\nrsync -a "$WORKDIR"/ "$RESULTDIR"/'
    elif args.workdir == "pwd":
        workdir_block = """# Use current directory (no staging)
WORKDIR="$PWD"
"""
        staging_block = 'echo "[INFO] no staging; running in $PWD"'
        cleanup_block = 'echo "[INFO] no cleanup for workdir=pwd"'
        cd_block = 'cd "$WORKDIR"'
        copyback_block = 'echo "[INFO] no copy-back; results already in $PWD"'
    else:
        raise ValueError(f"Unknown workdir option: {args.workdir}")

    slurm_content = TEMPLATE.format(
        job_name=job_name,
        ntasks=nprocs,
        memory=f"{mem}MB",
        exclusive="#SBATCH --exclusive\n" if args.exclusive else "",
        partition=args.partition,
        time=args.time,
        nodelist=f"#SBATCH --nodelist={args.nodelist}\n" if args.nodelist else "",
        input_name=inp_path.name,
        workdir_block=workdir_block,
        staging_block=staging_block,
        cleanup_block=cleanup_block,
        cd_block=cd_block,
        copyback_block=copyback_block,
    )

    slurm_path.write_text(slurm_content)
    print(f"[INFO] Created {slurm_path}")
    if args.submit:
        subprocess.run(["sbatch", str(slurm_path)])
        print(f"[INFO] Submitted {slurm_path}")

def create_all_slurms(args):
    for inp in Path(".").glob("*.inp"):
        create_slurm(inp, args)

def interactive_menu():
    slurms = sorted(Path(".").glob("*.slurm"))
    if not slurms:
        print("No .slurm files found.")
        return
    print("\nSelect SLURM scripts to submit:")
    for i, f in enumerate(slurms):
        print(f"[{i}] {f.name}")
    try:
        idxs = input("Enter comma-separated indices (e.g. 0,2,3): ")
        for i in map(int, idxs.split(",")):
            subprocess.run(["sbatch", str(slurms[i])])
    except Exception as e:
        print(f"[ERROR] {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate SLURM scripts for ORCA.")

    parser.add_argument("--inp", type=str, help="Generate .slurm for one .inp file")
    parser.add_argument("--all", action="store_true", help="Generate .slurm for all .inp files")
    parser.add_argument("--submit", action="store_true", help="Submit jobs after creation")
    parser.add_argument("--menu", action="store_true", help="Interactive menu to submit jobs")

    # Cluster / scheduling args
    parser.add_argument("--nodelist", help="Specific node list, e.g., c1028 or c1028,c1029")
    parser.add_argument("--partition", default="normal", help='Slurm partition (default: "normal")')
    parser.add_argument("--time", default="48:00:00", help='Walltime (default: "48:00:00")')
    parser.add_argument("--job-name", default=None, help='Job name (default: "$USER_ORCA_calc")')
    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument("--exclusive", dest="exclusive", action="store_true", help="Use --exclusive (default)")
    exclusive_group.add_argument("--no-exclusive", dest="exclusive", action="store_false", help="Disable --exclusive")
    parser.set_defaults(exclusive=True)

    # New workdir flag
    parser.add_argument(
        "--workdir",
        choices=["lscratch", "scratch", "pwd"],
        default="lscratch",
        help='Where to run: "lscratch" (default), "scratch", or "pwd" (run-in-place; no staging/cleanup)'
    )

    parser.add_argument(
        "--clean",
        choices=["standart", "copy_tmp"],
        default="copy_tmp",
        help='Standart - cleaning up everything without saving if the job terminated earlier.'
    )

    args = parser.parse_args()

    if args.inp:
        create_slurm(args.inp, args)
    elif args.all:
        create_all_slurms(args)
    elif args.menu:
        interactive_menu()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
