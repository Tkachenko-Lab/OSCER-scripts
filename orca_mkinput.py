#!/usr/bin/env python3
"""
orca_mkinput.py - ORCA input generator for tkachenko lab use.


Features:
- Generate .inp from a single .xyz OR from all .xyz in a folder.
- Defaults to DEFGRID3 and TightSCF (safe/standard).
- Job kinds: sp, opt, freq, optfreq, optts, tddft (with --nstates), neb.
- Optional solvation: CPCM(--cpcm <solvent>) or SMD(--smd <solvent>).
- %compound mode via numbered options: --job1/--method1/--basis1/--extra1/--moinp1,
  --job2/... etc. If any numbered option is present, %compound is used.
- Each compound step gets its own "! ..." line. Coordinates are referenced once (first step).

Examples
--------
Single OPT+FREQ:
  orca_mkinput.py --xyz mol.xyz --method wB97X-D4 --basis def2-TZVPPD --job optfreq

TDDFT 30 states in water (CPCM):
  orca_mkinput.py --xyz mol.xyz --job tddft --nstates 30 --cpcm water

Batch over folder:
  orca_mkinput.py --folder ./xyzs --pattern "*.xyz" --job opt --method wB97X-D4 --basis def2-TZVP

Two-step %compound:
  orca_mkinput.py --xyz mol.xyz --job1 optfreq --method1 wB97X-D4 --basis1 def2-SVPD --smd1 water --extra1 "SlowConv defgrid3 tightSCF" --moinp1 mol_guess.gbw --job2 sp --method2 wB97M-V --basis2 def2-TZVPPD --smd2 water --extra2 "SlowConv defgrid3 tightSCF" --pal 32 --maxcore-mb 2000 --pal-style bang

NEB:
    orca_mkinput.py --xyz reactant.xyz --job nebts --neb-product product.xyz --neb-ts guessTS.xyz --method "PBEh-3c" --extra FREQ --charge 0 --mult 1 --name neb_ts --overwrite
"""

import argparse
import glob
import os
import sys
from pathlib import Path

# ----------------------------- Utilities -------------------------------------

def _parse_extras(extra_list):
    if not extra_list:
        return []
    toks = []
    for item in extra_list:
        for t in item.replace(",", " ").split():
            t = t.strip()
            if t:
                toks.append(t)
    return toks

def _join_bang_tokens(tokens):
    return "! " + " ".join([t for t in tokens if t])

JOB_MAP = {
    "sp": [],
    "opt": ["Opt"],
    "freq": ["Freq"],
    "optfreq": ["Opt", "Freq"],
    "optts": ["OptTS"],
    "tddft": [],
    "neb": ["NEB"],       
    "nebci": ["NEB-CI"],  
    "nebts": ["NEB-TS"],  
}


def _make_bang_line(method, basis, job, grid_token, cpcm, smd, extra_tokens, force_tokens=None):
    toks = []
    if method:
        toks.append(method)
    if basis and basis not in ("None", "-", "NA"):
        toks.append(basis)
    if grid_token:
        toks.append(grid_token)
    # Default-tightSCF only if user didn’t explicitly add tightSCF/LooseSCF in extras
    if not any(t.lower().startswith(("tightscf", "loosescf")) for t in extra_tokens):
        toks.append("TightSCF")
    toks += JOB_MAP.get(job, [])
    if cpcm:
        toks.append(f"CPCM({cpcm})")
    if smd:
        toks.append(f"SMD({smd})")
    toks += extra_tokens
    if force_tokens:
        toks += force_tokens
    return _join_bang_tokens(toks)

def _pal_block(pal):  # %pal style
    return f"%pal\n  nprocs {pal}\nend"

def _maxcore_block(mb):
    return f"%MaxCore {mb}"

def _scf_block(maxiter=300):
    return f"%scf\n  MaxIter {maxiter}\nend"

