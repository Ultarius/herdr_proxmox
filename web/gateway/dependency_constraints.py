"""Compare a declared version constraint against a measured version.

Small on purpose, and honest about its edges. It evaluates the notations this
gateway reads, and answers `undecidable` for anything else. A wrong `satisfied`
authorises work under a rule nobody evaluated; `undecidable` asks a human.

Interpretation is per notation, never shared. npm's `^1.2` and PEP 440's `~=1.2`
look similar and mean different things, so an operator outside its notation's
set is refused rather than reinterpreted. The numeric comparison underneath is
shared, and is the only thing that is.

Desugaring follows the published rules:

* npm ranges — <https://github.com/npm/node-semver#advanced-range-syntax>
  `1.2` := `>=1.2.0 <1.3.0`; `>1` := `>=2.0.0`; `<=1.2` := `<1.3.0`;
  `^0.0.3` := `>=0.0.3 <0.0.4`; `^0.0` := `>=0.0.0 <0.1.0`;
  `~1` := `>=1.0.0 <2.0.0`; `>=1.2.x` := `>=1.2.0`
* PEP 440 — <https://packaging.python.org/en/latest/specifications/version-specifiers/>
  `~=3.8.1` := `>=3.8.1, ==3.8.*`; `~=3.8` := `>=3.8, ==3.*`;
  `==3.8` matches `3.8` and `3.8.0` by zero padding
* RubyGems — `~> 2.2` := `>=2.2, <3.0`; `~> 2.2.3` := `>=2.2.3, <2.3.0`
* Poetry — `^` and `~` desugar as npm's do above, and a bare version is the
  exact requirement Poetry spells without an operator
  (<https://python-poetry.org/docs/dependency-specification/#version-constraints>).
  Comparators and compatible release (`~=`) use Python exact-version semantics,
  not npm partial ranges. Commas conjoin clauses.

Prereleases, build metadata, epochs, hyphen ranges, `workspace:` and `catalog:`
protocols, git and file references and arbitrary equality are all `undecidable`.
"""
import re

SATISFIED = 'satisfied'
VIOLATED = 'violated'
UNDECIDABLE = 'undecidable'

# Operators each notation understands. `=` and `==` are accepted wherever
# equality is, because the difference is spelling rather than meaning.
OPERATORS = {
    'semver': ('>=', '<=', '>', '<', '=', '==', '^', '~'),
    'pep440': ('>=', '<=', '>', '<', '==', '!=', '~='),
    'poetry': ('>=', '<=', '>', '<', '=', '==', '!=', '^', '~', '~='),
    'composer': ('>=', '<=', '>', '<', '=', '==', '^', '~'),
    'pub': ('>=', '<=', '>', '<', '^'),
    'dotnet': ('>=', '<=', '>', '<', '='),
    'gomod': ('>=', '<=', '>', '<'),
    'rubygems': ('>=', '<=', '>', '<', '~>'),
}
# Version-file requests are families when a patch is omitted, unlike PEP 440.
OPERATORS['python-version'] = OPERATORS['pep440']


def python_family(value):
    values, wildcard = _partial(value)
    return values if values and len(values) < 3 and wildcard is None else None


def family_verdict(selection, constraint, notation):
    """Compare a Python family with numeric constraints without choosing a patch."""
    family = python_family(selection)
    if not family:
        return evaluate(constraint, selection, notation)
    # Predicate truth changes only at stated numeric boundaries. Test those
    # points and adjacent releases at every component inside the family. Major
    # requests can overlap a constraint beyond an excluded entire minor family.
    candidates = {tuple(_floor(family))}
    for token in re.findall(r'\d+(?:\.\d+)*', str(constraint)):
        values, _ = _partial(token)
        if len(values) > 3:
            return UNDECIDABLE
        point = _floor(values)
        for index in range(3):
            for delta in (-1, 0, 1):
                adjacent = point[:]
                adjacent[index] += delta
                if adjacent[index] >= 0:
                    candidates.add(tuple(adjacent[:index + 1] + [0] * (2 - index)))
    results = [evaluate(constraint, '.'.join(map(str, point)), notation)
               for point in candidates if list(point[:len(family)]) == family]
    if SATISFIED in results:
        return SATISFIED
    return UNDECIDABLE if UNDECIDABLE in results else VIOLATED

TOKEN = re.compile(r'(>=|<=|>|<|==|!=|=|\^|~=|~>|~)?(.+)')
OPERATOR_ONLY = re.compile(r'(>=|<=|>|<|==|!=|=|\^|~=|~>|~)')
# The first version-looking run in arbitrary text, for reading what a probed
# executable reported. A bare major counts: `go version` prints `1.22.3` and
# some tools print only `1`.
VERSION_ONLY = re.compile(r'\d+(?:\.\d+)*')
WILDCARD = ('*', 'x', 'X')
ANY = ('',) + WILDCARD
EQUALITY = ('=', '==')


def _partial(text):
    """`(values, wildcard)` for a partial version, or `(None, None)`.

    `1.2` -> `([1, 2], None)`; `1.2.x` -> `([1, 2], 2)`; `*` -> `([], 0)`.
    """
    text = str(text).strip().lstrip('vV')
    if not text:
        return [], 0
    values = []
    for index, chunk in enumerate(text.split('.')):
        if chunk in WILDCARD:
            return values, index
        if not chunk.isdigit():
            return None, None
        values.append(int(chunk))
    return values, None


def _floor(values):
    return list(values) + [0] * (3 - len(values))


def _ceiling(values, index):
    """The exclusive bound after incrementing one component."""
    result = _floor(values)
    result[index] += 1
    return result[:index + 1]


