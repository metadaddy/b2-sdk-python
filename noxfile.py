######################################################################
#
# File: noxfile.py
#
# Copyright 2020 Backblaze Inc. All Rights Reserved.
#
# License https://www.backblaze.com/using_b2_code.html
#
######################################################################
from __future__ import annotations

import os
import pathlib
import platform
import re
import subprocess

import nox

UPSTREAM_REPO_URL = 'git@github.com:Backblaze/b2-sdk-python.git'

# Required for PDM to use nox's virtualenvs
os.environ.update({'PDM_IGNORE_SAVED_PYTHON': '1'})

CI = os.environ.get('CI') is not None
NOX_PYTHONS = os.environ.get('NOX_PYTHONS')
_NOX_EXTRAS = os.environ.get('NOX_EXTRAS')
NOX_EXTRAS = [[]] if _NOX_EXTRAS is None else list(filter(None, [_NOX_EXTRAS.split(',')]))

PYTHON_VERSIONS = (
    [
        'pypy3.9',
        'pypy3.10',
        '3.7',
        '3.8',
        '3.9',
        '3.10',
        '3.11',
        '3.12',
        '3.13',
    ]
    if NOX_PYTHONS is None
    else NOX_PYTHONS.split(',')
)


def _detect_python_nox_id() -> str:
    major, minor, *_ = platform.python_version_tuple()
    python_nox_id = f'{major}.{minor}'
    if platform.python_implementation() == 'PyPy':
        python_nox_id = f'pypy{python_nox_id}'
    return python_nox_id


if CI and not NOX_PYTHONS:
    # this is done to allow it to work even if `nox -p` was passed to nox
    PYTHON_VERSIONS = [_detect_python_nox_id()]
    print(f'CI job mode; using provided interpreter only; PYTHON_VERSIONS={PYTHON_VERSIONS!r}')

PYTHON_DEFAULT_VERSION = PYTHON_VERSIONS[-2] if len(PYTHON_VERSIONS) > 1 else PYTHON_VERSIONS[0]

PY_PATHS = ['b2sdk', 'test', 'noxfile.py']

nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = [
    'lint',
    'test',
]


def pdm_install(session: nox.Session, *args: str, dev: bool = True) -> None:
    # dev dependencies are installed by default
    prod_args = [] if dev else ['--prod']
    group_args = []
    for group in args:
        group_args.extend(['--group', group])
    session.run('pdm', 'install', *prod_args, *group_args, external=True)


def skip_coverage(python_version: str | None) -> bool:
    return (python_version or platform.python_implementation()).lower().startswith('pypy')


@nox.session(name='format', python=PYTHON_DEFAULT_VERSION)
def format_(session):
    """Lint the code and apply fixes in-place whenever possible."""
    pdm_install(session, 'lint')
    session.run('ruff', 'check', '--fix', *PY_PATHS)
    session.run('ruff', 'format', *PY_PATHS)
    # session.run(
    #     'docformatter',
    #     '--in-place',
    #     '--recursive',
    #     '--wrap-summaries=100',
    #     '--wrap-descriptions=100',
    #     *PY_PATHS,
    # )


@nox.session(python=PYTHON_DEFAULT_VERSION)
def lint(session):
    """Run linters in readonly mode."""
    # We need to install 'doc' group because liccheck needs to inspect it.
    pdm_install(session, 'doc', 'lint', 'full')
    session.run('ruff', 'check', *PY_PATHS)
    session.run('ruff', 'format', *PY_PATHS)
    # session.run(
    #     'docformatter',
    #     '--check',
    #     '--recursive',
    #     '--wrap-summaries=100',
    #     '--wrap-descriptions=100',
    #     *PY_PATHS,
    # )

    session.run('pytest', 'test/static')
    session.run('liccheck', '-s', 'pyproject.toml')

    # Check if the lockfile is up to date
    session.run('pdm', 'lock', '--check', external=True)


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize('extras', NOX_EXTRAS)
def unit(session, extras):
    """Run unit tests."""
    pdm_install(session, 'test', *extras)
    args = ['--doctest-modules', '-n', 'auto']
    if not skip_coverage(session.python):
        args += ['--cov=b2sdk', '--cov-branch', '--cov-report=xml']
    # TODO: Use session.parametrize for apiver
    session.run('pytest', '--api=v3', *args, *session.posargs, 'test/unit')
    if not skip_coverage(session.python):
        args += ['--cov-append']
    session.run('pytest', '--api=v2', *args, *session.posargs, 'test/unit')
    session.run('pytest', '--api=v1', *args, *session.posargs, 'test/unit')
    session.run('pytest', '--api=v0', *args, *session.posargs, 'test/unit')

    if not skip_coverage(session.python) and not session.posargs:
        session.notify('cover')


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize('extras', NOX_EXTRAS)
def integration(session, extras):
    """Run integration tests."""
    pdm_install(session, 'test', *extras)
    session.run('pytest', '-s', *session.posargs, 'test/integration')


