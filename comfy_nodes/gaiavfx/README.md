# GAIAVFX ComfyUI nodes

The maintained node pack for the GAIAVFX movie workspace.
Copy this folder to `ComfyUI/custom_nodes/gaiavfx` and restart ComfyUI.
Use the Python environment that runs ComfyUI for optional dependencies.
Install them with `python -m pip install -r requirements.txt`.
Without InsightFace or ONNX Runtime, seven core nodes remain available.
Face scanning and face checking require both packages.
Install `buffalo_l` under `models/insightface/models/buffalo_l`.
Required files: `det_10g.onnx`, `w600k_r50.onnx`, `2d106det.onnx`.
The pack never downloads models automatically.
`GET /gaiavfx/health` reports registered nodes, dependencies and model files.
H3 nodes provide audio safety, scheduling, LoRA loading, masking and trimming.
Geometry nodes save set and still geometry for the application.
Licensed GPL-3.0-or-later; the adapted trim retains its MIT notice in `licenses/`.
