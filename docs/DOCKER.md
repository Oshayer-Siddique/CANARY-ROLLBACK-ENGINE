# Docker application guide

Related: [Phase 2 issue history and safeguards](troubleshooting/PHASE_2_DOCKER.md).

This phase packages the existing application into two local release images.
Traffic splitting and automatic rollback require the later Kubernetes/controller
phases. For application behavior, see [the application guide](APPLICATION.md).

## Build

Docker Engine/Desktop must be running with Linux containers. Check `docker version`
for both client and server. Run these commands from the repository root:

```bash
docker build --build-arg APP_VERSION=v1 -t application:v1 .
docker build --build-arg APP_VERSION=v2 -t application:v2 .
```

The first build needs Docker Hub and Python package repository access. The second
reuses common layers. Both images default to zero failures. A tag does not set
application identity: use the matching build argument (default: v1).

Images remain local; no registry push is performed. The Python base tag and
dependency ranges are mutable, so builds are not fully locked. Use `--pull` when
deliberately refreshing the base. Digest/dependency pinning can be added later.

## Run both releases

```bash
docker run --detach --name canary-mvp-stable \
  --publish 127.0.0.1:8000:8000 application:v1

docker run --detach --name canary-mvp-canary \
  --publish 127.0.0.1:8001:8000 --env FAILURE_RATE=30 application:v2
```

