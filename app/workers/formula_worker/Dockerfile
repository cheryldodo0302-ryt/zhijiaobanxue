FROM zhijiao-mineru:3.4.5

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN printf 'torch==2.7.1\n' > /tmp/torch-constraints.txt \
    && PIP_CONSTRAINT=/tmp/torch-constraints.txt python3 -m pip install --break-system-packages -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
# Use a CUDA 12.x/cuDNN 9 build compatible with the shared CUDA 12.6 runtime.
RUN python3 -m pip uninstall --break-system-packages -y onnxruntime \
    && python3 -m pip install --break-system-packages -i https://mirrors.aliyun.com/pypi/simple \
       "onnxruntime-gpu==1.20.2"
COPY app.py .
EXPOSE 8100
ENTRYPOINT []
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]