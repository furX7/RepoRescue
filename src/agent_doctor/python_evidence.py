"""Read-only current-interpreter facts, without executing target module code."""

from collections.abc import Sequence
import csv
import io
from itertools import islice
from email.parser import Parser
from importlib.machinery import (
    BuiltinImporter, FrozenImporter, FileFinder, ModuleSpec,
    SourceFileLoader, SourcelessFileLoader, ExtensionFileLoader,
    SOURCE_SUFFIXES, BYTECODE_SUFFIXES, EXTENSION_SUFFIXES,
)
import os
from pathlib import Path
import platform
import re
import site
import struct
import sys
import sysconfig
from zipimport import zipimporter, ZipImportError

from .models import Evidence


MAX_METADATA_BYTES = 1024 * 1024
MAX_FILE_LIST_BYTES = 4 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10000
MAX_METADATA_CANDIDATES = 2048
MAX_SEARCH_ROOTS = 128


class MetadataLimitExceeded(ValueError):
    """A bounded collection is incomplete, not evidence of a project fault."""


def interpreter_evidence() -> Evidence:
    """Describe the running collector, not a traceback's historical interpreter."""
    return Evidence(
        "environment:interpreter", "python_interpreter", "stdlib:sys",
        "Current diagnostic interpreter; not proof of the supplied log's runtime.",
        location=sys.executable or None,
        metadata={
            "executable": sys.executable, "version": sys.version,
            "prefix": sys.prefix, "base_prefix": sys.base_prefix,
            "is_venv": sys.prefix != sys.base_prefix, "platform": sys.platform,
            "machine": platform.machine(), "pointer_bits": struct.calcsize('P') * 8,
            "status": "available",
        },
    )


def _spec_in_paths(name: str, paths: Sequence[str]) -> ModuleSpec | None:
    """Use stdlib finders directly: no custom meta/path hooks or loader execution."""
    namespaces = []
    for entry in paths:
        if not isinstance(entry, str):
            continue
        entry = entry or os.getcwd()
        if os.path.isdir(entry):
            finder = FileFinder(entry,
                (ExtensionFileLoader, EXTENSION_SUFFIXES),
                (SourceFileLoader, SOURCE_SUFFIXES),
                (SourcelessFileLoader, BYTECODE_SUFFIXES))
        else:
            try:
                finder = zipimporter(entry)
            except ZipImportError:
                continue
        spec = finder.find_spec(name)
        if spec is None:
            continue
        if spec.loader is not None:
            return spec
        namespaces.extend(spec.submodule_search_locations or ())
    if namespaces:
        spec = ModuleSpec(name, None)
        spec.submodule_search_locations = namespaces
        return spec
    return None


def find_module_spec(module: str) -> ModuleSpec | None:
    """Resolve dotted names from parent specs without importing the parents.

    util.find_spec(dotted_name) can import the parent. Here only stdlib builtin,
    frozen, file and zip finders are used; arbitrary import hooks are excluded.
    Runtime changes to __path__ are deliberately not predicted.
    """
    parts = module.split('.')
    root = parts[0]
    spec = BuiltinImporter.find_spec(root) or FrozenImporter.find_spec(root)
    if spec is None:
        spec = _spec_in_paths(root, sys.path)
    for index in range(1, len(parts)):
        if spec is None or spec.submodule_search_locations is None:
            return None
        name = '.'.join(parts[:index + 1])
        spec = (BuiltinImporter.find_spec(name) or FrozenImporter.find_spec(name)
                or _spec_in_paths(name, spec.submodule_search_locations))
    return spec


def _within(path: str, parent: str) -> bool:
    """Lexical path membership, not proof about symlink targets or ownership."""
    try:
        child = os.path.normcase(os.path.abspath(path))
        root = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath((child, root)) == root
    except (OSError, ValueError):
        return False


