# NanoClaw Agent Container
# Runs Claude Agent SDK in isolated Linux VM with browser automation

FROM node:22-slim

# Install system dependencies for Chromium
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    fonts-noto-color-emoji \
    libgbm1 \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2 \
    libpangocairo-1.0-0 \
    libcups2 \
    libdrm2 \
    libxshmfence1 \
    python3 \
    python3-pip \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set Chromium path for agent-browser
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Install agent-browser and claude-code globally
RUN npm install -g agent-browser @anthropic-ai/claude-code

# Install notebooklm-py (paper analysis via Google NotebookLM)
COPY vendor/notebooklm-py /tmp/notebooklm-py
RUN pip install --break-system-packages /tmp/notebooklm-py && rm -rf /tmp/notebooklm-py \
 && mv "$(command -v notebooklm)" /usr/local/lib/notebooklm-real
# PyMuPDF: the paper scripts read PDFs with it — the structural audit against a
# PDF source, and rendering figures and tables from one. Without it the audit
# printed "PyMuPDF not installed; cannot read PDF source" and every source check
# was skipped, and a PDF-only paper could not get a single figure from here.
# Same version as the host healer and CI, so a crop is the same crop everywhere.
RUN pip install --break-system-packages pymupdf==1.27.2.2

# Serialize concurrent `notebooklm` calls (single shared ~/.notebooklm browser
# profile can't be driven by two processes at once). Wrapper flocks then execs the
# real CLI, so parallel paper subagents queue their asks instead of colliding.
COPY container/bin/notebooklm /usr/local/bin/notebooklm
RUN chmod +x /usr/local/bin/notebooklm

# Create app directory
WORKDIR /app

# Copy package files first for better caching
COPY container/agent-runner/package*.json ./

# Install dependencies
RUN npm install

# Copy source code
COPY container/agent-runner/ ./

# Build TypeScript
RUN npm run build

# Create workspace directories
RUN mkdir -p /workspace/group /workspace/global /workspace/extra /workspace/ipc/messages /workspace/ipc/tasks /workspace/ipc/input

# Create entrypoint script
# Secrets are passed via stdin JSON — temp file is deleted immediately after Node reads it
# Follow-up messages arrive via IPC files in /workspace/ipc/input/
RUN printf '#!/bin/bash\nset -e\ncd /app && npx tsc --outDir /tmp/dist 2>&1 >&2\nln -s /app/node_modules /tmp/dist/node_modules\nchmod -R a-w /tmp/dist\ncat > /tmp/input.json\nnode /tmp/dist/index.js < /tmp/input.json\n' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Set ownership to node user (non-root) for writable directories
RUN chown -R node:node /workspace && chmod 777 /home/node

# Switch to non-root user (required for --dangerously-skip-permissions)
USER node

# Set working directory to group workspace
WORKDIR /workspace/group

# Entry point reads JSON from stdin, outputs JSON to stdout
ENTRYPOINT ["/app/entrypoint.sh"]