def _compare(left, right):
    """-1, 0 or 1 with missing components treated as zero."""
    width = max(len(left), len(right))
    left = list(left) + [0] * (width - len(left))
    right = list(right) + [0] * (width - len(right))
    return (left > right) - (left < right)


def _bounds(operator, values, given, notation):
    """`(floor, ceiling)` half-open bounds for one clause, or None when unsupported."""
    if not values:
        return (None, None) if operator in (None,) + EQUALITY else None
    last = given - 1
    if operator in (None,) + EQUALITY:
        return _floor(values), _ceiling(values, last)
    if operator == '>=':
        return _floor(values), None
    if operator == '>':
        return _ceiling(values, last), None
    if operator == '<':
        return None, _floor(values)
    if operator == '<=':
        return None, _ceiling(values, last)
    if operator == '^':
        if notation == 'pep440':
            return None  # PEP 440 has no caret
        for index, value in enumerate(_floor(values)):
            if value:
                return _floor(values), _ceiling(values, index)
        # Every stated component is zero. A missing one is still flexible, so
        # `^0.0` reaches 0.1.0 while `^0.0.0` stops at 0.0.1.
        return _floor(values), _ceiling(values, last if given < 3 else 2)
    if operator == '~':
        if notation == 'pep440':
            return None  # PEP 440 spells this `~=`
        if notation == 'composer':
            return _floor(values), _ceiling(values, max(given - 2, 0))
        return _floor(values), _ceiling(values, 1 if given >= 2 else 0)
    if operator in ('~=', '~>'):
        expected = ('pep440', 'poetry') if operator == '~=' else ('rubygems',)
        if notation not in expected or given < 2:
            return None
        # PEP 440 `~=` and RubyGems `~>` both freeze everything but the last
        # stated component: `~=3.8.1` -> `>=3.8.1, <3.9.0`.
        return _floor(values), _ceiling(values, given - 2)
    return None


def _clause(operator, bound, measured, notation):
    table = OPERATORS.get(notation)
    if table is None:
        return UNDECIDABLE
    if operator is None and notation == 'poetry':
        # Poetry spells an exact requirement as a bare version; npm reads the
        # same text as a partial range, which is why the notation decides.
        operator = '='
    if operator is not None and operator not in table:
        return UNDECIDABLE
    values, wildcard = _partial(bound)
    if values is None:
        return UNDECIDABLE
    if notation == 'python-version':
        if operator is None:
            if not values or len(values) > 3 or wildcard is not None:
                return UNDECIDABLE
        else:
            # uv also accepts explicit specifiers in version files. Preserve
            # their Python semantics; only bare numeric requests are families.
            notation = 'pep440'
    if notation in ('pep440', 'poetry', 'composer'):
        # Python and Composer comparisons use zero-padded exact versions.
        # npm's partial comparator bounds must not leak here.
        if wildcard is not None and operator not in (None, '=', '==', '!='):
            return UNDECIDABLE
        if values and wildcard is None and operator in (None, '=', '==', '!=', '>', '>=', '<', '<='):
            comparison = _compare(measured, values)
            matched = {None: comparison == 0, '=': comparison == 0, '==': comparison == 0,
                       '!=': comparison != 0, '>': comparison > 0, '>=': comparison >= 0,
                       '<': comparison < 0, '<=': comparison <= 0}[operator]
            return SATISFIED if matched else VIOLATED
    if operator == '!=':
        if notation not in ('pep440', 'poetry') or not values:
            return UNDECIDABLE
        return VIOLATED if list(measured[:len(values)]) == values else SATISFIED
    bounds = _bounds(operator, values, len(values), notation)
    if bounds is None:
        return UNDECIDABLE
    floor, ceiling = bounds
    if floor is not None and _compare(measured, floor) < 0:
        return VIOLATED
    if ceiling is not None and _compare(measured, ceiling) >= 0:
        return VIOLATED
    return SATISFIED


def _tokenize(term):
    """Clauses of one conjunction, with a space after an operator re-joined.

    `>= 18.0.0 < 23` is two clauses, not four tokens, and the space that
    separates them has to survive being removed from after the operators.
    """
    tokens, pending = [], ''
    for chunk in re.split(r'[\s,]+', term.strip()):
        if not chunk:
            continue
        if OPERATOR_ONLY.fullmatch(chunk):
            if pending:
                return None  # two operators in a row
            pending = chunk
            continue
        tokens.append(pending + chunk)
        pending = ''
    return None if pending else tokens


def _term(term, measured, notation):
    tokens = _tokenize(term)
    if not tokens:
        return UNDECIDABLE
    results = []
    for token in tokens:
        found = TOKEN.fullmatch(token)
        if not found:
            return UNDECIDABLE
        results.append(_clause(found.group(1), found.group(2), measured, notation))
    if UNDECIDABLE in results:
        return UNDECIDABLE
    return SATISFIED if all(result == SATISFIED for result in results) else VIOLATED


def evaluate(constraint, version, notation):
    """`SATISFIED`, `VIOLATED` or `UNDECIDABLE` for one constraint.

    `notation` is required rather than defaulted: the same string means
    different things in different ecosystems, and a silent default would decide
    a Python constraint with npm rules.
    """
    if constraint is None:
        return SATISFIED
    text = str(constraint).strip()
    if text in ANY:
        return SATISFIED
    if notation not in OPERATORS:
        return UNDECIDABLE
    values, wildcard = _partial(version)
    if not values or wildcard is not None:
        # A measured version must be exact. An unmeasurable one is never a
        # refutation, only an unknown.
        return UNDECIDABLE
    terms = [term.strip() for term in text.split('||')]
    if not any(terms):
        return UNDECIDABLE
    results = [_term(term, values, notation) for term in terms if term]
    if SATISFIED in results:
        return SATISFIED
    if all(result == VIOLATED for result in results):
        return VIOLATED
    return UNDECIDABLE
