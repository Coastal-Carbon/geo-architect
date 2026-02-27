#!/usr/bin/env python3
"""Librarian end-to-end test harness.

Spawns a querying agent per dataset that consults the librarian subagent,
reads recipe files, and attempts to download one sample. Optionally runs
a judge agent to score each conversation.

Usage:
    python harness/run.py                             # run all 21 tests
    python harness/run.py --only sentinel-2-l2a naip  # run specific tests
    python harness/run.py --skip-commercial            # skip 8 commercial datasets
    python harness/run.py --parallel 1                 # sequential (debug)
    python harness/run.py --skip-judge                 # skip judge scoring
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

# Support running as both `python harness/run.py` and `python -m harness.run`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import (
    COMMERCIAL_DATASET_IDS,
    DEFAULT_PARALLEL,
    DEFAULT_TIMEOUT_SECONDS,
    HARNESS_DIR,
    JUDGE_PROMPT,
    LIBRARIAN_PROJECT_DIR,
    OUTCOME_TIMEOUT,
    QUERYING_AGENT_PROMPT,
    RESULTS_DIR,
    extract_conversation_text,
    generate_transcript,
)

# ---------------------------------------------------------------------------
# Environment for subprocess calls
# ---------------------------------------------------------------------------


def _claude_subprocess_env() -> dict[str, str]:
    """Return an env dict safe for spawning nested claude processes.

    Claude Code sets a ``CLAUDECODE`` env var and refuses to start inside
    another session. We strip that (and related vars) so our subprocess
    invocations work.
    """
    env = os.environ.copy()
    for key in ("CLAUDECODE", "CLAUDE_CODE_SESSION"):
        env.pop(key, None)
    return env


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------


def load_scenarios(path: Path) -> list[dict]:
    """Load test scenarios from the YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["scenarios"]


def filter_scenarios(
    scenarios: list[dict],
    only: list[str] | None = None,
    exclude: list[str] | None = None,
    skip_commercial: bool = False,
) -> list[dict]:
    """Filter scenarios by --only, --exclude, and --skip-commercial flags."""
    filtered = scenarios

    if only:
        only_set = set(only)
        filtered = [s for s in filtered if s["id"] in only_set]

    if exclude:
        exclude_set = set(exclude)
        filtered = [s for s in filtered if s["id"] not in exclude_set]

    if skip_commercial:
        filtered = [s for s in filtered if s["id"] not in COMMERCIAL_DATASET_IDS]

    return filtered


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------


def check_claude_cli() -> bool:
    """Verify the claude CLI is available."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            print(f"  claude CLI: {result.stdout.strip()}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print("  claude CLI: NOT FOUND")
    return False


def check_aws_credentials() -> bool:
    """Check if AWS credentials are configured (needed for STAC access)."""
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            identity = json.loads(result.stdout)
            print(f"  AWS identity: {identity.get('Arn', 'unknown')}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    print("  AWS credentials: NOT CONFIGURED (some tests may fail with AUTH_FAILURE)")
    return False


def check_librarian_project() -> bool:
    """Verify the librarian project directory exists with expected structure."""
    agent_file = LIBRARIAN_PROJECT_DIR / ".claude" / "agents" / "geospatial-librarian.md"
    index_file = LIBRARIAN_PROJECT_DIR / "datasets" / "index.yaml"

    if not LIBRARIAN_PROJECT_DIR.exists():
        print(f"  Librarian project: NOT FOUND at {LIBRARIAN_PROJECT_DIR}")
        return False

    if not agent_file.exists():
        print(f"  Librarian agent def: NOT FOUND at {agent_file}")
        return False

    if not index_file.exists():
        print(f"  Dataset index: NOT FOUND at {index_file}")
        return False

    print(f"  Librarian project: {LIBRARIAN_PROJECT_DIR}")
    return True


def run_preflight() -> bool:
    """Run all preflight checks. Returns True if critical checks pass."""
    print("Preflight checks:")
    claude_ok = check_claude_cli()
    check_aws_credentials()  # non-critical, just informational
    librarian_ok = check_librarian_project()
    print()

    if not claude_ok:
        print("FATAL: claude CLI not found. Install Claude Code first.")
        return False
    if not librarian_ok:
        print("FATAL: Librarian project not found or incomplete.")
        return False

    return True


# ---------------------------------------------------------------------------
# Build the prompt for a single test
# ---------------------------------------------------------------------------


def build_test_prompt(scenario: dict, download_dir: Path) -> str:
    """Build the full prompt for the querying agent for a single test.

    Combines the system prompt template with scenario-specific details.
    """
    system_prompt = QUERYING_AGENT_PROMPT.read_text()

    # Replace placeholder in system prompt
    system_prompt = system_prompt.replace("{DOWNLOAD_DIR}", str(download_dir))

    prompt = f"""{system_prompt}

