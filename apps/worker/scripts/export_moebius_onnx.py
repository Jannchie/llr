r"""Export Moebius to the three ONNX graphs erase.py runs.

One-off, and the only place PyTorch is needed — the worker itself runs the
graphs with onnxruntime. Moebius's own code builds the network, so it runs from
a checkout of the upstream repository with its weights in place:

    git clone https://github.com/hustvl/Moebius && cd Moebius
    git checkout b88d462bacb9af6e7128a3b4cc4a07418bedfd61
    # weights (Apache-2.0): hf download hustvl/Moebius ft_places2/diffusion_pytorch_model.bin
    #   --local-dir weight/Moebius ; hf download hustvl/PixelHacker --include "vae/*" --local-dir weight
    uv run --no-project --python 3.12 --with torch --with torchvision --with "diffusers==0.38.0" \
        --with transformers --with accelerate --with safetensors --with timm --with omegaconf --with pyyaml \
        --with einops --with opencv-python --with tqdm --with toml --with orjson --with pandas --with scipy \
        --with tensorboard --with lpips --with elatentlpips --with onnx --with onnxscript \
        python <llr>/apps/worker/scripts/export_moebius_onnx.py . ~/.cache/llr/models/moebius

Nothing here needs triton: the network Moebius ships (λ attention + depthwise
convolutions) is plain PyTorch. Its package imports an unused GLA variant that
pulls in flash-linear-attention, so that import is stubbed out below rather
than installed.

What is exported (fixed 512 x 512, the resolution the model was trained at;
erase.py pads every region to square):

- moebius_unet.onnx  (x[1,9,64,64] = noisy|mask|masked latents, t[1] int64,
  guidance scalar) -> eps[1,4,64,64]. Both classifier-free-guidance branches run
  inside as one batch of two, and the conditioning — a fixed table of learned
  embeddings, ids 0..9 conditional and 10..19 unconditional — is a constant.
- vae_encoder.onnx   image[1,3,512,512] in [-1, 1] -> scaled latent *mean*.
  The reference samples the posterior; the mean makes the fill deterministic.
- vae_decoder.onnx   latent[1,4,64,64] -> image[1,3,512,512] in [0, 1].

fp32 on purpose: an fp16 conversion runs ~25x slower on DirectML (shape ops
fall back to the CPU) and the fp32 UNet is already ~95 ms a step on a 3090.
"""

import os
import sys
import types
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).expanduser().resolve()
weights = sys.argv[3] if len(sys.argv) > 3 else "weight/Moebius/ft_places2/diffusion_pytorch_model.bin"
sys.path.insert(0, str(repo))
os.chdir(repo)


def _placeholder(attr: str) -> type:
    if attr.startswith("__"):  # inspect & co. probe __file__ and friends
        raise AttributeError(attr)
    return type(attr, (), {})


# model_lib/__init__ imports the GLA network too; Moebius's own does not use it.
for name in ("fla", "fla.ops", "fla.ops.gla", "fla.models", "fla.models.gla", "fla.models.gla.modeling_gla",
             "fla.modules"):
    stub = types.ModuleType(name)
    stub.__getattr__ = _placeholder  # type: ignore[method-assign]
    sys.modules.setdefault(name, stub)

import torch  # noqa: E402
from removal.v1_2 import build_removal_model, load_cfg, load_removal_model  # noqa: E402
from utils_train import build_vae  # noqa: E402

SIZE = 512
LAT = SIZE // 8

cfg = load_cfg("config/model_cfg/moebius.yaml")
model = build_removal_model(cfg, 20)
load_removal_model(model, weights, "cpu")
model.eval()
vae = build_vae(cfg).eval()


class Guided(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.diff = m.diff_model
        with torch.no_grad():
            half = m.num_embeddings // 2
            cond = m.embedding_layer(torch.arange(half)[None])
            uncond = m.embedding_layer(torch.arange(half, m.num_embeddings)[None])
        self.register_buffer("ehs", torch.cat([uncond, cond]))

    def forward(self, x, t, guidance):
        eps = self.diff(torch.cat([x, x]), timestep=t, encoder_hidden_states=self.ehs).sample
        un, co = eps.chunk(2)
        return un + guidance * (co - un)


class Enc(torch.nn.Module):
    def __init__(self, v):
        super().__init__()
        self.v = v

    def forward(self, img):
        return self.v.encode(img).latent_dist.mean * self.v.config.scaling_factor


class Dec(torch.nn.Module):
    def __init__(self, v):
        super().__init__()
        self.v = v

    def forward(self, z):
        return ((self.v.decode(z / self.v.config.scaling_factor).sample + 1) / 2).clamp(0, 1)


def export(mod, args, inputs, outputs, name):
    path = out / name
    torch.onnx.export(mod, args, str(path), input_names=inputs, output_names=outputs,
                      opset_version=18, dynamo=False, do_constant_folding=True)
    print("wrote", path, path.stat().st_size // 2**20, "MB")


out.mkdir(parents=True, exist_ok=True)
with torch.no_grad():
    export(Guided(model).eval(), (torch.randn(1, 9, LAT, LAT), torch.tensor([500]), torch.tensor(2.0)),
           ["x", "t", "guidance"], ["eps"], "moebius_unet.onnx")
    export(Enc(vae), (torch.randn(1, 3, SIZE, SIZE),), ["image"], ["latent"], "vae_encoder.onnx")
    export(Dec(vae), (torch.randn(1, 4, LAT, LAT),), ["latent"], ["image"], "vae_decoder.onnx")
