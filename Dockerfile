# CogSession as a stdio MCP server.
#
# Exists so a directory can start the server and send it an introspection
# request without installing anything. The image is not how CogSession is
# normally used — the hooks that make it record without being asked write to
# the host's Claude Code settings, which a container cannot reach. For real
# use, `pip install cogsession && cogsession-admin install`.
#
# Two constraints shape everything below:
#
#   stdio is the transport. Anything printed to stdout that is not a JSON-RPC
#   message corrupts the stream, which is why PYTHONUNBUFFERED is set and
#   nothing here echoes on start.
#
#   Sessions are written to the working directory, so it has to be writable by
#   the non-root user the container runs as.

FROM python:3.12-slim AS build

# uv gives a reproducible install from uv.lock rather than a fresh resolve.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Build the venv at the path it will live at in the final image. uv bakes an
# absolute interpreter path into each console script's shebang, so a venv built
# at /build and copied to /app produces `exec: no such file or directory` —
# the venv works, the entry point does not, and the error names neither.
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first, as their own layer: they change far less often than the
# source, so an edit to a .py file does not re-resolve the whole tree.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable

COPY cogsession/ ./cogsession/
# --no-editable installs a real copy into site-packages. The default editable
# install writes a .pth pointing back at the source tree, so copying only the
# venv into the runtime stage yields a venv that imports nothing.
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim

# git is a genuine runtime dependency, not a build tool: every journal entry
# records the branch, commit and dirty count it happened at. Without it the
# server still runs and records `no-git`, but the entries lose the thing that
# ties a decision to the state of the code that produced it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The server reads and writes a project directory, and there is no
# reason for it to do so with more rights than the work requires.
RUN useradd --create-home --uid 1000 cogsession

COPY --from=build --chown=cogsession:cogsession /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The session store lands under the working directory. Mount a project here to
# use the container against real code: `docker run -v "$PWD:/workspace" ...`
WORKDIR /workspace
RUN chown cogsession:cogsession /workspace
USER cogsession

# The MCP Registry verifies image ownership by this annotation, so it has to
# match the server name in server.json exactly.
LABEL io.modelcontextprotocol.server.name="io.github.premanand8800/cogsession"
LABEL org.opencontainers.image.source="https://github.com/premanand8800/cogsession"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.description="Session memory for AI coding agents"

# stdio, so no port and no healthcheck: the client speaks JSON-RPC over the
# process's own stdin and stdout.
ENTRYPOINT ["cogsession"]
