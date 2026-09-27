"""Literal API/build facts and conservative tag checks, without loading code."""

import ast
from email.parser import Parser
from itertools import product, islice
from pathlib import Path
import os
import re
import struct
import sys
import sysconfig

from .models import Evidence
from .version_provenance import VERSION

MAX_BYTES = 256 * 1024
MAX_TAGS = 256
MAX_TARGETS = 128
MAX_AST_NODES = 10000


def _read(path):
    if (not path.is_absolute()
            or '..' in path.parts
            or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))
            or not path.is_file()):
        raise ValueError('Unproven regular source')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Source byte limit')
    return data


def _source_text(data):
    """Decode a small trusted encoding set without the extensible codec registry."""
    bom = data.startswith(b'\xef\xbb\xbf')
    if bom:
        data = data[3:]
    lines = data.split(b'\n', 2)
    cookie = re.compile(br'^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)')
    match = cookie.match(lines[0])
    if not match and len(lines) > 1 and re.match(br'^[ \t\f]*(?:#|\r?$)', lines[0]):
        match = cookie.match(lines[1])
    encoding = 'utf-8'
    if match:
        name = match[1].decode('ascii').lower().replace('-', '_')
        aliases = {'utf8': 'utf-8', 'utf_8': 'utf-8',
                   'ascii': 'ascii', 'us_ascii': 'ascii', 'usascii': 'ascii',
                   'latin1': 'latin-1', 'latin_1': 'latin-1',
                   'iso8859_1': 'latin-1', 'iso_8859_1': 'latin-1'}
        if name not in aliases:
            raise ValueError('Unsupported source encoding')
        encoding = aliases[name]
        if bom and encoding != 'utf-8':
            raise ValueError('Conflicting source encoding')
    # CPython handles these fixed byte decoders directly, without codec lookup.
    return data.decode(encoding)


def runtime_facts():
    """Current collector only; no subprocess and no inferred historical runtime."""
    facts = {'implementation': sys.implementation.name,
             'python_version': tuple(sys.version_info[:3]), 'sys_platform': sys.platform,
             'pointer_bits': struct.calcsize('P') * 8,
             'interpreter_ref': 'environment:interpreter',
             'runtime_scope': 'current_interpreter_only', 'status': 'available',
             'soabi': None, 'extension_suffix': None, 'platform_tag': None,
             'debug_build': None, 'gil_disabled': None, 'abi_flags': getattr(sys, 'abiflags', ''),
             'native_abi': None,
             'platform_tag_overridden': sys.platform != 'win32' and '_PYTHON_HOST_PLATFORM' in os.environ}
    try:
        # get_config_var initializes platform data through normal import hooks.
        # Consume only an already initialized stdlib snapshot, never cause a load.
        config = getattr(sysconfig, '_CONFIG_VARS', None)
        if type(config) is not dict:
            facts.update(status='unavailable', limitation='build_configuration_not_initialized')
            return facts
        facts.update(soabi=config.get('SOABI'), extension_suffix=config.get('EXT_SUFFIX'),
                     debug_build=config.get('Py_DEBUG'), gil_disabled=config.get('Py_GIL_DISABLED'))
        # Other get_platform branches can lazily import OS-specific helpers.
        if sys.platform in ('win32', 'linux'):
            facts['platform_tag'] = sysconfig.get_platform().replace('-', '_').replace('.', '_')
        major, minor = facts['python_version'][:2]
        soabi = facts['soabi']
        if facts['implementation'] == 'cpython' and major == 3 and minor >= 8 and isinstance(soabi, str):
            match = re.fullmatch(r'(?:cp|cpython-)' + f'{major}{minor}' + r'([dt]*)-.+', soabi)
            if match and len(set(match[1])) == len(match[1]):
                flags = match[1]
                if (bool(facts['debug_build']) == ('d' in flags)
                        and bool(facts['gil_disabled']) == ('t' in flags)):
                    facts['native_abi'] = f'cp{major}{minor}' + flags
    except Exception as error:
        facts.update(status='unavailable', error_type=type(error).__name__)
    return facts


