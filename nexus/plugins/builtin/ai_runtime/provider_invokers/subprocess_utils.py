import subprocess


def run_cli_prompt(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=cwd,
    )


def wrap_timeout_error(
    exc: subprocess.TimeoutExpired, *, provider_name: str, timeout: int
) -> Exception:
    return Exception(f"{provider_name} analysis timed out (>{timeout}s)")