@nox.session(python=PYTHON_DEFAULT_VERSION)
def cleanup_old_buckets(session):
    """Remove buckets from previous test runs."""
    pdm_install(session, 'test')
    session.run('python', '-m', 'test.integration.cleanup_buckets')


@nox.session(python=PYTHON_VERSIONS)
def test(session):
    """Run all tests."""
    if session.python:
        session.notify(f'unit-{session.python}')
        session.notify(f'integration-{session.python}')
    else:
        session.notify('unit')
        session.notify('integration')


@nox.session
def cover(session):
    """Perform coverage analysis."""
    pdm_install(session, 'test')
    session.run('coverage', 'report', '--fail-under=75', '--show-missing', '--skip-covered')
    session.run('coverage', 'erase')


@nox.session(python=PYTHON_DEFAULT_VERSION)
def build(session):
    """Build the distribution."""
    session.run('pdm', 'build', external=True)

    # Set outputs for GitHub Actions
    if CI:
        with open(os.environ['GITHUB_OUTPUT'], 'a') as github_output:
            # Path have to be specified with unix style slashes even for windows,
            # otherwise glob won't find files on windows in action-gh-release.
            print('asset_path=dist/*', file=github_output)

            version = os.environ['GITHUB_REF'].replace('refs/tags/v', '')
            print(f'version={version}', file=github_output)


@nox.session(python=PYTHON_DEFAULT_VERSION)
def doc(session):
    """Build the documentation."""
    pdm_install(session, 'doc')
    session.cd('doc')
    sphinx_args = ['-b', 'html', '-T', '-W', 'source', 'build/html']
    session.run('rm', '-rf', 'build', external=True)

    if not session.interactive:
        session.run('sphinx-build', *sphinx_args)
        session.notify('doc_cover')
    else:
        sphinx_args[-2:-2] = [
            '-E',
            '--open-browser',
            '--watch',
            '../b2sdk',
            '--ignore',
            '*.pyc',
            '--ignore',
            '*~',
        ]
        session.run('sphinx-autobuild', *sphinx_args)


@nox.session
def doc_cover(session):
    """Perform coverage analysis for the documentation."""
    pdm_install(session, 'doc')
    session.cd('doc')
    sphinx_args = ['-b', 'coverage', '-T', '-W', 'source', 'build/coverage']
    report_file = 'build/coverage/python.txt'
    session.run('sphinx-build', *sphinx_args)
    session.run('cat', report_file, external=True)

    with open('build/coverage/python.txt') as fd:
        # If there is no undocumented files, the report should have only 2 lines (header)
        if sum(1 for _ in fd) != 2:
            session.error('sphinx coverage has failed')


@nox.session(python=PYTHON_DEFAULT_VERSION)
def make_release_commit(session):
    """
    Runs `towncrier build`, commits changes, tags, all that is left to do is pushing
    """
    if session.posargs:
        version = session.posargs[0]
    else:
        session.error('Provide -- {release_version} (X.Y.Z - without leading "v")')

    if not re.match(r'^\d+\.\d+\.\d+$', version):
        session.error(
            f'Provided version="{version}". Version must be of the form X.Y.Z where '
            f'X, Y and Z are integers'
        )

    local_changes = subprocess.check_output(['git', 'diff', '--stat'])
    if local_changes:
        session.error('Uncommitted changes detected')

    current_branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode()
    if current_branch != 'master':
        session.log('WARNING: releasing from a branch different than master')

    pdm_install(session, 'release')
    session.run('towncrier', 'build', '--yes', '--version', version)

    session.log(
        f'CHANGELOG updated, changes ready to commit and push\n'
        f'    git remote add upstream {UPSTREAM_REPO_URL!r} 2>/dev/null || git remote get-url upstream\n'
        f'    git commit -m "release {version}"\n'
        f'    git push upstream {current_branch}\n'
        f'Wait for a CI workflow to complete successfully, before triggering CD by pushing a tag.\n'
        f'    git tag v{version}\n'
        f'    git push upstream v{version}\n'
        f'Wait for a CD workflow to complete successfully, indicates the release is done.'
    )