def expand_tags(raw_tags):
    tags = set()
    for raw in raw_tags:
        if not isinstance(raw, str) or len(raw) > 1024:
            raise ValueError('Unsupported tag')
        fields = raw.split('-')
        if len(fields) != 3 or not all(re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', f) for f in fields):
            raise ValueError('Malformed tag')
        groups = [field.split('.') for field in fields]
        if len(groups[0]) * len(groups[1]) * len(groups[2]) > MAX_TAGS:
            raise ValueError('Compressed tag limit')
        tags.update('-'.join(parts) for parts in product(*groups))
        if len(tags) > MAX_TAGS:
            raise ValueError('Tag limit')
    if not tags:
        raise ValueError('No tags')
    return tuple(sorted(tags))


def tag_check(tag, runtime):
    """Tag eligibility only, never proof that code or a binary can run."""
    python, abi, target = tag.split('-')
    major, minor = runtime['python_version'][:2]
    current = runtime.get('platform_tag')
    system = runtime.get('sys_platform')
    if runtime.get('status') != 'available':
        return 'unknown'
    # Native platform comparisons deliberately stop before libc/deployment targets.
    if target == 'any':
        platform_match = True if abi == 'none' else None
    elif target in ('win32', 'win_amd64', 'win_arm64'):
        platform_match = current == target if system == 'win32' and current in ('win32', 'win_amd64', 'win_arm64') else False if system in ('linux', 'darwin') else None
    elif re.fullmatch(r'(?:linux|manylinux[^_]*|manylinux_[0-9]+_[0-9]+|musllinux_[0-9]+_[0-9]+)_[A-Za-z0-9_]+', target):
        platform_match = False if system in ('win32', 'darwin') else (True if system == 'linux' and target == current and target.startswith('linux_') else None)
    elif re.fullmatch(r'macosx_[0-9]+_[0-9]+_[A-Za-z0-9_]+', target):
        platform_match = False if system in ('win32', 'linux') else None
    else:
        platform_match = None
    if platform_match is True and target != 'any':
        if runtime.get('platform_tag_overridden'):
            platform_match = None
        elif target.startswith('linux_'):
            arch = target[len('linux_'):]
            bits = (64 if arch in ('x86_64', 'aarch64', 'ppc64', 'ppc64le', 's390x', 'riscv64', 'loongarch64')
                    else 32 if arch in ('i386', 'i486', 'i586', 'i686', 'armv6l', 'armv7l', 'armv8l', 'ppc', 's390', 'riscv32')
                    else None)
            if bits is None or runtime.get('pointer_bits') != bits:
                platform_match = None
    cp = re.fullmatch(r'cp([23])([0-9]{1,2})', python)
    py = re.fullmatch(r'py([23])([0-9]{1,2})?', python)
    if py and abi == 'none':
        interpreter_match = int(py[1]) == major and (py[2] is None or int(py[2]) <= minor)
        abi_match = True
    elif cp and runtime.get('implementation') == 'cpython':
        requested = (int(cp[1]), int(cp[2]))
        if abi == 'abi3':
            if runtime.get('gil_disabled') or runtime.get('native_abi') is None:
                interpreter_match, abi_match = None, None
            else:
                interpreter_match = requested[0] == major and (3, 2) <= requested <= (major, minor)
                abi_match = True
        elif abi == 'none':
            interpreter_match, abi_match = requested == (major, minor), True
        elif re.fullmatch(r'cp[23][0-9]{1,2}[dt]*', abi) and runtime.get('native_abi'):
            interpreter_match = requested == (major, minor)
            # CPython 3.8+ debug builds also accept the corresponding release ABI.
            supported = {runtime['native_abi'], runtime['native_abi'].replace('d', '')}
            abi_match = abi in supported
        else:
            interpreter_match, abi_match = None, None
    else:
        interpreter_match, abi_match = None, None
    dimensions = (interpreter_match, abi_match, platform_match)
    return 'tag_mismatch' if False in dimensions else 'tag_match' if all(x is True for x in dimensions) else 'unknown'


def check_tags(tags, runtime):
    checks = tuple({'tag': tag, 'result': tag_check(tag, runtime)} for tag in tags)
    results = {check['result'] for check in checks}
    status = 'tag_match' if 'tag_match' in results else 'unknown' if 'unknown' in results else 'tag_mismatch'
    return {'tag_check': status, 'tag_checks': checks, 'runtime_evidence_ref': 'environment:abi_platform',
            'tag_check_runtime_scope': 'current_interpreter_only',
            'compatibility_scope': 'metadata_tags_only', 'runtime_compatibility': 'unknown'}


def wheel_filename(filename):
    """Parse a supplied basename, not an archive or a path to open."""
    if not isinstance(filename, str) or len(filename) > 1024 or not filename.endswith('.whl'):
        raise ValueError('Unsupported filename')
    fields = filename[:-4].split('-')
    if len(fields) not in (5, 6):
        raise ValueError('Malformed wheel filename')
    name, version = fields[:2]
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._]*[A-Za-z0-9])?', name) or not re.fullmatch(VERSION, version):
        raise ValueError('Unsupported wheel identity')
    if len(fields) == 6 and not re.fullmatch(r'[0-9][A-Za-z0-9_]*', fields[2]):
        raise ValueError('Invalid build tag')
    return {'filename': filename, 'name': name, 'version': version,
            'build': fields[2] if len(fields) == 6 else None,
            'raw_tags': ('-'.join(fields[-3:]),), 'tags': expand_tags(('-'.join(fields[-3:]),))}