Open [stable demo](http://127.0.0.1:8000/demo) and
[canary demo](http://127.0.0.1:8001/demo). Both containers listen on internal port
8000, with different host ports. Stop earlier local servers or change the host
ports if occupied. Host bindings are localhost-only.

Each page currently calls only its own server; there is no common router yet.
Use `FAILURE_RATE=100` for guaranteed failure or 0 for a healthy canary. Change
settings by recreating the container; rebuilding the image is not necessary.

## Dockerfile design

The builder uses `python:3.12-slim-bookworm`, creates `/opt/venv`, and installs
the package with runtime dependencies only. The HTML asset is included in the
installed package. The final stage copies the virtual environment, runs UID/GID
10001, and starts Uvicorn directly with an exec-form command.

Uvicorn uses the app factory, one worker, `0.0.0.0:8000`, and a 10-second graceful
shutdown timeout. Python output is unbuffered; bytecode writes are disabled.
Code resides in installed site-packages and needs no repository bind mount.
The Dockerfile exposes port 8000; `--publish` is still required for host access.

The `.dockerignore` allowlist sends only packaging/application inputs to the
builder. Local environments, Git data, tests, docs, and caches are excluded.
The structure follows Docker's [multi-stage build guidance](https://docs.docker.com/build/building/best-practices/).

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| APP_VERSION | Build argument | Version in responses and metrics |
| FAILURE_RATE | 0 | Business request failure probability, 0–100 percent |
| INSTANCE_ID | Container hostname | Response/log instance identifier |

Pass settings with `--env NAME=value`. Keep APP_VERSION consistent with the image
tag. Runtime overrides do not change the image's version label. Host `.env` files
are not included; explicitly use `--env-file` if needed. Running containers do
not notice changes to host environment variables or files.

## Inspect behavior

```bash
curl -i http://127.0.0.1:8000/
curl -i http://127.0.0.1:8001/
curl -i http://127.0.0.1:8001/health
curl -i http://127.0.0.1:8001/ready
curl http://127.0.0.1:8001/metrics
docker inspect --format '{{.State.Health.Status}}' canary-mvp-canary
docker logs --tail 30 canary-mvp-canary
docker exec canary-mvp-canary id
```

Docker healthchecks call `/ready` every 10 seconds, with a 3-second Docker timeout,
a 2-second HTTP timeout, a 10-second startup grace period, and three retries.
They use Python's standard library; curl is not needed inside the image.

Even a 100%-failing synthetic release should report healthy: readiness does not
depend on business failures. Docker health status does not perform rollback or
automatically restart a standalone container. Kubernetes will need explicit
probe configuration; Dockerfile healthchecks do not replace it.

Metrics remain in memory and reset when the process restarts. Probe/scrape calls
do not inflate business metrics. Logs are available through `docker logs`.

## Repeatable verification

After building both images:

```bash
python3 scripts/verify_docker.py
```

The optional verifier requires host Python 3 and Docker, but no Python packages.
It checks healthy v1, healthy v2, and fully failing v2; release identity; exact
metrics; probes; packaged HTML; Docker health; request logs; and clean SIGTERM
shutdown. Containers run non-root with a read-only filesystem, all capabilities
dropped, and privilege escalation disabled.

It uses random localhost ports and removes only containers it creates, including
on failure. It preserves your existing containers and the built images.
Running the application images themselves does not require Python on the host.

## Stop and remove manually started demo containers

For the containers created by the run commands above:

```bash
docker stop --time 15 canary-mvp-stable canary-mvp-canary
docker rm canary-mvp-stable canary-mvp-canary
```

Removal discards those containers' logs and writable layers. Images remain;
there are no application data volumes. Avoid broad Docker prune commands.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Docker daemon unavailable | Start Docker and check `docker version` |
| Docker socket permission denied | Use your authorized Docker access setup |
| Image download fails | Check registry/network access and rebuild |
| Name already exists | Inspect the existing container before reusing that name |
| Port occupied | Stop your earlier server or select another host port |
| Immediate exit | Read logs; check configuration validation errors |
| Health status starting | Allow the first scheduled successful readiness check |
| HTTP 500 but healthy | Expected for simulated failures; inspect metrics |
| Wrong version | Check build argument and runtime overrides |
| No mixed versions on one page | Expected before ingress integration |

Use one worker per container and scrape each pod independently when scaling in
Kubernetes. Prometheus multiprocess mode is not configured. The next phase is
deploying these images in a local k3d cluster.

## Detailed operational notes

Docker packages the same FastAPI source into two independently runnable release artifacts. The image runs one stateless application replica; it does not split traffic, query Prometheus, change Kubernetes resources, or perform rollback. Those responsibilities belong to later phases.

The `APP_VERSION` build argument sets the default environment value and image label. Keep the build argument, image tag, and runtime value aligned:

```text
application:v1 -> APP_VERSION=v1 -> responses and metrics say v1
application:v2 -> APP_VERSION=v2 -> responses and metrics say v2
```

`FAILURE_RATE` is runtime-only. The same v2 image can be healthy, probabilistic, or deterministic without another build:

```bash
docker run --rm -e FAILURE_RATE=0 application:v2
docker run --rm -e FAILURE_RATE=30 application:v2
docker run --rm -e FAILURE_RATE=100 application:v2
```

The Dockerfile's builder stage creates `/opt/venv` and installs runtime dependencies. The runtime stage starts from a fresh slim Python image and copies only that virtual environment. Tests, Ruff, documentation, Git metadata, local environments, caches, and host `.env` files are excluded by `.dockerignore`. The packaged `demo.html` is included through `pyproject.toml` package data.

At startup the image runs Uvicorn with the application factory, one worker, and `0.0.0.0:8000`. The process loads settings, creates an in-memory metrics registry, serves the five application routes, and marks `/ready` successful when its lifespan starts. Internal port 8000 is mapped to host ports 8000 and 8001 for stable and canary. Each demo currently calls only its own origin because there is no shared ingress router yet.

The healthcheck calls `/ready` every 10 seconds, with a 3-second Docker timeout, 2-second Python HTTP timeout, 10-second startup grace period, and 3 retries. It checks process readiness rather than canary quality. A process with `FAILURE_RATE=100` can therefore return HTTP 500 for business requests while remaining Docker-healthy, allowing the future Prometheus decision engine to observe it. Docker health does not change traffic or roll back releases.

The runtime image uses UID/GID 10001, has no login shell, and does not need a writable application directory. The verifier additionally uses a read-only filesystem, drops all capabilities, and disables privilege escalation. One worker is deliberate because metrics are process-local; scale with Kubernetes replicas and scrape each pod independently.

`scripts/verify_docker.py` starts temporary containers on random localhost ports. For healthy v1, healthy v2, and v2 at 100% failure it checks readiness, UID, version responses, exact request/error counters, health, demo packaging, Docker health status, request logs, and clean SIGTERM shutdown. It removes only the temporary containers it created and leaves images and existing containers alone. It does not test 90/10 routing, Prometheus history, load volume, or rollback.

The Docker phase is complete when both images build from the shared Dockerfile, run without host Python, preserve application behavior, support runtime failure simulation, expose probes/metrics/demo, run non-root, pass healthchecks, and shut down cleanly. These checks have passed in this workspace. Docker alone does not prove traffic splitting, a 60-second canary error window, the 5% threshold, or automatic restoration to v1; k3d/Kubernetes is the next phase.
