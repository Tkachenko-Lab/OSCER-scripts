# Tkachenko Lab HPC Helpers
Utilities for students to work with Slurm and ORCA quickly: **job_status**, **orca_mkinput**, and **orca_submit**.

- **`job_status`** — student‑friendly Slurm job viewer; supports `--watch`, `--history`, `--nodeinfo NODE`, an SSH helper `--ssh-menu`, and an interactive cancel menu `--cancel-menu`.
- **`orca_mkinput`** — generates ORCA `.inp` files from `.xyz` with common prerequisites (method, basis, solvent, parallel, memory). Includes multi‑step `%compound` and NEB calculations.
- **`orca_submit`** — produces a Slurm script for an ORCA input and (optionally) submits it. Has useful options like `--workdir {lscratch|scratch|pwd}`, partition/time, exclusive mode, etc.

---

## Quick install (7 steps)
### 1) Create a `bin` folder in your home
```bash
mkdir -p ~/bin
```

### 2) Copy the three scripts into `~/bin`
From the repo directory (adjust paths if your scripts are in a different folder, or drag and drop if you are using WinSCP):
```bash
cp job_status.py orca_mkinput.py orca_submit.py ~/bin/
cd ~/bin
```

### 3) Normalize line endings
```bash
dos2unix job_status.py orca_mkinput.py orca_submit.py
```

### 4) Create short names without the `.py` suffix
```bash
ln -sf ~/bin/job_status.py   ~/bin/job_status
ln -sf ~/bin/orca_mkinput.py ~/bin/orca_mkinput
ln -sf ~/bin/orca_submit.py  ~/bin/orca_submit
```

### 5) Make the scripts executable
```bash
chmod +x ~/bin/job_status.py ~/bin/orca_mkinput.py ~/bin/orca_submit.py
```

### 6) Put `~/bin` on your PATH
For **bash** users:
```bash
grep -q 'export PATH="$HOME/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 7) Test the commands
```bash
job_status --help
orca_mkinput --help
orca_submit --help
```

---

## Usage — quick examples

### View your jobs and open the cancel menu
```bash
job_status --watch 3      # refresh every 3s
job_status --cancel-menu  # interactive cancel
```

### Generate a TDDFT input (30 states) in water
Make sure you have mol.xyz in the folder from which you are running **`orca_mkinput`** command
```bash
orca_mkinput --xyz mol.xyz --job tddft --nstates 30 --cpcm water --method wB97X-D4 --basis def2-TZVPPD --pal 32 --maxcore-mb 4000
```

### Create + submit a Slurm script for ORCA (run in node-local scratch)
```bash
orca_submit --inp mol.inp --workdir lscratch --partition normal --time 48:00:00 --submit
```

### Work directory options
- `--workdir lscratch` *(default)* — node‑local fast scratch; falls back to `/tmp` if `/lscratch` is missing
- `--workdir scratch` — shared scratch, quite slow I/O
- `--workdir pwd` — run in the current directory (no staging/cleanup)

