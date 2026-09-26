"""Small rules over structured inputs, without taking actions.

Confidence values are fixed rule indicators, not statistical probabilities or
confidence in a particular root cause. No root cause is inferred from an exit
code alone. Import analysis recognizes only two explicit exception lines from
already captured output. Evidence IDs are local to a single diagnosis call.
"""

from collections.abc import Sequence
from pathlib import Path
import re

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


_MODULE_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
_MODULE_NOT_FOUND = re.compile(
    rf"^ModuleNotFoundError: No module named (?P<quote>['\"])(?P<module>{_MODULE_NAME})(?P=quote)$"
)
_SYMBOL_IMPORT_FAILURE = re.compile(
    rf"^ImportError: cannot import name (?P<symbol_quote>['\"])(?P<symbol>[^'\"\r\n]+)"
    rf"(?P=symbol_quote) from (?P<module_quote>['\"])(?P<module>{_MODULE_NAME})(?P=module_quote)"
    r"(?: \([^\r\n]*\))?"
    r"(?:\. Did you mean: (?P<hint_quote>['\"])[^'\"\r\n]+(?P=hint_quote)\?)?$"
)


def analyze_import_failure(execution: ExecutionResult, index: int) -> Evidence | None:
    """Extract one explicit Python import failure from captured process output.

    stderr takes precedence over stdout. Within the selected stream, the last
    explicit supported error line wins. No process or project file is touched.
    """
    if execution.status in ("rejected", "requires_confirmation"):
        return None
    unsuccessful = (
        execution.status in ("failed", "timeout")
        or execution.timed_out
        or (execution.exit_code is not None and execution.exit_code != 0)
    )
    if not unsuccessful:
        return None

    for stream_name, output in (("stderr", execution.stderr), ("stdout", execution.stdout)):
        matches: list[tuple[str, re.Match[str]]] = []
        for line in output.splitlines():
            raw_message = line.strip()
            match = _MODULE_NOT_FOUND.fullmatch(raw_message)
            if match is not None:
                matches.append((raw_message, match))
                continue
            match = _SYMBOL_IMPORT_FAILURE.fullmatch(raw_message)
            if match is not None:
                matches.append((raw_message, match))
        if not matches:
            continue

        raw_message, match = matches[-1]
        if raw_message.startswith("ModuleNotFoundError:"):
            metadata = {
                "exception_type": "ModuleNotFoundError",
                "missing_module": match.group("module"),
                "source_stream": stream_name,
                "raw_message": raw_message,
                "status": "missing_module",
            }
        else:
            metadata = {
                "exception_type": "ImportError",
                "imported_symbol": match.group("symbol"),
                "source_module": match.group("module"),
                "source_stream": stream_name,
                "raw_message": raw_message,
                "status": "symbol_import_failure",
            }
        reference = f"execution:{index}"
        return Evidence(
            evidence_id=f"{reference}:python_import",
            kind="python_import_failure",
            source="captured_execution_output",
            summary=raw_message,
            associated_id=reference,
            location=str(execution.command.working_directory),
            metadata=metadata,
        )
    return None


