# Flood models

`cnn_v2/` is the deployable hydrodynamic inference package.

- `CNN_V2.py`: model architecture and inference entry point.
- `GT.txt`: hydrodynamic mesh definition.
- `weights/FLOOD_CNN.pth`: model checkpoint stored with Git LFS.
- `weights/FLOOD_CNN.json`: checkpoint metadata and normalization summary.
- `TIME.txt`, `CANSHU.txt`, `EPSG.txt`: model configuration and provenance.

Training and reference boundary templates are archived locally under
`local/reference_data/flood/cnn_v2/` and are not runtime dependencies.
