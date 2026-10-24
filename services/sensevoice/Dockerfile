FROM python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8
ARG SENSEVOICE_REVISION=586bbdb2b052f4fe19322581825228b6810a9fb3
LABEL org.opencontainers.image.source="https://github.com/QwenAudio/SenseVoice" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/sensevoice
RUN git init && git remote add origin https://github.com/QwenAudio/SenseVoice.git \
    && git fetch --depth 1 origin ${SENSEVOICE_REVISION} && git checkout --detach FETCH_HEAD \
    && rm -rf .git
# A matched CPU pair; never resolve the upstream CUDA image/dependency set.
RUN pip install --no-cache-dir torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
COPY services/sensevoice/requirements.txt /tmp/asr-requirements.txt
COPY services/sensevoice/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/asr-requirements.txt && pip freeze > /opt/asr-installed.txt
COPY services/sensevoice/server.py /opt/sensevoice/bank_voice_server.py
ENV PYTHONUNBUFFERED=1 HF_HOME=/models/huggingface OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
    SENSEVOICE_MODEL_REVISION=3847d57b6bdf2dd8875cb1508d2af43d80a16bf7
EXPOSE 50000
CMD ["uvicorn", "bank_voice_server:app", "--host", "0.0.0.0", "--port", "50000", "--no-access-log"]