def _diagnose_import_failure(item: Evidence, index: int) -> DiagnosisResult:
    reference = item.evidence_id
    status = item.metadata["status"]
    diagnosis_id = f"python_import_failure:{index}"
    if status == "missing_module":
        module = item.metadata["missing_module"]
        problem = f"Python could not import the module '{module}'."
        source = "rule:python_module_not_found"
        observed_title = f"ModuleNotFoundError names '{module}'"
        unavailable_title = f"The requested import '{module}' did not complete"
        review_description = (
            f"Check project dependency metadata to identify the distribution intended to provide '{module}'; "
            "do not assume the import name and distribution name are identical."
        )
        verification_description = (
            f"Using the intended interpreter, verify that importing '{module}' completes successfully."
        )
        plan_summary = (
            "Confirm which distribution provides the missing import module and ensure it is available "
            "in the intended Python environment."
        )
        plan_risk = "MEDIUM"
        recommended_actions = (
            "Confirm that the captured run used the intended project interpreter.",
            review_description,
            "If a dependency change is required, review and perform it manually before rerunning the project.",
        )
        plan_actions = (
            RepairAction(
                f"import:{index}:confirm_environment",
                "Confirm the captured run used the intended project interpreter.",
                (),
                True,
                True,
            ),
            RepairAction(
                f"import:{index}:review_metadata",
                review_description,
                (),
                True,
                True,
            ),
            RepairAction(
                f"import:{index}:manual_dependency_change",
                "If review establishes that a distribution is absent, manually install the correct distribution in the intended environment.",
                (),
                False,
                True,
            ),
        )
    else:
        symbol = item.metadata["imported_symbol"]
        module = item.metadata["source_module"]
        problem = f"Python could not import symbol '{symbol}' from module '{module}'."
        source = "rule:python_symbol_import_failure"
        observed_title = f"ImportError names symbol '{symbol}' from '{module}'"
        unavailable_title = f"The requested symbol import from '{module}' did not complete"
        review_description = (
            f"Review project metadata and the intended '{module}' API for the requested symbol '{symbol}'."
        )
        verification_description = (
            f"Using the intended interpreter, verify that importing '{symbol}' from '{module}' completes successfully."
        )
        plan_summary = (
            "Confirm the intended environment and review the requested module API before making any change."
        )
        plan_risk = "LOW"
        recommended_actions = (
            "Confirm that the captured run used the intended project interpreter.",
            review_description,
            "Rerun the import only after reviewing the intended module API.",
        )
        plan_actions = (
            RepairAction(
                f"import:{index}:confirm_environment",
                "Confirm the captured run used the intended project interpreter.",
                (),
                True,
                True,
            ),
            RepairAction(
                f"import:{index}:review_module_api",
                review_description,
                (),
                True,
                True,
            ),
        )

    return DiagnosisResult(
        problem=problem,
        category="python_import",
        severity="ERROR",
        confidence=1.0,
        source=source,
        evidence_refs=(reference,),
        probable_causes=(),
        recommended_actions=recommended_actions,
        diagnosis_id=diagnosis_id,
        root_cause_chain=(
            RootCauseStep(
                f"import:{index}:exception",
                observed_title,
                "The captured output contains this explicit Python exception line.",
                (reference,),
            ),
            RootCauseStep(
                f"import:{index}:incomplete",
                unavailable_title,
                "The requested import operation did not complete in the captured run.",
                (reference,),
            ),
            RootCauseStep(
                f"import:{index}:cause_unconfirmed",
                "The providing distribution and underlying cause are not established",
                "The exception identifies an import failure but does not prove which package, version, or environment change is appropriate.",
                (reference,),
            ),
        ),
        repair_plan=RepairPlan(
            id=f"preview:{diagnosis_id}",
            diagnosis_id=diagnosis_id,
            summary=plan_summary,
            risk=plan_risk,
            actions=plan_actions,
            verification_steps=(
                VerificationStep(
                    f"import:{index}:verify_interpreter",
                    "Confirm the verification uses the intended project interpreter.",
                    "manual",
                ),
                VerificationStep(
                    f"import:{index}:verify_metadata",
                    "Confirm project dependency metadata names the intended distribution when one is required.",
                    "file_check",
                ),
                VerificationStep(
                    f"import:{index}:verify_import",
                    verification_description,
                    "command",
                ),
            ),
        ),
    )