def _tddft_block(nroots):
    return f"%tddft\n  nroots {nroots}\nend"

def _file_exists(p):
    return p and Path(p).exists()

def _read_text_file(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"extra block file not found: {path}")
    # Preserve text verbatim; trim only trailing newlines for clean joins
    text = p.read_text(encoding="utf-8")
    return text.rstrip() + "\n"

def _collect_global_extra_blocks(args):
    blocks = []
    if args.extra_block:
        for f in args.extra_block:
            blocks.append(_read_text_file(f))
    return blocks  # list[str]

def _collect_step_extra_blocks(args):
    """For %compound only: {step_index -> [block_text, ...]}"""
    by_step = {}
    for k in range(1, 5):
        lst = getattr(args, f"extra_block{k}", None)
        if lst:
            by_step[k] = [ _read_text_file(f) for f in lst ]
    return by_step  # dict[int, list[str]]

# ----------------------------- Writers ---------------------------------------

def write_single_step(outpath, xyzfile, charge, mult, method, basis, job, grid, cpcm, smd,
                      extra, pal, maxcore_mb, nstates, moinp,
                      pal_style="block", extra_blocks=None):
    """
    Normal (non-%compound) input.
    """
    extra_tokens = _parse_extras(extra)
    if moinp and not any(t.lower() == "moread" for t in extra_tokens):
        extra_tokens.append("moread")
    header = _make_bang_line(method, basis, job, grid, cpcm, smd, extra_tokens)

    blocks = []
    if pal_style == "bang":
        header = header + f"\n\n! PAL{pal}"
    else:
        blocks.append(_pal_block(pal))
    blocks.append(_maxcore_block(maxcore_mb))

    if job == "tddft":
        blocks.append(_tddft_block(nstates))
    # Always give SCF block with a decent MaxIter:
    blocks.append(_scf_block())

    content = []
    content.append(header)
    if blocks:
        content.append("\n".join(blocks))
    if moinp:
        content.append(f'%moinp "{moinp}"')   # must precede the * xyzfile   
    if extra_blocks:
        content.append("\n".join(extra_blocks))
        
    content.append(f"\n* xyzfile {charge} {mult} {xyzfile}\n\n")

    Path(outpath).parent.mkdir(parents=True, exist_ok=True)
    with open(outpath, "w") as f:
        f.write("\n\n".join(content))
    print(f"[OK] Wrote {outpath}")


def write_compound(outpath, xyzfile, charge, mult, steps, pal, maxcore_mb,
                   pal_style="bang", global_extra_blocks=None, step_extra_blocks=None):
    """
    %compound with one or more steps.
    Each step: dict with keys {method,basis,job,grid,cpcm,smd,extra_tokens,moinp}.
    The first step references coordinates; subsequent steps just declare the job.
    """
    # Global header like your example:
    header_lines = []
    if pal_style == "bang":
        header_lines.append(f"! PAL{pal}")
    elif pal_style == "block":
        header_lines.append(f"%pal\n  nprocs {pal}\nend")
    header_lines.append(f"%MaxCore {maxcore_mb}")
    header = "\n".join(header_lines).strip()

    if global_extra_blocks:
        header_global = "\n".join(global_extra_blocks)

    comp = []
    comp.append("%compound")
    for i, st in enumerate(steps, start=1):
        comp.append("New_step")
        extra_tokens = st["extra_tokens"].copy()
        if st.get("moinp") and not any(tok.lower() == "moread" for tok in extra_tokens):
            extra_tokens.append("moread")
            bang = _make_bang_line(st["method"], st["basis"], st["job"], st["grid"], st["cpcm"], st["smd"], extra_tokens)
        else:
            bang = _make_bang_line(st["method"], st["basis"], st["job"], st["grid"], st["cpcm"], st["smd"], st["extra_tokens"])
        comp.append(bang)
        if st.get("moinp"):
            comp.append(f'%moinp "{st["moinp"]}"')
        if st.get("job") == "tddft":
            comp.append(_tddft_block(st["nstates"]))
        comp.append(_scf_block())
        # Inject step-specific blocks (if any)
        if step_extra_blocks and i in step_extra_blocks:
            comp.append("\n".join(step_extra_blocks[i]).rstrip())
        # First step carries coordinates:
        if i == 1:
            comp.append(f"* xyzfile {charge} {mult} {xyzfile}\n")
        comp.append("Step_end\n")
    comp.append("EndRun")
    body = "\n".join(comp)

    Path(outpath).parent.mkdir(parents=True, exist_ok=True)
    with open(outpath, "w") as f:
        if header:
            f.write(header + "\n\n")
        if global_extra_blocks:
            f.write("\n".join(global_extra_blocks).rstrip() + "\n\n")
        f.write(body + "\n")
    print(f"[OK] Wrote %compound input: {outpath}")