---

## Your Test Assignment

**Dataset:** {scenario['name']} (`{scenario['id']}`)
**Access pattern:** {scenario['access_pattern']}
**Question to ask the librarian:**

{scenario['question'].strip()}

**Download directory:** `{download_dir}`

Begin by consulting the librarian subagent with the question above. Then read the recipe files and attempt a download.
"""
    return prompt


# ---------------------------------------------------------------------------
# Run a single test
# ---------------------------------------------------------------------------


def run_single_test(
    scenario: dict,
    run_dir: Path,
) -> dict:
    """Run a single test scenario. Returns a status dict."""
    dataset_id = scenario["id"]
    test_dir = run_dir / dataset_id
    download_dir = test_dir / "download"
    download_dir.mkdir(parents=True, exist_ok=True)

    conversation_json = test_dir / "conversation.json"
    conversation_md = test_dir / "conversation.md"
    transcript_md = test_dir / "transcript.md"
    status_file = test_dir / "status.json"
    timeout = scenario.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)

    prompt = build_test_prompt(scenario, download_dir)

    print(f"  [{dataset_id}] Starting test (timeout: {timeout}s)...")
    start_time = time.time()

    try:
        result = subprocess.run(
            [
                "claude",
                "-p", prompt,
                "--output-format", "stream-json",
                "--verbose",
                "--dangerously-skip-permissions",
                "--max-turns", "30",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(LIBRARIAN_PROJECT_DIR),
            env=_claude_subprocess_env(),
        )

        elapsed = time.time() - start_time

        # Write the raw stream-json output
        conversation_json.write_text(result.stdout)

        # Extract compact transcript (conversation.md)
        try:
            transcript = extract_conversation_text(conversation_json)
            conversation_md.write_text(transcript)
        except Exception as e:
            conversation_md.write_text(f"Error extracting transcript: {e}\n\nRaw stderr:\n{result.stderr}")

        # Generate rich human-readable transcript (transcript.md)
        try:
            rich_transcript = generate_transcript(
                conversation_json,
                dataset_name=scenario["name"],
                dataset_id=dataset_id,
                outcome="(pending)",  # updated below once status is known
                elapsed=elapsed,
            )
            transcript_md.write_text(rich_transcript)
        except Exception as e:
            transcript_md.write_text(f"Error generating transcript: {e}")

        # Check if the querying agent wrote a status.json
        if not status_file.exists():
            # Agent didn't write status — create one from what we know
            status = {
                "dataset_id": dataset_id,
                "outcome": "EXECUTION_ERROR",
                "outcome_detail": "Querying agent did not produce a status.json",
                "files_downloaded": [],
                "error_message": result.stderr[-2000:] if result.stderr else None,
                "elapsed_seconds": round(elapsed, 1),
                "exit_code": result.returncode,
            }
            status_file.write_text(json.dumps(status, indent=2))
        else:
            # Agent wrote status — augment it with timing
            status = json.loads(status_file.read_text())
            status["elapsed_seconds"] = round(elapsed, 1)
            status["exit_code"] = result.returncode
            status_file.write_text(json.dumps(status, indent=2))

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        status = {
            "dataset_id": dataset_id,
            "outcome": OUTCOME_TIMEOUT,
            "outcome_detail": f"Test timed out after {timeout}s",
            "files_downloaded": [],
            "error_message": None,
            "elapsed_seconds": round(elapsed, 1),
            "exit_code": -1,
        }
        status_file.write_text(json.dumps(status, indent=2))
        conversation_md.write_text(f"Test timed out after {timeout} seconds.")
        print(f"  [{dataset_id}] TIMEOUT after {timeout}s")

    except Exception as e:
        elapsed = time.time() - start_time
        status = {
            "dataset_id": dataset_id,
            "outcome": "EXECUTION_ERROR",
            "outcome_detail": f"Orchestrator error: {e}",
            "files_downloaded": [],
            "error_message": str(e),
            "elapsed_seconds": round(elapsed, 1),
            "exit_code": -1,
        }
        status_file.write_text(json.dumps(status, indent=2))
        print(f"  [{dataset_id}] ERROR: {e}")

    print(f"  [{dataset_id}] Done in {status['elapsed_seconds']}s — {status['outcome']}")

    # Regenerate transcript with final outcome
    if conversation_json.exists() and conversation_json.stat().st_size > 0:
        try:
            rich_transcript = generate_transcript(
                conversation_json,
                dataset_name=scenario["name"],
                dataset_id=dataset_id,
                outcome=status.get("outcome", "UNKNOWN"),
                elapsed=status.get("elapsed_seconds"),
            )
            transcript_md.write_text(rich_transcript)
        except Exception:
            pass  # non-critical — compact transcript already exists

    return status


# ---------------------------------------------------------------------------
# Judge a single test
# ---------------------------------------------------------------------------


def run_judge(
    scenario: dict,
    run_dir: Path,
) -> dict | None:
    """Run the judge agent on a completed test. Returns scores dict or None."""
    dataset_id = scenario["id"]
    test_dir = run_dir / dataset_id
    conversation_md = test_dir / "conversation.md"
    status_file = test_dir / "status.json"
    judge_report = test_dir / "judge_report.json"

    if not conversation_md.exists():
        print(f"  [{dataset_id}] No conversation to judge, skipping")
        return None

    transcript = conversation_md.read_text()
    status_data = json.loads(status_file.read_text()) if status_file.exists() else {}

    # Build recipe file paths for context
    recipe_md = LIBRARIAN_PROJECT_DIR / "datasets" / "recipes" / f"{dataset_id}.md"
    recipe_py = LIBRARIAN_PROJECT_DIR / "datasets" / "recipes" / f"{dataset_id}.py"
    recipe_context = ""
    if recipe_md.exists():
        recipe_context += f"\n\n## Recipe Guide ({dataset_id}.md)\n\n{recipe_md.read_text()}"
    if recipe_py.exists():
        recipe_context += f"\n\n## Recipe Code ({dataset_id}.py)\n\n{recipe_py.read_text()}"

    judge_system = JUDGE_PROMPT.read_text()

    prompt = f"""{judge_system}

