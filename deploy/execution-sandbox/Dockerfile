# syntax=docker/dockerfile:1
# rebuild execution sandbox (Phase C2 scaffold).
#
# Purpose: a hardened, disposable image in which agent/OpenCode-generated code is
# executed in ISOLATION from the backend and host. Today the backend's
# opencode_adapter runs code as a host subprocess behind a deny-list; moving that
# execution here is the security-core of the containerization plan and maps to the
# platform's L4/L5 execution-risk levels.
#
# Hardening applied at RUN-time via compose (see docker-compose.yml sandbox service):
#   read_only rootfs + tmpfs workdir, no inbound network, dropped caps, non-root,
#   cpu/mem/pids limits. This image only provides the runtime + a non-root user.

FROM python:3.12-slim AS runtime

# Minimal toolchain only. Do NOT add git/ssh/network tools here — the sandbox
# must not be able to reach code hosts or remotes.
RUN useradd --create-home --uid 10002 sandboxuser
USER sandboxuser
WORKDIR /work

# No long-running server: the backend execs `python3 -c <code>` (or a bash
# fallback) inside an ephemeral container/exec. Keep the container alive when run
# standalone for inspection; real execution overrides the command.
CMD ["python3", "-c", "print('rebuild execution-sandbox ready')"]
