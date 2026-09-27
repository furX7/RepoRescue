"""Pure Core contract for bounded, traceable Pack correlation proposals."""

from collections import Counter
from dataclasses import dataclass, replace

from .models import CorrelationResult

MAX_EVIDENCE = 8192
MAX_DIAGNOSES = 256
MAX_CORRELATIONS = 8
MAX_STEPS = 16


class EvidenceIndex:
    """Duplicate IDs are ambiguous even if their payloads happen to agree."""

    def __init__(self, evidence):
        self.limited = len(evidence) > MAX_EVIDENCE
        items = tuple(item for item in evidence[:MAX_EVIDENCE]
                      if isinstance(item.evidence_id, str) and len(item.evidence_id) <= 1024)
        counts = Counter(item.evidence_id for item in items)
        self.duplicates = {key for key, count in counts.items() if count != 1}
        self.items = {item.evidence_id: item for item in items
                      if item.evidence_id and item.evidence_id not in self.duplicates}

    def kind(self, kind):
        return tuple(item for _, item in sorted(self.items.items()) if item.kind == kind)

    def get(self, reference, kind=None):
        item = self.items.get(reference) if isinstance(reference, str) else None
        return item if item and (kind is None or item.kind == kind) else None


@dataclass(frozen=True)
class CorrelationProposal:
    diagnosis_id: str
    result: CorrelationResult


def valid_result(result, index):
    """Check references and a linear, forward-supported chain before enrichment."""
    if index.limited or not result.evidence_refs or len(result.root_cause_chain) > MAX_STEPS:
        return False
    refs = set(result.evidence_refs)
    if len(refs) != len(result.evidence_refs) or not refs <= index.items.keys():
        return False
    seen = set()
    previous = None
    for step in result.root_cause_chain:
        if (not step.id or step.id in seen or step.parent_id != previous
                or step.relationship not in ('observed', 'supports', 'context')
                or len(set(step.evidence_refs)) != len(step.evidence_refs)
                or not set(step.evidence_refs) <= refs):
            return False
        seen.add(step.id)
        previous = step.id
    return True


def apply_correlations(diagnoses, evidence, proposals):
    """Preserve diagnoses/plans/confidence; append only validated related context.

    No I/O, evidence fabrication, severity escalation, callback or rule loading.
    Competing proposals for a pattern are refused rather than arbitrarily ranked.
    """
    index = EvidenceIndex(evidence)
    if index.limited or len(diagnoses) > MAX_DIAGNOSES:
        return tuple(diagnoses)
    diagnosis_counts = Counter(item.diagnosis_id for item in diagnoses)
    grouped = {}
    for proposal in proposals[:MAX_DIAGNOSES * MAX_CORRELATIONS]:
        grouped.setdefault(proposal.diagnosis_id, []).append(proposal.result)
    output = []
    for diagnosis in diagnoses:
        if diagnosis_counts[diagnosis.diagnosis_id] != 1 or diagnosis.correlations:
            output.append(diagnosis)
            continue
        candidates = grouped.get(diagnosis.diagnosis_id, ())
        counts = Counter(item.id for item in candidates)
        accepted = tuple(sorted((item for item in candidates
                                 if counts[item.id] == 1 and valid_result(item, index)
                                 and set(diagnosis.evidence_refs) <= index.items.keys()
                                 and set(diagnosis.evidence_refs) <= set(item.evidence_refs)),
                                key=lambda item: item.id))[:MAX_CORRELATIONS]
        existing = {step.id for step in diagnosis.root_cause_chain}
        steps = tuple(step for item in accepted for step in item.root_cause_chain)
        ids = [step.id for step in steps]
        if len(ids) != len(set(ids)) or existing.intersection(ids):
            accepted, steps = (), ()
        refs = tuple(sorted(set(diagnosis.evidence_refs).union(
            *(set(item.evidence_refs) for item in accepted))))
        output.append(replace(diagnosis, evidence_refs=refs,
                              root_cause_chain=(*diagnosis.root_cause_chain, *steps),
                              correlations=accepted) if accepted else diagnosis)
    return tuple(output)


def accept_enrichment(original, candidate, evidence):
    """Core checks the optional hook cannot replace findings or add repair authority."""
    index = EvidenceIndex(evidence)
    if index.limited or len(candidate) > MAX_DIAGNOSES:
        return tuple(original)
    counts = Counter(item.diagnosis_id for item in candidate)
    by_id = {item.diagnosis_id: item for item in candidate if counts[item.diagnosis_id] == 1}
    output = []
    original_ids = {item.diagnosis_id for item in original}
    for diagnosis in original:
        enriched = by_id.get(diagnosis.diagnosis_id)
        if not enriched or not enriched.correlations:
            output.append(diagnosis)
            continue
        # Reconstruct through the same Core guard; compare every observable field.
        proposals = tuple(CorrelationProposal(diagnosis.diagnosis_id, item) for item in enriched.correlations)
        expected = apply_correlations((diagnosis,), evidence, proposals)[0]
        output.append(enriched if enriched == expected else diagnosis)
    for diagnosis_id in sorted(by_id.keys() - original_ids):
        diagnosis = by_id[diagnosis_id]
        if (diagnosis.severity != 'INFO' or diagnosis.repair_plan is not None
                or diagnosis.recommended_actions or diagnosis.probable_causes
                or not diagnosis.correlations or len(diagnosis.correlations) > MAX_CORRELATIONS
                or not all(valid_result(item, index) for item in diagnosis.correlations)):
            continue
        refs = set().union(*(set(item.evidence_refs) for item in diagnosis.correlations))
        steps = tuple(step for item in diagnosis.correlations for step in item.root_cause_chain)
        if (set(diagnosis.evidence_refs) == refs and diagnosis.root_cause_chain == steps
                and len({step.id for step in steps}) == len(steps)):
            output.append(diagnosis)
    return tuple(output)


def correlation_unavailable(diagnoses, evidence, reason):
    """A declared optional-provider failure cannot invalidate completed diagnosis."""
    proposals = tuple(CorrelationProposal(diagnosis.diagnosis_id, CorrelationResult(
        f'correlation:{diagnosis.diagnosis_id}:provider_unavailable', 'correlation_unavailable',
        'Optional correlation could not complete', 'The original diagnosis and observations are preserved.',
        'low', diagnosis.evidence_refs, status='inconclusive', limitations=(reason,)))
        for diagnosis in diagnoses if diagnosis.evidence_refs)
    return apply_correlations(diagnoses, evidence, proposals)