def extract_wheel_log_facts(text):
    """Keep explicit pip artifact mentions without treating them as selections."""
    artifacts, limitations = [], []
    for line_number, line in enumerate(text.splitlines(), 1):
        if len(line) > 4096:
            continue
        # pip's indent_log prefixes ordinary artifact messages with spaces.
        # Accept only horizontal indentation, leaving command/prose prefixes intact.
        line = line.lstrip(' \t')
        rejected = re.fullmatch(r'ERROR: (\S+\.whl) is not a supported wheel on this platform\.', line)
        seen = re.fullmatch(r'(Processing|Downloading|Using cached) (\S+\.whl)(?: \([^\r\n]*\))?', line)
        if not rejected and not seen:
            continue
        token = rejected[1] if rejected else seen[2]
        # URL/path is retained only as text; no decode, traversal or archive access.
        basename = token.replace('\\', '/').rsplit('/', 1)[-1]
        try:
            facts = wheel_filename(basename)
            facts.update(status='available')
        except ValueError:
            facts = {'filename': basename, 'status': 'unknown', 'tags': (), 'limitation': 'unsupported_filename'}
        facts.update(evidence_origin=f'line:{line_number}', artifact_token=token,
                     observation='logged_rejection' if rejected else 'artifact_mention')
        artifacts.append(facts)
        if len(artifacts) > MAX_TARGETS:
            artifacts = []
            limitations.append('artifact_limit')
            break
    return {'wheel_artifacts': tuple(artifacts), 'wheel_limitations': tuple(limitations)}


def _wheel_metadata(item, runtime):
    facts = {'status': 'unavailable', 'name': item.metadata.get('name'),
             'source_evidence_ref': item.evidence_id, 'evidence_origin': None,
             'interpreter_ref': 'environment:interpreter', 'tags': (),
             'runtime_scope': 'current_interpreter_only', 'runtime_compatibility': 'unknown'}
    location = None
    try:
        metadata = item.metadata.get('metadata_location')
        directory = item.metadata.get('dist_info_location')
        if not metadata or not directory:
            facts['limitation'] = 'no_dist_info_wheel_source'
        else:
            parent = Path(directory)
            if parent.suffix.lower() != '.dist-info' or Path(metadata) != parent / 'METADATA':
                raise ValueError('Unproven WHEEL owner')
            path = parent / 'WHEEL'
            location = str(path)
            facts['evidence_origin'] = location
            parsed = Parser().parsestr(_read(path).decode('utf-8'))
            if parsed.defects or parsed.get_payload().strip():
                raise ValueError('Invalid wheel headers')
            def unique(key):
                values = parsed.get_all(key, ())
                if len(values) != 1:
                    raise ValueError('Missing/conflicting wheel header')
                return values[0]
            version, pure = unique('Wheel-Version'), unique('Root-Is-Purelib')
            facts.update(wheel_version=version, root_is_purelib=pure)
            if version != '1.0' or pure not in ('true', 'false'):
                facts['limitation'] = 'unsupported_wheel_headers'
            else:
                raw = tuple(parsed.get_all('Tag', ()))
                tags = expand_tags(raw)
                facts.update(status='available', raw_tags=raw, tags=tags, **check_tags(tags, runtime))
                if item.metadata.get('status') != 'available':
                    facts.update(status='unknown', limitation='distribution_identity_unavailable')
    except Exception as error:
        facts.update(limitation='wheel_not_read_or_parsed', error_type=type(error).__name__)
    return Evidence('', 'python_wheel', 'installed_wheel_metadata',
                    f"Installed WHEEL observation: {facts['status']}; runtime compatibility remains unknown.",
                    location=location, metadata=facts)


