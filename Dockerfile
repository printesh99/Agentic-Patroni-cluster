# --- oc CLI source stage ---------------------------------------------------
# The console reads live cluster state by shelling out to a Kubernetes CLI
# (app/sources.py). In-cluster it uses the pod ServiceAccount, so we just need
# the binary. Copy `oc` from the Red Hat ose-cli image (same registry as the
# base, so no extra internet egress / pull secret is needed). Pin a tag if you
# want full reproducibility.
FROM registry.redhat.io/openshift4/ose-cli:latest AS cli

# --- application image ------------------------------------------------------
FROM registry.redhat.io/ubi9/python-312@sha256:7e2b0e62dc436a5059c90cfb5a4e531833991ed26e507f0b962b4738f04e6ea5

WORKDIR /opt/app-root/src

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    CUTOVER_RUN_ROOT=/var/lib/console/cutover_runs \
    # Semantic RAG: load the baked all-MiniLM-L6-v2 model offline (no HF egress
    # in the air-gapped pod). vendor/fastembed_cache is COPYd in below.
    AI_EMBED_CACHE_DIR=/opt/app-root/src/vendor/fastembed_cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    # Uvicorn worker count (tunable at runtime via `oc set env WEB_CONCURRENCY=N`).
    # 3 workers lets concurrent dashboard / /metrics / AI requests use the pod's
    # 4-CPU burst limit instead of head-of-line blocking on one event loop.
    WEB_CONCURRENCY=3 \
    # Cap ONNX/OpenMP threads per worker so 3 workers don't oversubscribe the
    # 4-CPU limit (each embedding inference stays single-threaded; plenty fast
    # for the small KB, and avoids CPU-throttle thrash).
    OMP_NUM_THREADS=1

# Kubernetes CLI for live reads (oc, plus a kubectl alias).
COPY --from=cli /usr/bin/oc /usr/local/bin/oc

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY tools ./tools
COPY infra ./infra
COPY *.sh ./
# Baked embedding model (~87MB) for air-gapped semantic RAG.
COPY vendor ./vendor

# OpenShift restricted SCC runs with an arbitrary UID in group 0. Keep runtime
# paths group-readable/writable without requiring a fixed container user.
# Run as root to create /var/lib/console (outside the app-root the build user
# owns), then drop back to the UBI default user (1001) for runtime.
USER 0
RUN mkdir -p "${CUTOVER_RUN_ROOT}" \
    && ln -sf /usr/local/bin/oc /usr/local/bin/kubectl \
    && chmod 0755 /usr/local/bin/oc \
    && chmod 0755 /opt/app-root/src/*.sh \
    && chgrp -R 0 /opt/app-root/src /var/lib/console \
    && chmod -R g+rwX /opt/app-root/src /var/lib/console
USER 1001

EXPOSE 8080

# Shell form + `exec` so uvicorn becomes PID 1 (clean SIGTERM on Recreate) while
# still expanding ${WEB_CONCURRENCY} from the env (defaults to 3, see above).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers ${WEB_CONCURRENCY:-3}"]
