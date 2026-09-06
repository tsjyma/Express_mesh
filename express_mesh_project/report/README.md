# English report

Build the report from the repository root with:

```bash
make -C express_mesh_project/report
```

The LaTeX source references the experiment SVG files directly. The `svg`
package invokes Inkscape during `latexmk --shell-escape`, so the generated PDF
preserves the SVG vector primitives instead of rasterizing each figure as a
whole.

The configurable Garnet runner can be inspected with:

```bash
python3 express_mesh_project/run_phase3_measurement_v2.py --help
python3 express_mesh_project/validate_garnet_placements.py --help
```

The `Garnet_standalone` build option sets `NUMBER_BITS_PER_SET=256`, which is
required by the 256 directory nodes in a 16x16 experiment. Reconfigure an
existing build explicitly when switching from an older 64-bit build:

```bash
PYTHON_LIB="$(python3-config --prefix)/lib"
LD_LIBRARY_PATH="$PYTHON_LIB:${LD_LIBRARY_PATH:-}" \
  scons build/Garnet_standalone/gem5.opt NUMBER_BITS_PER_SET=256 -j8
```

The supplied Python runners add the matching Python library directory to their
child-process environment automatically.

For example, the following command uses an 8x8 topology, dynamic top-8
committed routing, the standard q/r weights, four-bit distance gossip, and
registered reservations:

```bash
python3 express_mesh_project/validate_garnet_placements.py \
  --topologies path/to/placement.json --labels proposed \
  --traffics uniform_random --rate 0.7 --dimension 8 \
  --express-budget 32 --express-max-degree 1 \
  --express-min-wire-length 3 --source-route-candidates 8 \
  --q-weight 1.0 --r-weight 0.6 \
  --express-info-mode distance-gossip --express-info-period 1 \
  --express-info-delay 1 --express-info-bits 4 \
  --express-reservation-mode registered --escape-timeout 32 \
  --output-dir /tmp/express-garnet-check
```
