ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY train_text_to_image_lora_sdxl.py /opt/xuntui-sdxl-artifact/train_text_to_image_lora_sdxl.py
COPY manifest.json /opt/xuntui-sdxl-artifact/manifest.json
COPY verify_sdxl_image_context.py /opt/xuntui-sdxl-artifact/verify_sdxl_image_context.py

RUN python /opt/xuntui-sdxl-artifact/verify_sdxl_image_context.py /opt/xuntui-sdxl-artifact \
    && mkdir -p /opt/diffusers/examples/text_to_image \
    && cp /opt/xuntui-sdxl-artifact/train_text_to_image_lora_sdxl.py /opt/diffusers/examples/text_to_image/train_text_to_image_lora_sdxl.py
