# Reproducible environment (Apptainer)

We use [Apptainer](https://apptainer.org/) (formerly Singularity) for the
HeteroRefactor toolchain, since Docker is typically unavailable on HPC nodes.
Vitis HLS itself is **not** containerized (it is licensed and host-installed);
see the top-level README for the host prerequisites.

## 1. HeteroRefactor container

`heterorefactor.def` builds an Ubuntu 22.04 image with the compilers and
libraries HeteroRefactor needs (gcc-9, autotools, MPFR, libhpdf, ...).

```bash
# Build the image (no root needed with --fakeroot on most clusters)
apptainer build --fakeroot containers/heterorefactor.sif containers/heterorefactor.def

# Clone the HeteroRefactor source next to the image, then build the tool inside
# the container (follow HeteroRefactor's own build steps from within an
# `apptainer shell`):
git clone https://github.com/UCLA-VAST/HeteroRefactor "$HETEROREFACTOR_DIR/heterorefactor"
apptainer shell --bind "$HETEROREFACTOR_DIR" containers/heterorefactor.sif
# ... build HeteroRefactor per its README ...
```

Point the flow at the result by setting in `.env`:

```
HETEROREFACTOR_DIR=/abs/path/to/heterorefactor   # contains heterorefactor.sif + heterorefactor/
```

The flow (`flow/tools/heterorefactor.py`) invokes the tool through
`apptainer exec` against `$HETEROREFACTOR_DIR/heterorefactor.sif`. HeteroRefactor
integration is only needed when running with `--hetero_enabled`.

## 2. Python environment

The agentic flow is pure Python and is easiest to run from a conda/venv:

```bash
conda create -n agrefactor python=3.10 -y && conda activate agrefactor
pip install -r requirements.txt
```

If you prefer an Apptainer image for the Python side too, build a minimal
image from `pip install -r requirements.txt` on top of a Python base; nothing
in the flow requires it.