def write_neb_input(outdir, name, mode, method, basis, grid, cpcm, smd, extra,
                            reactant_xyz, product_xyz, ts_xyz,
                            charge, mult, pal, maxcore_mb,
                            pal_style="block",
                            springconst=0.01, maxiter=200, nimages=10):
    """
    Minimal NEB input referencing only reactant/product (and optional TS) — NO images.
    Emits:
      ! <method> <basis> [NEB/NEB-CI/NEB-TS] [Freq] [grids, solvents, extras]
      %NEB
        NEB_END_XYZFILE "product.xyz"
        [NEB_TS_XYZFILE "guessTS.xyz"]
        SpringConst <val>
        MAXITER <val>
      end
      * xyzfile Q M reactant.xyz
    """
    extra_tokens = _parse_extras(extra)
    job_key = {"neb":"neb", "neb-ci":"nebci", "neb-ts":"nebts"}[mode]
    header = _make_bang_line(method, basis, job_key, grid, cpcm, smd, extra_tokens)

    blocks = []
    if pal_style == "bang":
        header = header + f"\n\n! PAL{pal}"
    else:
        blocks.append(_pal_block(pal))
    blocks.append(_maxcore_block(maxcore_mb))
    blocks.append(_scf_block())

    neb_lines = ["%NEB", f'  NEB_END_XYZFILE "{product_xyz}"']
    if ts_xyz:
        neb_lines.append(f'  NEB_TS_XYZFILE "{ts_xyz}"')
    neb_lines.append(f"  SpringConst {springconst}")
    neb_lines.append(f"  MAXITER {maxiter}")
    neb_lines.append(f"  NImages {nimages}")
    neb_lines.append("end")

    chunks = [
        header,
        "\n".join(blocks),
        "\n".join(neb_lines),
        f"* xyzfile {charge} {mult} {reactant_xyz}\n",
    ]

    outdir = Path(outdir) if outdir else Path.cwd()
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"{name}.inp"
    with open(outpath, "w") as f:
        f.write("\n\n".join([c for c in chunks if c]) + "\n")
    print(f"[OK] Wrote minimal {mode.upper()} input: {outpath}")



