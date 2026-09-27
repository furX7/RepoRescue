"""Bounded literal dependency facts; no resolution or compatibility decisions."""

from collections import defaultdict
from configparser import ConfigParser
from dataclasses import replace
import json
from pathlib import Path
import re
import tomllib

from .models import Evidence, ProjectInfo

MAX_SOURCE_BYTES = 256 * 1024
MAX_SOURCES = 64
MAX_RECORDS = 2048
NAME = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?'
# Deliberately limited PEP 440 spelling, not a version comparison engine.
VERSION = r'v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)?'
LOCKS = frozenset({'uv.lock', 'poetry.lock', 'Pipfile.lock'})


def normalize_name(name: str) -> str:
    """PEP 503 key only; never equate module names with distribution names."""
    return re.sub(r'[-_.]+', '-', name).lower()


def is_source(name: str) -> bool:
    return (name in LOCKS or name in ('pyproject.toml', 'setup.cfg', 'setup.py')
            or bool(re.fullmatch(r'requirements[^/\\]*\.txt', name)))


def _fact(provenance, source, location, name=None, value=None, status='available', **extra):
    return Evidence('', 'python_version_provenance', source,
                    f'{provenance} version source for {name or "unknown dependency"}: {status}.',
                    location=location, metadata={
                        'provenance': provenance, 'name': name,
                        'normalized_name': normalize_name(name) if name else None,
                        'value': value, 'status': status, **extra})


def _requirement(raw, source, path, origin):
    if not isinstance(raw, str):
        return _fact('declared', source, path, status='unknown', evidence_origin=origin,
                     limitation='invalid_requirement')
    if (any(ord(c) < 32 and c not in '\t' or 127 <= ord(c) <= 159 for c in raw)
            or raw.strip().endswith(('.whl', '.tar.gz', '.zip'))):
        return _fact('declared', source, path, status='unknown', evidence_origin=origin,
                     limitation='unsupported_requirement')
    match = re.fullmatch(r'\s*(' + NAME + r')(?:\[([A-Za-z0-9._,-]+)\])?\s*(.*)', raw)
    if match is None:
        return _fact('declared', source, path, status='unknown', evidence_origin=origin,
                     limitation='unsupported_requirement')
    name, extras, remainder = match.groups()
    if remainder and not remainder.startswith(('@', ';', '(', '~', '=', '!', '<', '>')):
        return _fact('declared', source, path, status='unknown', evidence_origin=origin,
                     limitation='unsupported_requirement')
    spec, separator, marker = remainder.partition(';')
    spec = spec.strip()
    if spec.startswith('(') and spec.endswith(')'):
        spec = spec[1:-1].strip()
    clauses = spec.split(',') if spec else []
    valid = all(re.fullmatch(r'\s*(?:~=|===|==|!=|<=|>=|<|>)\s*' + VERSION + r'\s*', c)
                or re.fullmatch(r'\s*(?:==|!=)\s*[0-9]+(?:\.[0-9]+)*\.\*\s*', c) for c in clauses)
    # Markers are preserved, never evaluated. Unsupported remote/local references
    # do not supply a version constraint. Invalid marker text is not validated.
    status = 'available' if valid and not separator else 'unknown'
    return _fact('declared', source, path, name, spec if valid else None, status,
                 raw_requirement=raw, extras=extras, marker=marker.strip() if separator else None,
                 evidence_origin=origin,
                 limitation='marker_not_evaluated' if valid and separator else None if valid else 'unsupported_requirement')


