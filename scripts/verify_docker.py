"""Verify local images; remove only temporary containers created by this script."""

import json
import subprocess
import time
import urllib.error
import urllib.request


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def request(base, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(base + path, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read().decode()


def verify(image, version, failure_rate, expected_status):
    container = docker(
        "run",
        "--detach",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--publish",
        "127.0.0.1::8000",
        "--env",
        f"FAILURE_RATE={failure_rate}",
        image,
    )
    try:
        info = json.loads(docker("inspect", container))[0]
        port = info["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                if request(base, "/ready")[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("Container did not become ready")

        require(docker("exec", container, "id", "-u") == "10001", "Wrong runtime UID")
        for _ in range(20):
            status, body = request(base, "/")
            require(status == expected_status, f"Unexpected HTTP status: {status}")
            payload = json.loads(body)
            require(payload["version"] == version, "Image default version mismatch")
            require(bool(payload["instance"]), "Missing instance identity")
        for path in ("/health", "/ready", "/demo"):
            status, body = request(base, path)
            require(status == 200, f"Failed endpoint: {path}")
            if path == "/demo":
                require("Start requests" in body, "Missing packaged demo asset")
        _, metrics = request(base, "/metrics")
        expected = (
            f'requests_total{{method="GET",route="/",status="{expected_status}",'
            f'version="{version}"}} 20.0'
        )
        require(expected in metrics, "Business request count mismatch")
        errors = 20 if expected_status == 500 else 0
        require(
            f'http_errors_total{{route="/",version="{version}"}} {errors}.0' in metrics,
            "Error counter mismatch",
        )
        for _ in range(40):
            health = docker("inspect", "--format", "{{.State.Health.Status}}", container)
            if health == "healthy":
                break
            time.sleep(0.5)
        require(health == "healthy", "Docker healthcheck failed")
        docker("stop", "--time", "15", container)
        require(
            docker("inspect", "--format", "{{.State.ExitCode}}", container) == "0",
            "Container did not shut down cleanly",
        )
        require("application_request" in docker("logs", container), "Missing request logs")
        print(
            f"PASS {image}: failure_rate={failure_rate}, HTTP {expected_status}, "
            "metrics, probes, asset, non-root, read-only, healthcheck, shutdown",
            flush=True,
        )
    except Exception:
        subprocess.run(["docker", "logs", "--tail", "30", container], check=False)
        raise
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container], check=True, stdout=subprocess.DEVNULL
        )


if __name__ == "__main__":
    verify("application:v1", "v1", 0, 200)
    verify("application:v2", "v2", 0, 200)
    verify("application:v2", "v2", 100, 500)