# ----------------------------- CLI / Main ------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Generate ORCA .inp (single, %%compound, NEB). For compound scripts add an index to the flag (e.g. --job1 XX, --method1 XX)")

    src = p.add_mutually_exclusive_group(required=False)
    src.add_argument("--xyz", type=str, help=f"Input XYZ file (for single or %%compound).")
    src.add_argument("--folder", type=str, help="Folder with XYZ files (batch single-step only).")

    p.add_argument("--outdir", type=str, help="Directory to write outputs (default: CWD or folder).")
    p.add_argument("--pattern", type=str, default="*.xyz", help="Glob pattern for --folder (default: *.xyz)")
    p.add_argument("--name", type=str, help="Output basename (without extension). Default: xyz stem")

    p.add_argument("--charge", type=int, default=0, help="Molecular charge (default: 0)")
    p.add_argument("--mult", type=int, default=1, help="Spin multiplicity (default: 1)")

    # Unnumbered = single-step fallback
    p.add_argument("--method", type=str, help="DFT/ab initio method (e.g. wB97X-D4)")
    p.add_argument("--basis", type=str, help="Basis (e.g. def2-TZVPPD)")
    p.add_argument("--job", choices=["sp","opt","freq","optfreq","optts","tddft","neb","nebts","nebci"],help="Job type")
    p.add_argument("--nstates", type=int, default=10, help="TDDFT nroots (single-step only)")
    p.add_argument("--cpcm", type=str, help="CPCM solvent (e.g. water)")
    p.add_argument("--smd", type=str, help="SMD solvent (e.g. water)")
    p.add_argument("--grid", type=str, default="DEFGRID3", help="Grid token (default: DEFGRID3)")
    p.add_argument("--moinp", type=str, help="Read MOs from GBW for single-step jobs (adds %%moinp and MOREAD).")
    p.add_argument("--extra", action="append", help="Extra tokens for the '!' line (comma/space-separated).")
    p.add_argument("--extra-block", action="append",help="Path to a text file whose contents are injected globally.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing .inp")
    # Compound steps: up to 4 for convenience
    for k in range(1, 5):
        p.add_argument(f"--job{k}", choices=["sp","opt","freq","optfreq","optts","tddft"], help=argparse.SUPPRESS)# help=f"Step {k} job")
        p.add_argument(f"--method{k}", type=str,  help=argparse.SUPPRESS)# help=f"Step {k} method")
        p.add_argument(f"--basis{k}", type=str,  help=argparse.SUPPRESS)# help=f"Step {k} basis")
        p.add_argument(f"--nstates{k}", type=int, default=10, help=argparse.SUPPRESS)# help=f"TDDFT nroots for step {k}")
        p.add_argument(f"--grid{k}", type=str,  help=argparse.SUPPRESS)# help=f"Step {k} grid (default: inherit global)")
        p.add_argument(f"--cpcm{k}", type=str,  help=argparse.SUPPRESS)# help=f"Step {k} CPCM solvent")
        p.add_argument(f"--smd{k}", type=str,  help=argparse.SUPPRESS)# help=f"Step {k} SMD solvent")
        p.add_argument(f"--extra{k}", action="append",  help=argparse.SUPPRESS)# help=f"Step {k} extra tokens for the '!' line")
        p.add_argument(f"--moinp{k}", type=str,  help=argparse.SUPPRESS)# help=f'Step {k} %%moinp file (e.g., "something.gbw")')
        p.add_argument(f"--extra-block{k}", action="append",  help=argparse.SUPPRESS)# help=f'Path(s) to text file(s) injected inside step {k} (compound).')

    # Parallel/memory: choose %pal/%MaxCore or shorthand
    p.add_argument("--pal", type=int, default=32, help="Number of cores (default: 32)")
    p.add_argument("--maxcore-mb", type=int, default=4000, help="MaxCore per core in MB (default: 4000)")
    p.add_argument("--pal-style", choices=["block","bang"], default="block",
                   help='Parallel style: "block" -> %%pal, "bang" -> ! PALN (default: block)')

    # NEB
    neb = p.add_argument_group("NEB options (reactant/product, optional TS)")
    neb.add_argument("--neb-mode", choices=["neb", "neb-ci", "neb-ts"],
                     help="NEB variant: NEB, NEB-CI, or NEB-TS. If omitted but --job is neb/nebci/nebts, it follows --job.")
    neb.add_argument("--neb-product", type=str, help="Product geometry XYZ (for %%NEB NEB_END_XYZFILE).")
    neb.add_argument("--neb-ts", type=str, help="Optional TS guess XYZ (for %%NEB NEB_TS_XYZFILE).")
    neb.add_argument("--neb-springconst", type=float, default=0.01, help="Spring constant (default: 0.1)")
    neb.add_argument("--neb-maxiter", type=int, default=200, help="Max NEB iterations (default: 200)")
    neb.add_argument("--nimages", type=int, default=10, help="Number of images without fixed endpoints in NEB (default: 10)")

    return p.parse_args()

