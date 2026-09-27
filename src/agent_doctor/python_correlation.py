"""Small Python rules over supplied observations; never infer runtime exports/ABI.

Step 5 proves static syntax and metadata tag eligibility, not runtime causation.
These rules therefore expose partial context and explicit missing premises.
"""

from dataclasses import replace
from pathlib import PurePosixPath, PureWindowsPath
import re

from .correlation import EvidenceIndex, CorrelationProposal, apply_correlations, MAX_DIAGNOSES
from .models import CorrelationResult, DiagnosisResult, RootCauseStep
from .version_provenance import normalize_name, NAME


def _path(text):
    if not isinstance(text, str) or len(text) > 4096 or any(ord(c) < 32 for c in text):
        return None
    path = PureWindowsPath(text) if '\\' in text or re.match(r'^[A-Za-z]:', text) else PurePosixPath(text)
    return path if path.is_absolute() and '..' not in path.parts else None


def _within(child, parent):
    left, right = _path(child), _path(parent)
    return bool(left and right and type(left) is type(right) and left.is_relative_to(right))


def _result(diagnosis, pattern, title, refs, observations=(), limitations=(), ambiguous=False):
    if ambiguous:
        observations = ()
    identifier = f'correlation:{diagnosis.diagnosis_id}:{pattern}'
    steps, previous = [], None
    for position, (step_title, explanation, step_refs, relationship) in enumerate(observations):
        step_id = f'{identifier}:{position}'
        steps.append(RootCauseStep(step_id, step_title, explanation, tuple(sorted(set(step_refs))),
                                  parent_id=previous, relationship=relationship))
        previous = step_id
    result = CorrelationResult(
        identifier, pattern, title,
        'Related observations support bounded context; the underlying runtime cause remains unproven.',
        'low' if ambiguous or not observations else 'medium',
        tuple(sorted(set((*diagnosis.evidence_refs, *refs)))), tuple(steps),
        'ambiguous' if ambiguous else 'partial' if observations else 'inconclusive',
        tuple(sorted(set(limitations))),
    )
    return CorrelationProposal(diagnosis.diagnosis_id, result)


def _unique(index, kind, predicate):
    matches = tuple(item for item in index.kind(kind) if predicate(item.metadata))
    return (matches[0] if len(matches) == 1 else None), len(matches) > 1


def _provenance(index, name):
    records = tuple(item for item in index.kind('python_version_provenance')
                    if isinstance(item.metadata.get('name'), str)
                    and normalize_name(item.metadata['name']) == normalize_name(name))
    conflict = any(item.metadata.get('status') == 'ambiguous' for item in records)
    for category in ('declared', 'locked', 'resolved', 'installed'):
        values = {item.metadata.get('value') for item in records
                  if item.metadata.get('provenance') == category
                  and isinstance(item.metadata.get('value'), str)}
        conflict |= len(values) > 1
    return records, conflict