def _api(item, origins):
    module, symbol = item.metadata.get('source_module'), item.metadata.get('imported_symbol')
    facts = {'module': module, 'requested_symbol': symbol, 'status': 'unavailable',
             'source_evidence_ref': item.evidence_id, 'api_compatibility': 'unknown',
             'runtime_scope': 'current_interpreter_static_source', 'evidence_origin': None}
    origin = origins.get(module)
    path = origin.metadata.get('origin') if origin else None
    try:
        if not origin or origin.metadata.get('status') != 'available' or not isinstance(path, str) or not path.endswith('.py'):
            facts['limitation'] = 'no_plain_python_source'
        elif not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z_]\w*', symbol, re.ASCII):
            facts['limitation'] = 'unsupported_symbol'
        else:
            facts.update(module_origin_ref=origin.evidence_id, evidence_origin=path)
            data = _read(Path(path))
            tree = ast.parse(_source_text(data))
            if len(list(islice(ast.walk(tree), MAX_AST_NODES + 1))) > MAX_AST_NODES:
                raise ValueError('AST node limit')
            bindings, exports = [], []
            for node in tree.body:
                names = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                             for alias in node.names]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [target.id for target in targets if isinstance(target, ast.Name)]
                    if '__all__' in names and isinstance(node.value, (ast.List, ast.Tuple)):
                        if all(isinstance(value, ast.Constant) and isinstance(value.value, str) for value in node.value.elts):
                            exports.append({'line': node.lineno, 'contains_symbol': any(v.value == symbol for v in node.value.elts)})
                if symbol in names:
                    bindings.append({'line': node.lineno, 'syntax': type(node).__name__})
            facts.update(status='available', direct_syntax_bindings=tuple(bindings), literal_all_observations=tuple(exports),
                         symbol_observation='direct_syntax_seen' if bindings else 'not_seen_in_direct_syntax',
                         limitation='static_syntax_cannot_establish_runtime_exports')
    except Exception as error:
        facts.update(limitation='source_not_read_or_parsed', error_type=type(error).__name__)
    return Evidence('', 'python_api', 'stdlib:ast',
                    f"Static API observation for {module}.{symbol}: {facts['status']}; API compatibility remains unknown.",
                    location=path if facts['evidence_origin'] else None, metadata=facts)


def collect_compatibility_evidence(evidence):
    """Use only already selected distributions, explicit symbols and supplied logs."""
    distributions = [e for e in evidence if e.kind == 'python_distribution']
    requests = [e for e in evidence if e.kind == 'python_import_failure' and e.metadata.get('imported_symbol')]
    logs = [e for e in evidence if e.kind == 'provided_log' and e.metadata.get('input_type') == 'install_log'
            and (e.metadata.get('wheel_artifacts') or e.metadata.get('wheel_limitations'))]
    if not distributions and not requests and not logs:
        return ()
    runtime = runtime_facts()
    output = [Evidence('environment:abi_platform', 'python_abi_platform', 'stdlib:sysconfig',
                       'Current interpreter build facts; tags do not establish runtime compatibility.', metadata=runtime)]
    origins = {e.metadata.get('module'): e for e in evidence if e.kind == 'python_module_origin'}
    targets = ([('distribution', e) for e in distributions] + [('api', e) for e in requests]
               + [('log_wheel', (log, item)) for log in logs for item in log.metadata.get('wheel_artifacts', ())])
    for kind, item in targets[:MAX_TARGETS]:
        if kind == 'distribution':
            output.append(_wheel_metadata(item, runtime))
        elif kind == 'api':
            output.append(_api(item, origins))
        else:
            log, artifact = item
            facts = dict(artifact, source_evidence_ref=log.evidence_id, runtime_scope='supplied_log_only',
                         runtime_compatibility='unknown')
            if facts['status'] == 'available':
                facts.update(check_tags(facts['tags'], runtime))
            output.append(Evidence('', 'python_wheel', log.source,
                                   'Supplied log wheel artifact; no installation or historical runtime claim.',
                                   location=log.location, metadata=facts))
    if len(targets) > MAX_TARGETS:
        output.append(Evidence('', 'python_compatibility_collection', 'python_pack', 'Compatibility target limit.',
                               metadata={'status': 'unavailable', 'limitation': 'target_limit'}))
    for log in logs[:MAX_TARGETS]:
        for reason in log.metadata.get('wheel_limitations', ()):
            output.append(Evidence('', 'python_compatibility_collection', log.source, 'Wheel log limitation.',
                                   location=log.location, metadata={'status': 'unavailable', 'limitation': reason,
                                                                   'source_evidence_ref': log.evidence_id}))
    from dataclasses import replace
    return tuple(item if index == 0 else replace(item, evidence_id=f'compatibility:{index}') for index, item in enumerate(output))