def _collect_compound_steps(args):
    steps = []
    for k in range(1, 5):
        if any(getattr(args, f"{field}{k}") is not None for field in ["job","method","basis","grid","cpcm","smd","extra","moinp"]):
            st = {
                "job": getattr(args, f"job{k}", None) or "sp",
                "method": getattr(args, f"method{k}", None) or args.method or "wB97X-D4",
                "basis": getattr(args, f"basis{k}", None) or args.basis or "def2-SVPD",
                "grid": getattr(args, f"grid{k}", None) or args.grid,
                "cpcm": getattr(args, f"cpcm{k}", None),
                "smd": getattr(args, f"smd{k}", None),
                "extra_tokens": _parse_extras(getattr(args, f"extra{k}", None)),
                "moinp": getattr(args, f"moinp{k}", None),
                "nstates": getattr(args, f"nstates{k}", None),
            }
            steps.append(st)
    return steps

def _any_numbered_options_present(args):
    for k in range(1, 5):
        for field in ["job","method","basis","grid","cpcm","smd","extra","moinp"]:
            if getattr(args, f"{field}{k}", None) is not None:
                return True
    return False

def _resolve_name_outdir(xyzpath, args):
    xyz = Path(xyzpath) if xyzpath else None
    stem = args.name or (xyz.stem if xyz else "orca_job")
    outdir = Path(args.outdir) if args.outdir else (xyz.parent if xyz else Path.cwd())
    outdir.mkdir(parents=True, exist_ok=True)
    return stem, outdir

def _load_neb_list(path):
    with open(path, "r") as f:
        return [line.strip() for line in f if line.strip()]

