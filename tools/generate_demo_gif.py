"""Render a README GIF from the installed CLI; Pillow is a local docs tool only.

Run from any directory with a Python environment that already provides Pillow:
    python -B tools/generate_demo_gif.py
The project .venv must already contain repo-rescue. No packages are installed.
"""

import getpass
import math
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples' / 'fixtures' / 'missing-module'
OUTPUT = ROOT / 'docs' / 'assets' / 'repo-rescue-demo.gif'
COMMAND = 'repo-rescue examples/fixtures/missing-module --run-startup-probe'
WIDTH = 1200
FONT_SIZE = 18
LINE_HEIGHT = 24
PADDING = 28
BACKGROUND = '#10151d'
FOREGROUND = '#d6deeb'


def fixture_snapshot():
    """Include directories and exact bytes/mtimes, without writing a snapshot."""
    return {
        path.relative_to(FIXTURE).as_posix():
        (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
        for path in FIXTURE.rglob('*')
    }


def privacy_issues(text):
    """Fail closed on public-output paths and common personal/credential markers."""
    patterns = [
        r'(?i)\b[A-Z]:[\\/]',
        r'\\\\[^\\\s]+\\',
        r'(?<![\w.])/(?:[^\s/]+/)+[^\s]*',
        r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
        r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])',
        r'(?i)\b(?:[a-f0-9]{1,4}:){2,}[a-f0-9:]+\b',
        r'(?i)\b(?:secret|token|proxy|password|credential|bearer)\b',
        r'-----BEGIN .*PRIVATE KEY-----',
        r'\b(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AKIA[0-9A-Z]{16})\b',
    ]
    for value in (getpass.getuser(), os.environ.get('USERNAME'),
                  os.environ.get('COMPUTERNAME')):
        if value:
            patterns.append(r'(?i)(?<!\w)' + re.escape(value) + r'(?!\w)')
    checks = [re.compile(pattern) for pattern in patterns]
    return [(number, line) for number, line in enumerate(text.splitlines(), 1)
            if any(check.search(line) for check in checks)]


def capture_demo():
    cli = ROOT / '.venv' / ('Scripts/repo-rescue.exe' if os.name == 'nt' else 'bin/repo-rescue')
    if not cli.is_file():
        raise RuntimeError('The project .venv must already contain the installed repo-rescue CLI.')
    version = subprocess.run([str(cli), '--version'], cwd=ROOT, capture_output=True,
                             text=True, timeout=10, shell=False, stdin=subprocess.DEVNULL)
    if version.returncode != 0:
        raise RuntimeError('The installed CLI version check failed.')
    before, environment = fixture_snapshot(), dict(os.environ)
    started = perf_counter()
    result = subprocess.run([str(cli), 'examples/fixtures/missing-module', '--run-startup-probe'],
                            cwd=ROOT, capture_output=True, text=True, timeout=15,
                            shell=False, stdin=subprocess.DEVNULL)
    elapsed = perf_counter() - started
    if before != fixture_snapshot() or environment != dict(os.environ):
        raise RuntimeError('The demo changed fixture files, mtimes, or the parent environment.')
    if any(path.name == '__pycache__' for path in FIXTURE.rglob('*')):
        raise RuntimeError('The fixture contains a bytecode cache.')
    issues = privacy_issues(version.stdout + version.stderr + result.stdout + result.stderr)
    if issues:
        for number, line in issues:
            print(f'Privacy check blocked GIF generation at captured line {number}: {line}', file=sys.stderr)
        raise RuntimeError('Captured output is not suitable for public recording.')
    if result.returncode != 1 or result.stderr:
        raise RuntimeError('The fixture did not produce the expected diagnostic exit code 1 and empty stderr.')
    required = ('[ERROR] Python import:', '[ERROR] Startup probe exited with code 1.',
                'reporescue_fixture_missing_dependency_xyz', 'Root cause:',
                'Repair preview:', 'Verification (planned, not run):',
                'No repair actions were executed.')
    if not all(value in result.stdout for value in required):
        raise RuntimeError('Required demo findings are absent; no GIF was generated.')
    return version.stdout.strip(), result, elapsed


def select_output(stdout):
    """Select entire captured lines; never write replacement diagnostic text."""
    selected = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if (line.startswith(('[CAUTION]', 'Project code may', 'Project:', '[ERROR]',
                             '  Missing import:', '  ModuleNotFoundError names',
                             '  Suggested repair', '  - Confirm the verification uses',
                             '  - Using the intended interpreter', '  - After explicit confirmation'))
                or line in ('RepoRescue', 'Findings:', 'Root cause:', 'Repair preview:',
                            'Verification (planned, not run):',
                            'No repair actions were executed.',
                            'RepoRescue currently performs limited checks.')):
            selected.append(line)
    return selected