---

## Test Details

**Dataset ID:** {dataset_id}
**Dataset Name:** {scenario['name']}
**Access Pattern:** {scenario['access_pattern']}
**Expected Outcome:** {scenario['expected_outcome']}

## Status.json (test outcome)

```json
{json.dumps(status_data, indent=2)}
```

## Conversation Transcript

{transcript}

{recipe_context}

---

Now evaluate this test and return your JSON scoring. Return ONLY the JSON object, no other text.
"""

    print(f"  [{dataset_id}] Running judge...")

    try:
        result = subprocess.run(
            [
                "claude",
                "-p", prompt,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                "--max-turns", "3",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(LIBRARIAN_PROJECT_DIR),
            env=_claude_subprocess_env(),
        )

        # Extract JSON from judge response
        response = result.stdout.strip()
        # Try to find JSON in the response
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            judge_data = json.loads(response[json_start:json_end])
            judge_report.write_text(json.dumps(judge_data, indent=2))
            print(f"  [{dataset_id}] Judge complete")
            return judge_data
        else:
            print(f"  [{dataset_id}] Judge returned non-JSON response")
            judge_report.write_text(json.dumps({"error": "non-json response", "raw": response[:500]}, indent=2))
            return None

    except subprocess.TimeoutExpired:
        print(f"  [{dataset_id}] Judge timed out")
        return None
    except (json.JSONDecodeError, Exception) as e:
        print(f"  [{dataset_id}] Judge error: {e}")
        return None


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------


def generate_summary(
    scenarios: list[dict],
    run_dir: Path,
    skip_judge: bool = False,
) -> dict:
    """Generate a summary report from all test results."""
    summary = {
        "run_id": run_dir.name,
        "timestamp": datetime.now().isoformat(),
        "total_tests": len(scenarios),
        "results": [],
    }

    successes = 0
    expected_failures_correct = 0
    unexpected_failures = 0

    for scenario in scenarios:
        dataset_id = scenario["id"]
        status_file = run_dir / dataset_id / "status.json"
        judge_file = run_dir / dataset_id / "judge_report.json"

        result_entry = {
            "dataset_id": dataset_id,
            "name": scenario["name"],
            "expected_outcome": scenario["expected_outcome"],
        }

        if status_file.exists():
            status = json.loads(status_file.read_text())
            result_entry["actual_outcome"] = status.get("outcome", "UNKNOWN")
            result_entry["elapsed_seconds"] = status.get("elapsed_seconds")

            # Classify result
            actual = status.get("outcome", "")
            expected = scenario["expected_outcome"]

            if expected == "success" and actual == "SUCCESS":
                result_entry["verdict"] = "PASS"
                successes += 1
            elif expected == "expected_failure" and actual in ("AUTH_FAILURE", "NO_DATA", "EXECUTION_ERROR"):
                result_entry["verdict"] = "EXPECTED_FAIL"
                expected_failures_correct += 1
            else:
                result_entry["verdict"] = "FAIL"
                unexpected_failures += 1
        else:
            result_entry["actual_outcome"] = "NO_STATUS"
            result_entry["verdict"] = "FAIL"
            unexpected_failures += 1

        if not skip_judge and judge_file.exists():
            try:
                judge_data = json.loads(judge_file.read_text())
                result_entry["judge_scores"] = judge_data.get("scores")
                result_entry["judge_summary"] = judge_data.get("summary")
            except json.JSONDecodeError:
                pass

        summary["results"].append(result_entry)

    summary["successes"] = successes
    summary["expected_failures_correct"] = expected_failures_correct
    summary["unexpected_failures"] = unexpected_failures

    # Write JSON summary
    summary_json = run_dir / "summary.json"
    summary_json.write_text(json.dumps(summary, indent=2))

    # Write markdown summary
    summary_md = run_dir / "summary.md"
    md_lines = [
        f"# Test Run: {run_dir.name}",
        f"",
        f"**Date:** {summary['timestamp']}",
        f"**Tests:** {summary['total_tests']}",
        f"**Passed:** {successes}",
        f"**Expected failures (correctly identified):** {expected_failures_correct}",
        f"**Unexpected failures:** {unexpected_failures}",
        f"",
        f"## Results",
        f"",
        f"| Dataset | Expected | Actual | Verdict | Time |",
        f"|---------|----------|--------|---------|------|",
    ]

    for r in summary["results"]:
        verdict_icon = {"PASS": "OK", "EXPECTED_FAIL": "~", "FAIL": "FAIL"}.get(r.get("verdict", "?"), "?")
        elapsed = f"{r.get('elapsed_seconds', '?')}s"
        md_lines.append(
            f"| {r['dataset_id']} | {r['expected_outcome']} | {r.get('actual_outcome', '?')} | {verdict_icon} | {elapsed} |"
        )

    if not skip_judge:
        # Add judge score averages
        all_scores: dict[str, list[int]] = {}
        for r in summary["results"]:
            scores = r.get("judge_scores")
            if scores:
                for dim, val in scores.items():
                    all_scores.setdefault(dim, []).append(val)

        if all_scores:
            md_lines.extend([
                "",
                "## Average Judge Scores",
                "",
                "| Dimension | Avg | Min | Max |",
                "|-----------|-----|-----|-----|",
            ])
            for dim, vals in sorted(all_scores.items()):
                avg = sum(vals) / len(vals)
                md_lines.append(f"| {dim} | {avg:.1f} | {min(vals)} | {max(vals)} |")

    md_lines.append("")
    summary_md.write_text("\n".join(md_lines))

    print(f"\nSummary written to:")
    print(f"  {summary_json}")
    print(f"  {summary_md}")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Librarian end-to-end test harness",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="ID",
        help="Run only these dataset IDs",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        metavar="ID",
        help="Exclude these dataset IDs",
    )
    parser.add_argument(
        "--skip-commercial",
        action="store_true",
        help="Skip the 8 commercial datasets (expected failures)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL,
        metavar="N",
        help=f"Number of parallel tests (default: {DEFAULT_PARALLEL})",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip the judge scoring phase",
    )
    args = parser.parse_args()

    # Preflight
    if not run_preflight():
        sys.exit(1)

    # Load and filter scenarios
    scenarios = load_scenarios(HARNESS_DIR / "scenarios.yaml")
    scenarios = filter_scenarios(
        scenarios,
        only=args.only,
        exclude=args.exclude,
        skip_commercial=args.skip_commercial,
    )

    if not scenarios:
        print("No scenarios to run after filtering.")
        sys.exit(0)

    print(f"Running {len(scenarios)} test(s) with parallelism={args.parallel}\n")

    # Create run directory
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results directory: {run_dir}\n")

    # Phase 1: Run tests
    print("=" * 60)
    print("Phase 1: Running tests")
    print("=" * 60)

    statuses: dict[str, dict] = {}

    if args.parallel == 1:
        # Sequential execution (debug mode)
        for scenario in scenarios:
            status = run_single_test(scenario, run_dir)
            statuses[scenario["id"]] = status
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(run_single_test, scenario, run_dir): scenario
                for scenario in scenarios
            }
            for future in as_completed(futures):
                scenario = futures[future]
                try:
                    status = future.result()
                    statuses[scenario["id"]] = status
                except Exception as e:
                    print(f"  [{scenario['id']}] Executor error: {e}")
                    statuses[scenario["id"]] = {
                        "dataset_id": scenario["id"],
                        "outcome": "EXECUTION_ERROR",
                        "outcome_detail": f"Executor error: {e}",
                    }

    # Phase 2: Judge
    if not args.skip_judge:
        print()
        print("=" * 60)
        print("Phase 2: Running judge")
        print("=" * 60)

        # Judge runs sequentially to avoid overwhelming the API
        for scenario in scenarios:
            run_judge(scenario, run_dir)
    else:
        print("\nSkipping judge phase (--skip-judge)")

    # Phase 3: Summary
    print()
    print("=" * 60)
    print("Phase 3: Summary")
    print("=" * 60)

    summary = generate_summary(scenarios, run_dir, skip_judge=args.skip_judge)

    # Print quick results
    print(f"\n  Passed: {summary['successes']}")
    print(f"  Expected failures: {summary['expected_failures_correct']}")
    print(f"  Unexpected failures: {summary['unexpected_failures']}")
    print()


if __name__ == "__main__":
    main()
