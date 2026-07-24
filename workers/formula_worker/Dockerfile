FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install --break-system-packages -r requirements.txt
# Pix2Text declares the CPU ONNX Runtime package. Replace it explicitly so
# formula recognition can use the NVIDIA execution provider in this worker.
RUN python3 -m pip uninstall --break-system-packages -y onnxruntime \
    && python3 -m pip install --break-system-packages -i https://mirrors.aliyun.com/pypi/simple \
       "onnxruntime-gpu==1.23.2"
COPY app.py .
EXPOSE 8100
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