def _parse_source(name, text, path):
    if name == 'setup.py':
        return [_fact('declared', name, path, status='unavailable',
                      evidence_origin='setup.py', limitation='executable_metadata_not_read')]
    if name.startswith('requirements'):
        records = []
        pending, start = '', 0
        for line_number, line in enumerate(text.splitlines(), 1):
            line = re.split(r'\s+#', line, maxsplit=1)[0].strip()
            if not line or line.startswith('#'):
                continue
            start = start or line_number
            pending += line
            if line.endswith('\\'):
                pending = pending[:-1] + ' '
                continue
            records.append(_requirement(pending, name, path, f'line:{start}'))
            pending, start = '', 0
        if pending:
            records.append(_fact('declared', name, path, status='unknown',
                                 evidence_origin=f'line:{start}', limitation='unfinished_continuation'))
        return records
    if name == 'setup.cfg':
        cfg = ConfigParser(interpolation=None, strict=True)
        cfg.read_string(text)
        groups = []
        if cfg.has_option('options', 'install_requires'):
            groups.append(('options.install_requires', cfg.get('options', 'install_requires')))
        if cfg.has_section('options.extras_require'):
            groups.extend((f'options.extras_require.{key}', value)
                          for key, value in cfg.items('options.extras_require'))
        return [_requirement(line.strip(), name, path, origin)
                for origin, value in groups for line in value.splitlines() if line.strip()]
    if name == 'Pipfile.lock':
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        data = json.loads(text, object_pairs_hook=unique)
        if not isinstance(data, dict) or not any(key in data for key in ('default', 'develop')):
            raise ValueError('Unsupported lock structure')
        records = []
        for group in ('default', 'develop'):
            packages = data.get(group, {})
            if not isinstance(packages, dict):
                raise ValueError('Invalid lock group')
            for package, details in packages.items():
                value = details.get('version') if isinstance(details, dict) else None
                value = value[2:] if isinstance(value, str) and value.startswith('==') else None
                records.append(_locked(name, path, package, value, f'{group}.{package}', details))
        return records
    data = tomllib.loads(text)
    if name in ('uv.lock', 'poetry.lock'):
        if 'package' not in data:
            raise ValueError('Unsupported lock structure')
        if name == 'uv.lock' and data.get('version', 1) != 1:
            raise ValueError('Unsupported uv lock version')
        if name == 'poetry.lock' and data.get('metadata', {}).get('lock-version', '2.0') not in ('1.0', '1.1', '2.0', '2.1'):
            raise ValueError('Unsupported Poetry lock version')
        packages = data.get('package', [])
        if not isinstance(packages, list):
            raise ValueError('Invalid package list')
        return [_locked(name, path, item.get('name'), item.get('version'), f'package:{i}', item)
                for i, item in enumerate(packages)]
    project = data.get('project', {})
    records = []
    groups = [('project.dependencies', project.get('dependencies', []))]
    groups.extend((f'project.optional-dependencies.{key}', value)
                  for key, value in project.get('optional-dependencies', {}).items())
    for origin, values in groups:
        if not isinstance(values, list):
            raise ValueError('Dependencies must be arrays')
        records.extend(_requirement(raw, name, path, f'{origin}:{i}') for i, raw in enumerate(values))
    if 'dependencies' in project.get('dynamic', []):
        records.append(_fact('declared', name, path, status='unknown',
                             evidence_origin='project.dynamic', limitation='dynamic_dependencies'))
    if data.get('tool', {}).get('poetry', {}).get('dependencies'):
        records.append(_fact('declared', name, path, status='unavailable',
                             evidence_origin='tool.poetry.dependencies', limitation='unsupported_declaration_format'))
    return records


def _locked(source, path, name, value, origin, details):
    valid_name = isinstance(name, str) and re.fullmatch(NAME, name)
    valid_value = isinstance(value, str) and re.fullmatch(VERSION, value)
    # uv includes the project and editable/path packages: their version is not a
    # registry lock pin. Record uncertainty instead of borrowing that number.
    local = (isinstance(details.get('source'), dict)
             and (any(key in details['source'] for key in ('editable', 'virtual', 'directory', 'path'))
                  or details['source'].get('type') in ('directory', 'file', 'git')))
    status = 'available' if valid_name and valid_value and not local else 'unknown'
    return _fact('locked', source, path, name if valid_name else None,
                 value if valid_value and not local else None, status,
                 evidence_origin=origin, limitation=None if status == 'available' else 'no_supported_lock_pin')