def choose_font(ImageFont):
    fonts = [
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        for name in ('CascadiaMono.ttf', 'CascadiaCode.ttf', 'consola.ttf', 'cour.ttf')
    ] + [Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')]
    for path in fonts:
        if path.is_file():
            return ImageFont.truetype(str(path), FONT_SIZE), path.name
    raise RuntimeError('No existing supported monospace font was found.')


def line_color(line):
    if line.startswith('[ERROR]'):
        return '#ff7d87'
    if line.startswith('[CAUTION]') or line.startswith('Project code may'):
        return '#efbf68'
    if line in ('RepoRescue', 'Findings:', 'Root cause:', 'Repair preview:', 'Verification (planned, not run):'):
        return '#76cfe5'
    if 'Missing import:' in line:
        return '#c0a4ee'
    if line == 'No repair actions were executed.':
        return '#efbf68'
    return FOREGROUND


def generate_gif(Image, ImageDraw, ImageFont, stdout, elapsed):
    font, font_name = choose_font(ImageFont)
    columns = int((WIDTH - 2 * PADDING) // font.getlength('M'))
    rows = []
    for line in select_output(stdout):
        if line in ('Findings:', 'Root cause:', 'Repair preview:', 'Verification (planned, not run):',
                    'No repair actions were executed.'):
            rows.append(('', FOREGROUND))
        wrapped = textwrap.wrap(line, width=columns, subsequent_indent='    ',
                                break_long_words=False, break_on_hyphens=False,
                                replace_whitespace=False, drop_whitespace=True)
        rows.extend((part, line_color(line)) for part in wrapped)
    height = max(700, math.ceil((108 + len(rows) * LINE_HEIGHT + PADDING) / 20) * 20)
    if height > 900:
        raise RuntimeError('Captured output is too tall for this README layout.')

    def frame(command, visible):
        image = Image.new('RGB', (WIDTH, height), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH, 40), fill='#1b2431')
        draw.text((PADDING, 10), 'RepoRescue | terminal demo', font=font, fill='#9baabc')
        draw.text((PADDING, 58), 'PS> ', font=font, fill='#76cfe5')
        draw.text((PADDING + font.getlength('PS> '), 58), command, font=font, fill='#edf3fc')
        for index, (line, color) in enumerate(rows[:visible]):
            draw.text((PADDING, 108 + index * LINE_HEIGHT), line, font=font, fill=color)
        return image

    images, durations = [frame('', 0)], [500]
    chunks = math.ceil(len(COMMAND) / 3)
    for index in range(1, chunks + 1):
        images.append(frame(COMMAND[:index * 3], 0))
        durations.append(100)
    durations[-1] += max(100, round(elapsed * 1000 / 10) * 10)
    for visible in range(1, len(rows) + 1):
        images.append(frame(COMMAND, visible))
        durations.append(80)
    durations[-1] += 5500
    # One shared small palette keeps colors stable and permits delta-frame encoding.
    palette = images[-1].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUTPUT, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=1)
    with Image.open(OUTPUT) as gif:
        frame_count = gif.n_frames
        duration = sum(gif.seek(i) or gif.info.get('duration', 0) for i in range(frame_count))
        if gif.format != 'GIF' or frame_count <= 1 or OUTPUT.stat().st_size >= 8 * 1024 * 1024:
            raise RuntimeError('Generated GIF did not pass format/frame/size validation.')
        size = gif.size
    return size, duration / 1000, frame_count, font_name


def main():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('Demo generation requires Pillow as a local documentation tool.', file=sys.stderr)
        return 1
    try:
        version, result, elapsed = capture_demo()
        size, duration, frames, font_name = generate_gif(Image, ImageDraw, ImageFont, result.stdout, elapsed)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Demo generation failed: {error}', file=sys.stderr)
        return 1
    print(f'CLI version: {version}')
    print(f'Command: {COMMAND}')
    print(f'CLI exit code: {result.returncode}; execution duration: {elapsed:.3f}s')
    print('Privacy check: passed; fixture files/mtimes and parent environment unchanged.')
    print(f'GIF: docs/assets/repo-rescue-demo.gif; {size[0]}x{size[1]}; {duration:.2f}s; {frames} frames')
    print(f'File size: {OUTPUT.stat().st_size} bytes; font: {font_name}')
    print('Display uses selected real output lines with wrapping; full stdout/stderr are privacy-checked.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