def diagnose(
    project: ProjectInfo,
    detection: DetectionResult,
    environment: EnvironmentInfo,
    executions: Sequence[ExecutionResult] = (),
    *, python_requirement: Evidence | None = None,
    local_environment: Evidence | None = None,
    startup_probe: Evidence | None = None,
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

    if local_environment is not None:
        evidence.append(local_environment)
        if local_environment.metadata.get("status") == "different":
            reference = local_environment.evidence_id
            name = local_environment.metadata["detected_local_environment"]
            interpreter = local_environment.metadata["detected_interpreter_path"]
            current = local_environment.metadata["current_python_executable"]
            diagnosis_id = "project_interpreter_different"
            diagnoses.append(DiagnosisResult(
                problem="Current Python interpreter differs from the detected project environment.",
                category="python_environment", severity="WARNING", confidence=0.9,
                source="rule:project_interpreter_different", diagnosis_id=diagnosis_id,
                evidence_refs=(reference,),
                recommended_actions=("Confirm the intended environment before selecting the project-local interpreter.",),
                root_cause_chain=(
                    RootCauseStep(
                        "local:detected", f"Project-local environment detected at {name}",
                        f"An interpreter file was detected at {interpreter}; its usability and intended role are not established.", (reference,),
                    ),
                    RootCauseStep(
                        "local:current", f"Current interpreter: {current}",
                        "This is the interpreter running RepoRescue.", (reference,),
                    ),
                    RootCauseStep(
                        "local:different", "Current interpreter differs from the project-local environment",
                        "Normalized interpreter identities differ; this does not prove the project must use the detected environment.", (reference,),
                    ),
                ),
                repair_plan=RepairPlan(
                    id="preview:project_interpreter_different", diagnosis_id=diagnosis_id,
                    summary="If this is the intended environment, select its interpreter and rerun RepoRescue and the project.", risk="LOW",
                    actions=(RepairAction(
                        "local:select", f"Manually select {interpreter} only after confirming it is intended for this project; rerun diagnosis under it.",
                        (), True, True,
                    ),),
                    verification_steps=(
                        VerificationStep("local:current", "Check sys.executable in the selected environment.", "manual"),
                        VerificationStep("local:match", f"Confirm its normalized identity matches {interpreter}.", "manual"),
                        VerificationStep("local:version", "Rerun the existing Python version probe and requires-python checks.", "command"),
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

        import_evidence = analyze_import_failure(execution, index)
        if import_evidence is not None:
            evidence.append(import_evidence)
            diagnoses.append(_diagnose_import_failure(import_evidence, index))

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

    if startup_probe is not None:
        evidence.append(startup_probe)
        startup_diagnosis = _diagnose_startup(startup_probe)
        if startup_diagnosis is not None:
            diagnoses.append(startup_diagnosis)

    return diagnoses, evidence


def _diagnose_startup(item: Evidence) -> DiagnosisResult | None:
    status = item.metadata.get('execution_status')
    reference = item.evidence_id
    if status == 'timeout':
        return DiagnosisResult(
            problem='The startup probe remained running beyond the observation window.',
            category='startup', severity='INFO', confidence=1.0,
            source='rule:startup_observation_timeout', evidence_refs=(reference,),
            diagnosis_id='startup_observation_timeout',
            recommended_actions=('Review whether the entrypoint is expected to remain running, such as a service.',),
            root_cause_chain=(RootCauseStep(
                'startup:observation', 'Startup observation window ended',
                'This outcome does not establish a hang, deadlock, or failure to start.', (reference,),
            ),),
        )
    if status != 'failed':
        return None
    exit_code = item.metadata.get('exit_code')
    nonzero = isinstance(exit_code, int) and exit_code != 0
    problem = (
        'The supported startup probe exited with a non-zero status.' if nonzero
        else 'The supported startup probe could not be launched or completed.'
    )
    return DiagnosisResult(
        problem=problem, category='startup', severity='ERROR', confidence=1.0,
        source='rule:startup_probe_failed', evidence_refs=(reference,),
        diagnosis_id='startup_probe_failed',
        recommended_actions=('Review captured startup output and any independent, more specific diagnosis before retrying.',),
        root_cause_chain=(
            RootCauseStep('startup:entrypoint', 'Supported root main.py entrypoint selected',
                          'The startup policy validated the supported entrypoint before attempting execution.', (reference,)),
            RootCauseStep('startup:execution', 'Confirmed startup probe attempted',
                          'The caller explicitly confirmed this restricted project execution probe.', (reference,)),
            RootCauseStep('startup:outcome', f'Process exited with code {exit_code}' if nonzero else 'Process completion was not established',
                          'The captured outcome is a symptom; it does not establish an underlying project defect.', (reference,)),
        ),
        repair_plan=RepairPlan(
            id='preview:startup_probe_failed', diagnosis_id='startup_probe_failed', risk='LOW',
            summary='Inspect the captured startup error and resolve the most specific diagnosed failure before retrying.',
            actions=(RepairAction(
                'startup:review', 'Review stdout/stderr evidence and resolve any independently supported specific diagnosis before an explicitly confirmed retry.',
                (), True, True,
            ),),
            verification_steps=(
                VerificationStep('startup:retry', 'After explicit confirmation, rerun the same supported startup probe.', 'command'),
                VerificationStep('startup:failure', 'Confirm the same failure does not recur; exit code 0 establishes only successful probe completion.', 'manual'),
                VerificationStep('startup:timeout', 'Report continued execution beyond the observation window separately; do not count timeout as verified success.', 'manual'),
            ),
        ),
    )