def collect_project_provenance(project: ProjectInfo, evidence=()):
    """Consume only the existing bounded snapshot; no recursive discovery."""
    records = []
    sources = sorted({file for file in project.files if is_source(Path(file).name)})
    if len(sources) > MAX_SOURCES:
        categories = sorted({'locked' if Path(file).name in LOCKS else 'declared' for file in sources})
        records.extend(_fact(category, 'project_snapshot', str(project.root_path), status='unavailable',
                             evidence_origin='project.files', limitation='source_limit')
                       for category in categories)
        sources = []
    for relative in sources:
        path = project.root_path / relative
        name = path.name
        provenance = 'locked' if name in LOCKS else 'declared'
        try:
            # Snapshot paths are data too. Refuse escapes, links and replaced parents.
            parts = relative.replace('\\', '/').split('/')
            if (Path(relative).is_absolute() or any(part in ('', '.', '..') or ':' in part for part in parts)
                    or any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents))
                    or not path.resolve().is_relative_to(project.root_path.resolve()) or not path.is_file()):
                raise ValueError('Unsafe source')
            with path.open('rb') as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError('Source byte limit')
            parsed = _parse_source(name, raw.decode('utf-8-sig'), str(path))
            if len(records) + len(parsed) > MAX_RECORDS:
                raise ValueError('Record limit')
            records.extend(parsed)
        except Exception as error:
            records.append(_fact(provenance, name, str(path), status='unavailable',
                                 evidence_origin=relative, limitation='source_not_read_or_parsed',
                                 error_type=type(error).__name__))
    for item in evidence:
        if item.kind == 'provided_log' and item.metadata.get('input_type') == 'install_log':
            for fact in item.metadata.get('resolved_versions', ()):
                records.append(_fact('resolved', item.source, item.location, fact['name'], fact['version'],
                                     evidence_origin=fact['evidence_origin'], source_evidence_ref=item.evidence_id,
                                     raw_message=fact['raw_message'], runtime_scope='supplied_log_only'))
    return finalize(records)


def installed_provenance(evidence, names=()):
    """Project Step 3 observations, never manufacture installed facts from logs."""
    records, found = [], set()
    for item in evidence:
        if item.kind != 'python_distribution':
            continue
        name, version = item.metadata.get('name'), item.metadata.get('installed_version')
        if not isinstance(name, str) or not re.fullmatch(NAME, name):
            continue
        found.add(normalize_name(name))
        records.append(_fact('installed', item.source, item.location, name, version,
                             item.metadata.get('status', 'unavailable'),
                             evidence_origin=item.metadata.get('metadata_location'),
                             source_evidence_ref=item.evidence_id, interpreter_ref='environment:interpreter',
                             runtime_scope='current_interpreter_only'))
    for name in sorted(set(names)):
        if normalize_name(name) not in found:
            records.append(_fact('installed', 'stdlib:filesystem_metadata', None, name, status='unknown',
                                 evidence_origin='bounded_current_interpreter_metadata',
                                 interpreter_ref='environment:interpreter', limitation='no_observed_distribution'))
    return records


def finalize(records):
    """Stable run-local IDs; alternatives retain all source rows and values."""
    groups = defaultdict(list)
    for index, item in enumerate(records):
        if item.metadata.get('normalized_name'):
            groups[(item.metadata['provenance'], item.metadata['normalized_name'])].append(index)
    conflicts = {i for indices in groups.values()
                 if len({records[j].metadata['value'] for j in indices
                         if records[j].metadata['value'] is not None}) > 1 for i in indices}
    output = []
    for index, item in enumerate(records):
        facts = dict(item.metadata)
        if index in conflicts and facts['status'] == 'available':
            facts.update(status='ambiguous', limitation='multiple_source_values')
        output.append(replace(item, evidence_id=f'provenance:{index}', metadata=facts,
                              summary=f"{facts['provenance']} version source for {facts['name'] or 'unknown dependency'}: {facts['status']}."))
    return tuple(output)


def extract_resolved_versions(text):
    """Only pip's explicit dry-run selection, never candidate downloads or installs."""
    facts = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.startswith('Would install '):
            continue
        if len(line) > 4096:
            continue
        tokens = line[len('Would install '):].split()
        parsed = []
        for token in tokens:
            match = re.fullmatch('(' + NAME + ')-(' + VERSION + ')', token)
            if not match:
                parsed = []
                break
            name, version = match.groups()
            parsed.append({'name': name, 'version': version, 'evidence_origin': f'line:{line_number}',
                           'raw_message': token})
        facts.extend(parsed)
        if len(facts) > MAX_RECORDS:
            return ()
    return tuple(facts)
