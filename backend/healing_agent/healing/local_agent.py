"""Local healing agents that do not require an Anthropic API key."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings
from ..models import Problem
from ..redaction import scrub


@dataclass
class LocalAgentResult:
    attempted: bool
    applied: bool
    summary: str


SYSTEM_PROMPT = (
    "You are a senior engineer repairing a checked-out repository. Return only "
    "a unified git diff beginning with --- a/. Fix the reported defects with "
    "the smallest safe change. Do not change tests, delete functionality, or "
    "include markdown fences."
)


def _task_prompt(problems: list[Problem], root: Path | None = None) -> str:
    defects = "\n".join(
        f"- {problem.file}:{problem.line} {problem.code} "
        f"({problem.severity.value}): {problem.message}"
        for problem in problems
    )
    context = ""
    if root:
        files: list[str] = []
        for problem in problems:
            if problem.file in files:
                continue
            path = root / problem.file
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files.append(problem.file)
            context += f"\n\n### {problem.file}\n{source[:12000]}"
            if len(files) >= 4:
                break
    return (
        "Fix these detected repository defects, then stop.\n\n"
        f"{defects}\n\n"
        "Inspect the files and keep the patch minimal."
        f"\n\nRelevant source files:{context}"
    )


def _extract_diff(text: str) -> str:
    fenced = re.search(r"```(?:diff)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    marker = text.find("--- a/")
    return text[marker:] if marker >= 0 else text


def _apply_diff(root: Path, diff_text: str) -> tuple[bool, str]:
    if not diff_text.strip():
        return False, "The local agent returned an empty patch."
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as handle:
        handle.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
        patch_path = handle.name
    try:
        result = subprocess.run(
            ["git", "apply", "--whitespace=fix", patch_path],
            cwd=str(root), capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def _simple_diff(settings: Settings, root: Path, problems: list[Problem]) -> tuple[bool, str]:
    prompt = _task_prompt(problems, root)
    try:
        response = httpx.post(
            f"{settings.local_llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.local_llm_api_key}"},
            json={
                "model": settings.local_llm_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
            timeout=settings.local_agent_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return _apply_diff(root, _extract_diff(str(content)))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        return False, f"Local LLM request failed: {scrub(str(exc))}"


def _openhands(settings: Settings, root: Path, problems: list[Problem]) -> tuple[bool, str]:
    binary = shutil.which(settings.openhands_bin)
    if not binary:
        return False, f"'{settings.openhands_bin}' was not found on PATH."
    env = {
        **os.environ,
        "LLM_MODEL": settings.local_llm_model,
        "LLM_BASE_URL": settings.local_llm_base_url,
        "LLM_API_KEY": settings.local_llm_api_key,
    }
    try:
        result = subprocess.run(
            [binary, "--headless", "--json", "--override-with-envs", "-t", _task_prompt(problems)],
            cwd=str(root), env=env, capture_output=True, text=True,
            timeout=settings.local_agent_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"OpenHands failed to run: {scrub(str(exc))}"
    detail = (result.stdout or result.stderr or "").strip().splitlines()[-1:]
    return result.returncode == 0, "OpenHands completed" if result.returncode == 0 else (detail[0] if detail else "OpenHands failed")


def run_local_agent(
    settings: Settings, root: Path, problems: list[Problem]
) -> LocalAgentResult:
    """Run OpenHands or a local OpenAI-compatible coding model."""
    if settings.healer_backend == "openhands":
        applied, summary = _openhands(settings, root, problems)
    else:
        applied, summary = _simple_diff(settings, root, problems)
    return LocalAgentResult(attempted=True, applied=applied, summary=summary)
