"""Small rules over structured inputs, without parsing logs or taking actions.

Confidence values are fixed rule indicators, not statistical probabilities or
confidence in a particular root cause. No root cause is inferred from an exit
code alone. Evidence IDs are local to a single diagnosis call.
"""

from collections.abc import Sequence
from pathlib import Path

from .models import (
    DetectionResult,
    DiagnosisResult,
    EnvironmentInfo,
    Evidence,
    ExecutionResult,
    ProjectInfo,
    RepairAction,
    RepairPlan,
    RootCauseStep,
    VerificationStep,
)


def diagnose(
    project: ProjectInfo,
    detection: DetectionResult,
    environment: EnvironmentInfo,
    executions: Sequence[ExecutionResult] = (),
    *, python_requirement: Evidence | None = None,
) -> tuple[list[DiagnosisResult], list[Evidence]]:
    """Return (diagnoses, evidence), preserving positive and blocked outcomes too.

    execution:N evidence refers to executions[N] in the caller's input sequence.
    Detection/environment evidence refers to the corresponding supplied objects.
    This function does not inspect files, start processes, or mutate inputs.
    """
    diagnoses: list[DiagnosisResult] = []
    evidence = [
        Evidence(
            evidence_id="detection:python", kind="detection", source="python_plugin",
            associated_id="detection", location=str(project.root_path),
            summary=f"level={detection.level}; matched_files={detection.matched_files!r}",
        ),
        Evidence(
            evidence_id="environment:python", kind="environment", source="inspection",
            associated_id="environment",
            location=str(environment.python_executable) if environment.python_executable else None,
            summary=(f"available={environment.python_available}; "
                     f"executable={environment.python_executable}; "
                     f"version={environment.python_version}; "
                     f"callable={environment.python_callable}"),
        ),
    ]

    if detection.level == "unknown":
        diagnoses.append(DiagnosisResult(
            problem="The shallow scan found no Python project markers",
            category="project_detection", severity="WARNING", confidence=0.5,
            source="rule:python_detection_unknown", evidence_refs=("detection:python",),
            recommended_actions=(
                "Check that the selected directory is the intended project root",
                "Check whether Python files are below the shallow scan depth",
                "Check for common Python project files",
            ),
            diagnosis_id="python_detection_unknown",
            root_cause_chain=(RootCauseStep(
                "detection:no_markers", "Shallow scan found no Python markers",
                "The selected root's shallow scan did not identify Python filenames; deeper files are not ruled out.",
                ("detection:python",),
            ),),
            repair_plan=RepairPlan(
                id="preview:python_detection_unknown", diagnosis_id="python_detection_unknown",
                summary="Review the selected project root and Python file locations", risk="LOW",
                actions=(RepairAction(
                    "detection:review_root", "Confirm the intended project root and inspect whether Python files are deeper than the supported scan.",
                    (str(project.root_path),), True, True,
                ),),
                verification_steps=(VerificationStep(
                    "detection:rescan", "Rescan the confirmed root and review actual matched Python filenames within the supported depth.",
                    "file_check",
                ),),
            ),
        ))

    if (
        not environment.python_available
        or environment.python_executable is None
        or environment.python_callable is False
    ):
        diagnoses.append(DiagnosisResult(
            problem="RepoRescue cannot use the current Python environment for further diagnosis",
            category="environment", severity="ERROR", confidence=0.9,
            source="rule:python_environment_unavailable", evidence_refs=("environment:python",),
            recommended_actions=(
                "Check the reported Python executable path and access permissions",
            ),
            diagnosis_id="python_environment_unavailable",
            root_cause_chain=(
                RootCauseStep(
                    "environment:unavailable", "Current Python environment is unavailable",
                    "Inspection reports an unavailable executable, no executable path, or an explicitly unsuccessful launch check.",
                    ("environment:python",),
                ),
                RootCauseStep(
                    "environment:unvalidated", "A usable Python environment has not been established",
                    "This evidence does not establish an interpreter usable for diagnosis; the underlying reason is not established.",
                    ("environment:python",),
                ),
            ),
            repair_plan=RepairPlan(
                id="preview:python_environment_unavailable", diagnosis_id="python_environment_unavailable",
                summary="Review the interpreter and choose an existing intended Python environment", risk="LOW",
                actions=(RepairAction(
                    "environment:review_interpreter", "Check the reported interpreter path and permissions; for a future run, start RepoRescue with an existing intended interpreter.",
                    (str(environment.python_executable),) if environment.python_executable else (), True, True,
                ),),
                verification_steps=(
                    VerificationStep(
                        "environment:confirm_selection", "Confirm the selected interpreter is the intended project environment.", "manual",
                    ),
                    VerificationStep(
                        "environment:verify_version", "The selected existing Python interpreter's --version must complete with exit code 0.", "command",
                    ),
                ),
            ),
        ))

    for index, execution in enumerate(executions):
        reference = f"execution:{index}"
        evidence.append(Evidence(
            evidence_id=reference, kind="execution", source="executor",
            associated_id=reference, location=str(execution.command.working_directory),
            summary=(f"status={execution.status}; exit_code={execution.exit_code}; "
                     f"timed_out={execution.timed_out}; "
                     f"duration_seconds={execution.duration_seconds}; "
                     f"executable={execution.command.executable}; "
                     f"arguments={execution.command.arguments!r}"),
        ))
        if execution.status == "rejected":
            diagnoses.append(DiagnosisResult(
                problem="RepoRescue safety policy blocked a diagnostic command",
                category="tool/safety", severity="INFO", confidence=0.9,
                source="rule:command_rejected", evidence_refs=(reference,),
                recommended_actions=(
                    "Review the executor rejection reason and allowed command/work-directory constraints",
                ),
                diagnosis_id=f"command_rejected:{index}",
            ))
            continue
        if execution.status == "requires_confirmation":
            continue

        # The executor's current supported operation, identified without I/O.
        is_version_probe = (
            environment.python_executable is not None
            and Path(execution.command.executable) == environment.python_executable
            and execution.command.arguments == ("--version",)
            and execution.command.risk == "SAFE"
        )
        if not is_version_probe:
            continue
        if (
            execution.status in ("failed", "timeout")
            or execution.timed_out
            or (execution.exit_code is not None and execution.exit_code != 0)
        ):
            diagnoses.append(DiagnosisResult(
                problem="Python version probe did not complete successfully",
                category="environment", severity="ERROR", confidence=0.9,
                source="rule:python_version_probe_failed", evidence_refs=(reference,),
                recommended_actions=(
                    "Review the version probe's structured outcome and captured output",
                    "Check the reported executable path and access permissions before retrying",
                ),
                diagnosis_id=f"python_version_probe_failed:{index}",
                root_cause_chain=(
                    RootCauseStep(
                        f"probe:{index}:outcome", "Python version probe did not complete successfully",
                        f"Executor recorded status={execution.status}, exit_code={execution.exit_code}, timed_out={execution.timed_out}.",
                        (reference,),
                    ),
                    RootCauseStep(
                        f"probe:{index}:unverified", "Successful interpreter version validation is not established",
                        "This probe cannot confirm a successful version check; it does not identify a specific installation or environment defect.",
                        (reference,),
                    ),
                ),
                repair_plan=RepairPlan(
                    id=f"preview:python_version_probe_failed:{index}", diagnosis_id=f"python_version_probe_failed:{index}",
                    summary="Review the selected interpreter before retrying the version probe", risk="LOW",
                    actions=(RepairAction(
                        f"probe:{index}:review_interpreter", "Review the recorded outcome and interpreter path; choose an existing intended interpreter for a future retry if necessary.",
                        (execution.command.executable,), True, True,
                    ),),
                    verification_steps=(VerificationStep(
                        f"probe:{index}:verify_version", f'"{execution.command.executable}" --version must complete without timeout and with exit code 0.',
                        "command",
                    ),),
                ),
            ))

    if python_requirement is not None:
        evidence.append(python_requirement)
        if python_requirement.metadata.get("status") == "incompatible":
            requirement = python_requirement.metadata["declared_python_requirement"]
            current = python_requirement.metadata["current_python_version"]
            reference = python_requirement.evidence_id
            diagnosis_id = "python_version_incompatible"
            diagnoses.append(DiagnosisResult(
                problem="Current Python version does not satisfy the project's declared requirement.",
                category="python_version", severity="ERROR", confidence=1.0,
                source="rule:python_version_incompatible", evidence_refs=(reference,),
                diagnosis_id=diagnosis_id,
                recommended_actions=("Use an interpreter that satisfies the project's declared Python requirement.",),
                root_cause_chain=(
                    RootCauseStep(
                        "version:requirement", f"Project requires {requirement}",
                        "The root pyproject.toml declares this requires-python value.", (reference,),
                    ),
                    RootCauseStep(
                        "version:current", f"Current interpreter: {current}",
                        "This is the interpreter running RepoRescue, not an automatically selected project environment.", (reference,),
                    ),
                    RootCauseStep(
                        "version:mismatch", "Interpreter does not satisfy the declared requirement",
                        "Comparison of the supported final release constraints establishes a version mismatch.", (reference,),
                    ),
                ),
                repair_plan=RepairPlan(
                    id="preview:python_version_incompatible", diagnosis_id=diagnosis_id,
                    summary=f"Use a Python interpreter that satisfies {requirement}.", risk="MEDIUM",
                    actions=(RepairAction(
                        "version:select_environment",
                        "Select an existing compatible interpreter, or manually provision a compatible environment after review; run RepoRescue from that environment.",
                        (), False, True,
                    ),),
                    verification_steps=(
                        VerificationStep(
                            "version:probe", "Run the selected interpreter's --version and require exit code 0.", "command",
                        ),
                        VerificationStep(
                            "version:compare", f"Confirm that interpreter's detected version satisfies {requirement} by rerunning the requirement comparison.", "manual",
                        ),
                    ),
                ),
            ))

    return diagnoses, evidence