def _import_proposals(diagnosis, failure, index):
    module = failure.metadata.get('source_module') or failure.metadata.get('missing_module')
    if not isinstance(module, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', module, re.ASCII):
        return ()
    origin, origin_conflict = _unique(index, 'python_module_origin', lambda m: m.get('module') == module)
    mapping, mapping_conflict = _unique(index, 'python_distribution_mapping', lambda m: m.get('module') == module)
    interpreter, interpreter_conflict = _unique(index, 'python_interpreter', lambda m: True)
    local, local_conflict = _unique(index, 'local_python_environment', lambda m: True)
    execution = index.get(failure.associated_id, 'execution')
    current_run = bool(interpreter and interpreter.metadata.get('status') == 'available'
                       and failure.source == 'captured_execution_output'
                       and execution and _path(execution.metadata.get('executable'))
                       and _path(execution.metadata.get('executable')) == _path(interpreter.metadata.get('executable')))
    triggers = origin.metadata.get('trigger_evidence_refs', ()) if origin else ()
    origin_linked = bool(origin and origin.metadata.get('status') == 'available'
                         and isinstance(triggers, (tuple, list)) and failure.evidence_id in triggers[:128])
    proposals = []

    # A: observed interpreter difference + linked source membership, not intention.
    if local and local.metadata.get('status') == 'different' or local_conflict:
        refs = [local.evidence_id] if local else []
        observations = []
        limits = ['project_environment_intention_unproven', 'lexical_membership_only']
        ambiguous = local_conflict or interpreter_conflict or origin_conflict
        if (local and interpreter and origin_linked and current_run
                and _path(local.metadata.get('current_python_executable')) == _path(interpreter.metadata.get('executable'))
                and _path(local.metadata.get('detected_interpreter_path'))):
            refs.extend((interpreter.evidence_id, origin.evidence_id))
            project_env = _path(local.metadata['detected_interpreter_path']).parent.parent
            origin_path = origin.metadata.get('origin')
            if (_within(origin_path, interpreter.metadata.get('prefix'))
                    and not _within(origin_path, str(project_env))):
                observations = [
                    ('Import failure observed in the current interpreter', 'The captured command executable matches the collector interpreter.',
                     (failure.evidence_id, interpreter.evidence_id), 'observed'),
                    ('Module source is in the current environment outside the detected project environment',
                     'Static lookup reports lexical path membership, not proof of the module loaded during the failed run.',
                     (origin.evidence_id, interpreter.evidence_id, local.evidence_id), 'context'),
                    ('Current interpreter differs from the detected project interpreter',
                     'The intended project environment and causation of this failure are not established.', (local.evidence_id,), 'context')]
            else:
                limits.append('unexpected_origin_not_established')
        else:
            limits.append('current_failure_runtime_or_module_origin_unproven')
        proposals.append(_result(diagnosis, 'interpreter_environment', 'Interpreter and module environment context', refs,
                                 observations if not ambiguous else (), limits, ambiguous))

    candidate_refs = mapping.metadata.get('candidate_distribution_refs', ()) if mapping else ()
    owner = index.get(candidate_refs[0], 'python_distribution') if isinstance(candidate_refs, (tuple, list)) and len(candidate_refs) == 1 else None
    owner_proven = bool(mapping and mapping.metadata.get('status') == 'available' and owner
                        and owner.metadata.get('status') == 'available'
                        and owner.metadata.get('interpreter_ref') == (interpreter.evidence_id if interpreter else None))
    ambiguous = mapping_conflict or origin_conflict or interpreter_conflict or bool(mapping and mapping.metadata.get('status') == 'ambiguous')
    if mapping and (not isinstance(candidate_refs, (tuple, list)) or len(candidate_refs) > 1):
        ambiguous = True

    # C: static absence is NOT symbol unavailability. Explicit failure is retained.
    if failure.metadata.get('status') == 'symbol_import_failure':
        api, api_conflict = _unique(index, 'python_api', lambda m: m.get('source_evidence_ref') == failure.evidence_id)
        refs = [item.evidence_id for item in (mapping, api) if item]
        limits = ['static_syntax_cannot_establish_runtime_exports', 'api_causation_unproven']
        observations = []
        if owner_proven and current_run and origin_linked and api and api.metadata.get('status') == 'available' and api.metadata.get('module_origin_ref') == origin.evidence_id and api.metadata.get('module') == module and api.metadata.get('requested_symbol') == failure.metadata.get('imported_symbol'):
            refs.extend((owner.evidence_id, origin.evidence_id, interpreter.evidence_id))
            observations = [
                ('Requested symbol import failed', 'The explicit exception names this module and symbol in the captured run.', (failure.evidence_id,), 'observed'),
                ('One installed distribution is associated with the module', 'Bounded metadata mapping identifies one candidate; static source lookup is current-interpreter context.',
                 (mapping.evidence_id, owner.evidence_id, origin.evidence_id), 'context'),
                ('Static symbol syntax was inspected', 'Bindings, conditional definitions, re-exports and dynamic module behavior cannot prove runtime availability.', (api.evidence_id,), 'context')]
        else:
            limits.append('current_runtime_mapping_or_api_evidence_insufficient')
        ambiguous |= api_conflict
        proposals.append(_result(diagnosis, 'dependency_api', 'Dependency symbol failure context', refs,
                                 observations if not ambiguous else (), limits, ambiguous))

    # B: module missing alone never becomes an ABI failure.
    if origin_linked and isinstance(origin.metadata.get('origin'), str) and origin.metadata['origin'].endswith(('.pyd', '.so')):
        wheels = tuple(item for item in index.kind('python_wheel')
                       if owner and item.metadata.get('source_evidence_ref') == owner.evidence_id)
        runtime, runtime_conflict = _unique(index, 'python_abi_platform', lambda m: True)
        refs = [item.evidence_id for item in (origin, mapping, owner, runtime) if item]
        refs.extend(item.evidence_id for item in wheels)
        limits = ['wheel_tags_do_not_prove_native_binary_abi', 'module_load_and_abi_causation_unproven']
        observations = []
        if (current_run and owner_proven and runtime and runtime.metadata.get('status') == 'available'
                and len(wheels) == 1 and wheels[0].metadata.get('status') == 'available'
                and wheels[0].metadata.get('tag_check') == 'tag_mismatch'
                and wheels[0].metadata.get('runtime_evidence_ref') == runtime.evidence_id
                and wheels[0].metadata.get('compatibility_scope') == 'metadata_tags_only'):
            observations = [
                ('Import failure and native-looking module path observed', 'The filename is a static observation, not proof of a valid binary or its loading.', (failure.evidence_id, origin.evidence_id), 'observed'),
                ('Associated installed WHEEL tags do not match the current interpreter', 'Metadata tag eligibility is separate from native binary compatibility and root causation.',
                 (wheels[0].evidence_id, runtime.evidence_id, mapping.evidence_id, owner.evidence_id), 'context')]
        else:
            limits.append('explicit_abi_compatibility_evidence_missing')
        proposals.append(_result(diagnosis, 'native_artifact', 'Native artifact eligibility context', refs,
                                 observations if not ambiguous and not runtime_conflict else (), limits,
                                 ambiguous or runtime_conflict or len(wheels) > 1))

    # E: provenance is context only, including all competing source rows.
    if owner_proven and isinstance(owner.metadata.get('name'), str):
        records, conflict = _provenance(index, owner.metadata['name'])
        if records:
            refs = [mapping.evidence_id, owner.evidence_id, *(item.evidence_id for item in records)]
            incomplete = any(item.metadata.get('status') != 'available' for item in records)
            limits = ('version_values_are_context_only', 'version_provenance_incomplete') if incomplete else ('version_values_are_context_only',)
            proposals.append(_result(diagnosis, 'version_provenance', 'Dependency version source context', refs,
                () if conflict or ambiguous or incomplete else [('Declared, locked, resolved and installed sources retained separately',
                    'Different values across provenance categories do not establish a fault or suggest a version change.',
                    tuple(refs), 'context')], limits, conflict or ambiguous))
    # Exact command/cwd identity links startup to the same captured execution.
    startup, startup_conflict = _unique(index, 'startup_probe', lambda m:
        m.get('executed') is True and m.get('execution_status') == 'failed'
        and isinstance(m.get('exit_code'), int) and m['exit_code'] != 0
        and execution is not None and isinstance(m.get('argv'), (tuple, list))
        and isinstance(execution.metadata.get('arguments'), (tuple, list))
        and tuple(m['argv']) == (execution.metadata.get('executable'), *execution.metadata.get('arguments', ()))
        and _path(m.get('cwd')) == _path(execution.location) and _path(m.get('cwd')) is not None)
    if startup and current_run and not startup_conflict:
        linked = []
        for proposal in proposals:
            result = proposal.result
            if not result.root_cause_chain or result.status == 'ambiguous':
                linked.append(proposal)
                continue
            step = RootCauseStep(f'{result.id}:startup', 'Startup and import failures share the captured execution',
                'The confirmed startup observation has the same executable, arguments and working directory; hidden causation is not inferred.',
                (failure.evidence_id, startup.evidence_id, execution.evidence_id))
            chain = (step, replace(result.root_cause_chain[0], parent_id=step.id), *result.root_cause_chain[1:])
            linked.append(replace(proposal, result=replace(result, root_cause_chain=chain,
                evidence_refs=tuple(sorted(set((*result.evidence_refs, startup.evidence_id, execution.evidence_id)))))))
        proposals = linked
    return tuple(proposals)


def correlate_python(diagnoses, evidence):
    """Bounded pure entry point; current-runtime facts never describe historical logs."""
    index = EvidenceIndex(evidence)
    if index.limited or len(diagnoses) > MAX_DIAGNOSES:
        return tuple(diagnoses)
    proposals = []
    output = list(diagnoses)
    for diagnosis in diagnoses:
        failures = [index.get(ref, 'python_import_failure') for ref in diagnosis.evidence_refs]
        failures = [item for item in failures if item]
        if len(failures) == 1 and diagnosis.category == 'python_import' and diagnosis.severity == 'ERROR':
            proposals.extend(_import_proposals(diagnosis, failures[0], index))

    # D: same-log artifact context only; no error escalation from historical text.
    for log in index.kind('provided_log'):
        if log.metadata.get('input_type') != 'install_log' or 'no_matching_distribution' not in log.metadata.get('patterns', ()):
            continue
        messages = log.metadata.get('messages', ())
        if (not isinstance(messages, (tuple, list)) or len(messages) > 128
                or any(not isinstance(message, str) or len(message) > 4096 for message in messages)):
            continue  # Never hide a conflicting tail or choose by input order.
        wheels = tuple(item for item in index.kind('python_wheel') if item.metadata.get('source_evidence_ref') == log.evidence_id)
        if not wheels:
            continue  # Preserve Step 2 facts and its exit-code contract.
        runtime, conflict = _unique(index, 'python_abi_platform', lambda m: True)
        related = []
        names = set()
        # Parse only an already captured, explicitly classified failure fact.
        for message in messages:
            match = re.fullmatch(r'(?:ERROR:\s*)?No matching distribution found for (' + NAME + r')(?:[<>=!~].*)?', message)
            if match:
                names.add(normalize_name(match[1]))
        for wheel in wheels:
            name = wheel.metadata.get('name')
            if isinstance(name, str) and normalize_name(name) in names:
                related.append(wheel)
        if not related:
            continue
        identifier = f'logged_artifact_availability:{log.evidence_id}'
        if any(item.diagnosis_id == identifier for item in output):
            continue
        diagnosis = DiagnosisResult('Supplied installation log reports no matching distribution.',
            'python_installation', 'INFO', 1.0, 'rule:logged_artifact_availability',
            evidence_refs=(log.evidence_id,), diagnosis_id=identifier)
        refs = [log.evidence_id, *(item.evidence_id for item in related)]
        limits = ['supplied_log_runtime_unproven', 'observed_artifacts_not_complete_index', 'requires_python_conflict_not_established']
        observations = [('Installation failure reported in the supplied log',
                         'This is a historical log observation, not a verified current project execution.', (log.evidence_id,), 'observed')]
        if runtime and runtime.metadata.get('status') == 'available' and all(
                item.metadata.get('status') == 'available' and item.metadata.get('tag_check') == 'tag_mismatch'
                and item.metadata.get('runtime_evidence_ref') == runtime.evidence_id
                and item.metadata.get('compatibility_scope') == 'metadata_tags_only' for item in related):
            refs.append(runtime.evidence_id)
            observations.append(('No matching tags among the related observed wheel artifacts for the current interpreter',
                'This finite artifact set does not prove why the historical resolver failed or that no compatible artifact exists.', tuple(refs), 'context'))
        else:
            limits.append('artifact_eligibility_unknown_or_not_excluded')
        output.append(diagnosis)
        proposals.append(_result(diagnosis, 'artifact_availability', 'Logged artifact availability context', refs,
                                 observations if not conflict else (), limits, conflict or len(names) > 1))
    return apply_correlations(tuple(output), evidence, tuple(proposals))