def load_allowed_change_types(
    project_toml: pathlib.Path = pathlib.Path('./pyproject.toml'),
) -> set[str]:
    """
    Load the list of allowed change types from the pyproject.toml file.
    """
    import tomllib

    configuration = tomllib.loads(project_toml.read_text())
    return set(entry['directory'] for entry in configuration['tool']['towncrier']['type'])


def is_changelog_filename_valid(filename: str, allowed_change_types: set[str]) -> tuple[bool, str]:
    """
    Validates whether the given filename matches our rules.
    Provides information about why it doesn't match them.
    """
    error_reasons = []

    wanted_extension = 'md'
    try:
        description, change_type, extension = filename.rsplit('.', maxsplit=2)
    except ValueError:
        # Not enough values to unpack.
        return False, 'Doesn\'t follow the "<description>.<change_type>.md" pattern.'

    # Check whether the filename ends with .md.
    if extension != wanted_extension:
        error_reasons.append(f"Doesn't end with {wanted_extension} extension.")

    # Check whether the change type is valid.
    if change_type not in allowed_change_types:
        error_reasons.append(
            f"Change type '{change_type}' doesn't match allowed types: {allowed_change_types}."
        )

    # Check whether the description makes sense.
    try:
        int(description)
    except ValueError:
        if description[0] != '+':
            error_reasons.append("Doesn't start with a number nor a plus sign.")

    return len(error_reasons) == 0, ' / '.join(error_reasons) if error_reasons else ''


def is_changelog_entry_valid(file_content: str) -> tuple[bool, str]:
    """
    We expect the changelog entry to be a valid sentence in the English language.
    This includes, but not limits to, providing a capital letter at the start
    and the full-stop character at the end.

    Note: to do this "properly", tools like `nltk` and `spacy` should be used.
    """
    error_reasons = []

    # Check whether the first character is a capital letter.
    # Not allowing special characters nor numbers at the very start.
    if not file_content[0].isalpha() or not file_content[0].isupper():
        error_reasons.append('The first character is not a capital letter.')

    # Check if the last character is a full-stop character.
    if file_content.strip()[-1] != '.':
        error_reasons.append('The last character is not a full-stop character.')

    return len(error_reasons) == 0, ' / '.join(error_reasons) if error_reasons else ''


@nox.session(python=PYTHON_DEFAULT_VERSION)
def towncrier_check(session):
    """
    Check whether all the entries in the changelog.d follow the expected naming convention
    as well as some basic rules as to their format.
    """
    expected_non_md_files = {'.gitkeep'}
    allowed_change_types = load_allowed_change_types()

    is_error = False

    for filename in pathlib.Path('./changelog.d/').glob('*'):
        # If that's an expected file, it's all right.
        if filename.name in expected_non_md_files:
            continue

        # Check whether the file matches the expected pattern.
        is_valid, error_message = is_changelog_filename_valid(filename.name, allowed_change_types)
        if not is_valid:
            session.log(f"File {filename.name} doesn't match the expected pattern: {error_message}")
            is_error = True
            continue

        # Check whether the file isn't too big.
        if filename.lstat().st_size > 16 * 1024:
            session.log(
                f'File {filename.name} content is too big – it should be smaller than 16kB.'
            )
            is_error = True
            continue

        # Check whether the file can be loaded as UTF-8 file.
        try:
            file_content = filename.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            session.log(f'File {filename.name} is not a valid UTF-8 file.')
            is_error = True
            continue

        # Check whether the content of the file is anyhow valid.
        is_valid, error_message = is_changelog_entry_valid(file_content)
        if not is_valid:
            session.log(f'File {filename.name} is not a valid changelog entry: {error_message}')
            is_error = True
            continue

    if is_error:
        session.error(
            'Found errors in the changelog.d directory. Check logs above for more information'
        )