def _module_evidence(module: str, refs: tuple[str, ...]) -> Evidence:
    facts = {"module": module, "trigger_evidence_refs": refs,
             "interpreter_ref": "environment:interpreter",
             "lookup_scope": "stdlib_specs_without_import", "path_membership": "lexical",
             "status": "unavailable"}
    try:
        spec = find_module_spec(module)
        facts.update(origin=None, search_locations=(), module_type="unknown")
        if spec is not None:
            locations = tuple(str(path) for path in (spec.submodule_search_locations or ()))
            origin = spec.origin
            kind = ("builtin" if origin == "built-in" else "frozen" if origin == "frozen"
                    else "namespace" if origin is None and spec.submodule_search_locations is not None
                    else "package" if spec.submodule_search_locations is not None else "module")
            facts.update(status="available", origin=origin, search_locations=locations, module_type=kind)
            paths = ((origin,) if origin and kind not in ("builtin", "frozen") else ()) + locations
            roots = tuple(dict.fromkeys((sysconfig.get_path('purelib'), sysconfig.get_path('platlib'),
                                        *site.getsitepackages(), site.getusersitepackages())))
            facts.update(
                site_packages_roots=tuple(root for root in roots if root),
                site_packages_paths=tuple(path for path in paths if any(root and _within(path, root) for root in roots)),
                prefix_paths=tuple(path for path in paths if _within(path, sys.prefix)),
                venv_paths=tuple(path for path in paths if sys.prefix != sys.base_prefix and _within(path, sys.prefix)),
            )
        else:
            # A limited static lookup cannot rule out custom hooks or __path__ changes.
            facts.update(status="not_found", limitation="static_lookup_only")
    except Exception as error:
        facts.update(status="unavailable", error_type=type(error).__name__)
    return Evidence(f"module:{module}", "python_module_origin", "stdlib:importlib.machinery",
                    f"Module origin observation for {module}: {facts['status']} (current interpreter only).",
                    associated_id="environment:interpreter", location=facts.get('origin')
                    if facts.get('origin') not in ('built-in', 'frozen') else None, metadata=facts)


def _metadata_text(path: Path) -> str | None:
    """Only read a metadata file owned lexically by the discovered directory."""
    if path.is_symlink() or path.is_junction():
        raise ValueError('Linked metadata is not a proven source')
    if not path.is_file():
        return None
    limit = MAX_FILE_LIST_BYTES if path.name == 'RECORD' else MAX_METADATA_BYTES
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise MetadataLimitExceeded('Metadata file exceeds byte limit')
    return data.decode('utf-8')


def _static_path(file: str) -> tuple[str, ...]:
    """Validate a relative metadata entry lexically; never follow its target."""
    parts = tuple(file.replace('\\', '/').split('/'))
    if (not file or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in file)
            or any(not part or part in ('.', '..') or part != part.strip()
                   or part.endswith('.') or any(char in part for char in ':"<>|?*') for part in parts)):
        raise ValueError('Unsafe or ambiguous metadata path')
    return parts


def _metadata_headers(text: str) -> tuple[str | None, str | None, bool]:
    parsed = Parser().parsestr(text)
    if parsed.defects:
        return None, None, False

    def unique(field: str) -> str | None:
        values = {value.strip() for value in parsed.get_all(field, ())}
        if len(values) != 1:
            return None
        value = values.pop()
        if not value or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            return None
        return value

    name, version = unique('Name'), unique('Version')
    if name is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        name = None
    if version is not None and any(char.isspace() for char in version):
        version = None
    return name, version, name is not None and version is not None


def _declared_modules(directory: Path) -> tuple[str, ...]:
    top_level = _metadata_text(directory / 'top_level.txt')
    record = _metadata_text(directory / 'RECORD')
    recorded = {}
    if record is not None:
        for row in csv.reader(io.StringIO(record, newline=''), strict=True):
            if (len(row) != 3 or any(ord(char) < 32 or 127 <= ord(char) <= 159 or char == '"' for char in row[1])
                    or (row[2] and not re.fullmatch(r'[0-9]+', row[2]))):
                raise ValueError('Malformed RECORD row')
            path = _static_path(row[0])
            details = tuple(row[1:])
            if path in recorded and recorded[path] != details:
                raise ValueError('Conflicting RECORD rows')
            recorded[path] = details
    if top_level and top_level.strip():
        names = tuple(line.strip() for line in top_level.splitlines() if line.strip())
        if not all(re.fullmatch(r'[A-Za-z_]\w*', name, re.ASCII) for name in names):
            raise ValueError('Invalid top-level module metadata')
        return tuple(sorted(set(names)))
    # SOURCES.txt describes a source tree, not the installed import layout.
    paths = recorded
    modules = set()
    for parts in paths:
        suffixes = (*EXTENSION_SUFFIXES, '.py', '.pyc')
        if not any(parts[-1].endswith(suffix) for suffix in suffixes):
            continue
        first = parts[0]
        if len(parts) == 1:
            for suffix in suffixes:
                if first.endswith(suffix):
                    first = first[:-len(suffix)]
                    break
            else:
                continue
        if re.fullmatch(r'[A-Za-z_]\w*', first, re.ASCII) and first != '__pycache__':
            modules.add(first)
    return tuple(sorted(modules))


