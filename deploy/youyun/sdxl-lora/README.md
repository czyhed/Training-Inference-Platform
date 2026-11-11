# Youyun SDXL LoRA image context

This context contains the pinned Diffusers `v0.30.3` SDXL LoRA training script.
Build it on a host with a verified PyTorch/Diffusers base image:

```powershell
docker build --build-arg BASE_IMAGE=<verified-pytorch-diffusers-image> -t xuntui/sdxl-lora:v0.30.3 .
```

The build verifies the commit, size, and SHA-256 before installing the script at
`/opt/diffusers/examples/text_to_image/train_text_to_image_lora_sdxl.py`. Push or
import this image into the Youyun image registry, then run the guarded provider
probe before selecting its `CompShareImageId`. This repository cannot publish a
custom Youyun image automatically and the image must contain no credentials or
model weights.