def main():
    args = parse_args()

    # Mode: NEB 
    if args.job in ("neb", "nebci", "nebts") or args.neb_mode:
        mode = args.neb_mode or ("neb" if args.job == "neb" else "neb-ci" if args.job == "nebci" else "neb-ts")
        if not args.xyz:
            print("[ERR] NEB requires --xyz for the reactant structure.", file=sys.stderr); return 1
        if not args.neb_product:
            print("[ERR] NEB requires --neb-product <product.xyz>.", file=sys.stderr); return 1
        reactant = Path(args.xyz)
        product = args.neb_product
        ts_guess = args.neb_ts

        name = args.name or mode
        outdir = Path(args.outdir) if args.outdir else Path.cwd()
        if (outdir / f"{name}.inp").exists() and not args.overwrite:
            print(f"[SKIP] {(outdir / (name+'.inp'))} exists. Use --overwrite to replace.", file=sys.stderr)
            return 0

        write_neb_input(
            outdir=outdir,
            name=name,
            mode=mode,
            method=(args.method or "r2SCAN-3c"),
            basis=(args.basis or None),
            grid=args.grid,
            cpcm=args.cpcm,
            smd=args.smd,
            extra=args.extra,
            reactant_xyz=reactant.name,
            product_xyz=product,
            ts_xyz=ts_guess,
            charge=args.charge,
            mult=args.mult,
            pal=args.pal,
            maxcore_mb=args.maxcore_mb,
            pal_style=args.pal_style,
            springconst=args.neb_springconst,
            maxiter=args.neb_maxiter,
            nimages=args.nimages,
        )
        return 0

    if args.folder and args.name:
        print("[ERR] --name cannot be used with --folder (would overwrite files).", file=sys.stderr)
        return 1

    if args.folder:
        files = sorted(Path(args.folder).glob(args.pattern))
        if not files:
            print(f"[WARN] No files matched {args.pattern} in {args.folder}", file=sys.stderr)
            return 0

        # Precompute step/global blocks once (applies to every file in the folder)
        compound_mode = _any_numbered_options_present(args)
        global_blocks = _collect_global_extra_blocks(args)
        step_blocks   = _collect_step_extra_blocks(args) if compound_mode else None
        steps         = _collect_compound_steps(args) if compound_mode else None

        rc = 0
        for xyz in files:
            stem = args.name or xyz.stem
            outdir = Path(args.outdir) if args.outdir else xyz.parent
            outpath = outdir / f"{stem}.inp"

            if outpath.exists() and not args.overwrite:
                print(f"[SKIP] {outpath} exists. Use --overwrite to replace.", file=sys.stderr)
                continue

            if compound_mode:
                write_compound(
                    outpath=outpath,
                    xyzfile=xyz.name,
                    charge=args.charge, mult=args.mult,
                    steps=steps,
                    pal=args.pal, maxcore_mb=args.maxcore_mb,
                    pal_style=args.pal_style,
                    global_extra_blocks=global_blocks,
                    step_extra_blocks=step_blocks,
                )
            else:
                write_single_step(
                    outpath=outpath,
                    xyzfile=xyz.name,
                    charge=args.charge, mult=args.mult,
                    method=(args.method or "wB97X-D4"),
                    basis=(args.basis or "def2-TZVPPD"),
                    job=(args.job or "sp"),
                    grid=args.grid, cpcm=args.cpcm, smd=args.smd, extra=args.extra,
                    pal=args.pal, maxcore_mb=args.maxcore_mb, nstates=args.nstates,
                    moinp=args.moinp, pal_style=args.pal_style,
                    extra_blocks=global_blocks,
                )
        return rc


    # Single file modes (%compound or single-step)
    if not args.xyz:
        print("[ERR] Provide --xyz (or --folder) unless using --job neb.", file=sys.stderr)
        return 1
    xyz = Path(args.xyz)
    if not xyz.exists():
        print(f"[ERR] XYZ not found: {xyz}", file=sys.stderr)
        return 1
    stem, outdir = _resolve_name_outdir(xyz, args)
    outpath = outdir / f"{stem}.inp"
    if outpath.exists() and not args.overwrite:
        print(f"[SKIP] {outpath} exists. Use --overwrite to replace.", file=sys.stderr)
        return 0

    # Determine if we’re in compound mode:
    if _any_numbered_options_present(args):
        steps = _collect_compound_steps(args)
        if not steps:
            print("[ERR] Numbered options detected but no valid steps assembled.", file=sys.stderr)
            return 1
        global_blocks = _collect_global_extra_blocks(args)
        step_blocks = _collect_step_extra_blocks(args)
        write_compound(
            outpath=outpath,
            xyzfile=xyz.name,
            charge=args.charge, mult=args.mult,
            steps=steps,
            pal=args.pal, maxcore_mb=args.maxcore_mb,
            pal_style=args.pal_style,
            global_extra_blocks=global_blocks,
            step_extra_blocks=step_blocks,
        )
        return 0

    # Otherwise: single-step fallback (if user gave at least a job or basis/method)
    if not (args.job or args.method or args.basis):
        print("[ERR] No job/method/basis provided. Give --job and optionally --method/--basis, or numbered step flags.", file=sys.stderr)
        return 1
    global_blocks = _collect_global_extra_blocks(args)
    write_single_step(
        outpath=outpath,
        xyzfile=xyz.name,
        charge=args.charge, mult=args.mult,
        method=(args.method or "wB97X-D4"),
        basis=(args.basis or "def2-TZVPPD"),
        job=(args.job or "sp"),
        grid=args.grid, cpcm=args.cpcm, smd=args.smd, extra=args.extra,
        pal=args.pal, maxcore_mb=args.maxcore_mb, nstates=args.nstates,
        moinp=args.moinp, pal_style=args.pal_style,
        extra_blocks=global_blocks,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