def discover_metadata() -> tuple[list[dict], tuple[str, ...]]:
    """Inspect direct metadata children of explicit interpreter paths; no finders."""
    roots = tuple(dict.fromkeys(str(Path(path or os.getcwd()).absolute()) for path in (
        *sys.path, sysconfig.get_path('purelib'), sysconfig.get_path('platlib'),
        *site.getsitepackages(), site.getusersitepackages(),
    ) if isinstance(path, str)))
    if len(roots) > MAX_SEARCH_ROOTS:
        raise MetadataLimitExceeded('Too many interpreter search roots')
    records, errors, seen = [], [], set()
    for root in roots:
        try:
            directory = Path(root)
            if any(parent.is_symlink() or parent.is_junction() for parent in (directory, *directory.parents)):
                raise ValueError('Linked search root')
            if not directory.is_dir():
                continue
            with os.scandir(directory) as entries:
                children = [Path(entry.path) for entry in islice(entries, MAX_DIRECTORY_ENTRIES + 1)]
            if len(children) > MAX_DIRECTORY_ENTRIES:
                raise MetadataLimitExceeded('Too many directory entries')
            children = sorted((path for path in children if path.suffix.lower() in ('.dist-info', '.egg-info')),
                              key=lambda child: child.name)
            if len(children) > MAX_METADATA_CANDIDATES:
                raise MetadataLimitExceeded('Too many metadata candidates')
        except Exception as error:
            errors.append(type(error).__name__)
            continue
        for path in children:
            if path.suffix.lower() not in ('.dist-info', '.egg-info'):
                continue
            key = os.path.normcase(str(path.absolute()))
            if key in seen:
                continue
            seen.add(key)
            facts = {'metadata_location': None, 'dist_info_location': None,
                     'name': None, 'installed_version': None, 'status': 'unavailable'}
            modules = ()
            try:
                if path.is_symlink() or path.is_junction():
                    raise ValueError('Linked metadata directory')
                is_directory = path.is_dir()
                metadata_path = (path / ('METADATA' if path.suffix.lower() == '.dist-info' else 'PKG-INFO')
                                 if is_directory else path if path.suffix.lower() == '.egg-info' else None)
                text = _metadata_text(metadata_path) if metadata_path is not None else None
                if text is None:
                    raise ValueError('Missing own metadata file')
                name, version, valid_headers = _metadata_headers(text)
                facts.update(name=name, installed_version=version,
                             metadata_location=str(metadata_path),
                             dist_info_location=str(path) if path.suffix.lower() == '.dist-info' else None)
                if is_directory:
                    modules = _declared_modules(path)
                if not valid_headers:
                    raise ValueError('Incomplete or conflicting metadata headers')
                facts['status'] = 'available'
            except Exception as error:
                facts['error_type'] = type(error).__name__
                errors.append(type(error).__name__)
            records.append({'facts': facts, 'modules': modules})
    return records, tuple(sorted(set(errors)))


def collect_import_evidence(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Inspect explicit targets only; static metadata facts never become diagnoses."""
    targets = {}
    for item in evidence:
        if item.kind != 'python_import_failure':
            continue
        module = item.metadata.get('missing_module') or item.metadata.get('source_module')
        if isinstance(module, str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', module, flags=re.ASCII):
            targets.setdefault(module, []).append(item.evidence_id)
    if not targets:
        return ()
    try:
        records, errors = discover_metadata()
    except Exception as error:
        records, errors = [], (type(error).__name__,)
    observations, selected = [], set()
    for module, refs in targets.items():
        observations.append(_module_evidence(module, tuple(refs)))
        candidates = tuple(index for index, record in enumerate(records)
                           if module.split('.')[0] in record['modules'] and record['facts']['name'])
        names = tuple(records[index]['facts']['name'] for index in candidates)
        status = 'ambiguous' if len(candidates) > 1 else 'unavailable' if errors else 'available' if candidates else 'unknown'
        observations.append(Evidence(
            f"mapping:{module}", "python_distribution_mapping", "stdlib:filesystem_metadata",
            f"Distribution mapping for {module}: {status}; no candidate was selected.",
            associated_id=f"module:{module}", metadata={
                "module": module, "top_level_module": module.split('.')[0],
                "candidate_distributions": names,
                "candidate_distribution_refs": tuple(f"distribution:{index}" for index in candidates),
                "status": status, "discovery_errors": errors,
                "interpreter_ref": "environment:interpreter",
            },
        ))
        selected.update(candidates)
    for index in sorted(selected):
        facts = dict(records[index]['facts'], interpreter_ref='environment:interpreter')
        name = facts['name']
        observations.append(Evidence(
            f"distribution:{index}", "python_distribution", "stdlib:filesystem_metadata",
            f"Installed distribution observation for {name}: {facts['status']}.",
            associated_id="environment:interpreter", location=facts['metadata_location'], metadata=facts,
        ))
    return tuple(observations)
