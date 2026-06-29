# Backend image. Build context is the repository root.
#
# The five engine packages are not on a public index, so they are vendored into
# backend/engines_vendor/ by scripts/vendor-engines.sh before the build and
# installed here. This keeps the control plane composing the real engines while
# the image stays self-contained and air-gappable.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ACP_CONFIG=/config/config.json

WORKDIR /app

COPY backend/engines_vendor/ /engines/
RUN pip install \
    /engines/cost-governor \
    /engines/mcp-gateway \
    /engines/mcp-siem-bridge \
    /engines/agent-flight-recorder \
    /engines/agent-eval

COPY backend/ /app/
RUN pip install .

EXPOSE 8800
CMD ["control-plane", "serve"]
