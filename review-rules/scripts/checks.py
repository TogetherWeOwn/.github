"""Deterministic, read-only advisory checks. Python standard library only.

Lexical checks are deliberately not advertised as YAML/TypeScript AST proofs.
All findings remain shadow until their IDs are promoted by a reviewed change.
Repository contents are data: never import, source or execute target code.
"""
from __future__ import annotations

import ast
import re
import shlex
from bisect import bisect_left
from pathlib import PurePosixPath

# Provenance is centralized so script findings have the same evidence contract as
# Semgrep metadata.examples. The runtime output never includes source snippets.
EXAMPLES = {
    'supply-action-pin': ['paperclip-ops-tooling#938 unpinned-checkout-org-reusable'],
    'supply-container-digest': ['paperclip-ops-tooling#737 sync-toolkit-unpinned-tarball'],
    'supply-download-integrity': ['paperclip#113 trivy-mutable-installer', '.github#16 binary-downloads-unverified'],
    'workflow-artifact-scrub': ['two-web-next#533 ledger-artifact-bypasses-token-scrub', 'nntunev2#11 artifact-index-html-password'],
    'workflow-pull-request-target': ['paperclip-ops-tooling#881 prt-ban-bypass'],
    'workflow-required-aggregator': ['nntunev2#3 ci-ok-cannot-go-red', 'two-web-next#737 skipped-ci-ok-opens-merge-window'],
    'security-suppression-change': ['nntunev2#3 gitleaksignore-bypass'],
    'security-suppression-source': ['nntunev2#3 gitleaks-ignore-path-pin-ineffective'],
    'security-committed-operator-switch': ['two-bot-next#947 two-eventsub-not-barred-from-toml'],
    'shell-provision-cleanup': ['paperclip-ops-tooling#766 no-builder-cleanup-trap'],
    'shell-trap-exec': ['kofra#647 run-grype-exec-skips-exit-trap'],
    'workflow-cancel-cleanup': ['paperclip-ops#14 cancel-strands-clone-no-always-cleanup'],
    'api-list-pagination': ['paperclip-ops-tooling#827 comments-checkruns-page1-only', 'paperclip-operator-toolkit#68 red-main-poll-single-page-failing-checks'],
    'shell-tool-failure-hidden': ['paperclip-ops-tooling#754 publish-duplicate-guard-noop', 'kofra#578 e2e-evidence-download-failure-masked'],
    'shell-scan-proves-work': ['paperclip-ops#6 gitleaks-bad-range-exits-0'],
    'shell-pr-first-match': ['paperclip-ops-tooling#754 mirror-lookup-no-head-pin'],
    'shell-cli-head-binding': ['paperclip-ops-tooling#752 automerge-match-head-commit-missing-sha'],
    'test-inventory-unreferenced': ['paperclip-ops-tooling#754 suite-not-in-ci', 'two-web-next#773 guard-test-not-wired-into-ci'],
    'ts-secret-denylist-parity': ['paperclip#49 secret-denylist-missing-xai-bedrock', 'paperclip#58 denylist-missing-managed-provider-key'],
    'ts-tar-exclude-flavor': ['paperclip#56 unanchored-target-node-modules-excludes', 'paperclip#56 bsdtar-dot-slash-exclude-unanchored'],
    'ts-kit-parent-guard': ['two-web-next#842 activity-log-load-skips-parent-guard', 'two-web-next#844 load-skips-parent-guard'],
    'ts-flash-consumer': ['two-web-next#827 home-kit-flash-never-consumed', 'two-web-next#849 join-flash-never-consumed'],
    'rust-dump-version-probe': ['two-bot-next#922 v5-inventory-grows-without-version-bump', 'two-bot-next#925 rollback-readiness-probe-rejects-v6'],
    'rust-sql-list-limit': ['nntunev2#28 unbounded-public-list'],
    'rust-duplicate-secret-codec': ['nntunev2#28 late-novel-handrolled-base64-duplicates-b64'],
    'migration-number-collision': ['nntunev2#73 migration-number-collision-main-0007', 'two-bot-next#924 migration-number-0431-collides-with-931'],
    'migration-number-order': ['nntunev2#73 migration-number-collision'],
    'store-parity-case': ['kofra#639 fake-d1-fence-divergence', 'kofra#630 update-recipient-order-diverges'],
    'merge-modify-delete': ['paperclip-ops-tooling#737 merge-drops-703-auth-files'],
}


def finding(rule: str, path: str, line: int, message: str, severity: str = 'WARNING') -> dict:
    return {'rule_id': rule, 'file': path, 'line': max(1, line),
            'severity': severity, 'message': message}


def line_at(text: str, offset: int) -> int:
    return text.count('\n', 0, offset) + 1


def line_counter(text: str):
    """Return line_of(offset) for non-decreasing offsets into `text`.

    Counts newlines incrementally between successive offsets, so a finditer
    loop pays one pass over the text instead of recounting from offset 0
    (line_at) for every match -- quadratic on match-dense hostile input.
    """
    counted = 0
    line = 1

    def line_of(offset: int) -> int:
        nonlocal counted, line
        line += text.count('\n', counted, offset)
        counted = offset
        return line

    return line_of


def has_after_on_line(text: str, first: str, second: str) -> bool:
    """True when `second` matches after `first` on some single line.

    Two-step replacement for text-wide `first[^\\n]*second` / `first.*second`
    regexes, which backtrack from every `first` on a long line when `second`
    never follows (quadratic in line length). Existence is equivalent: if
    some `first` is followed by `second` on a line, the leftmost `first`
    on that line is too.
    """
    first_re = re.compile(first)
    second_re = re.compile(second)
    for line in text.split('\n'):
        hit = first_re.search(line)
        if hit and second_re.search(line, hit.end()):
            return True
    return False


def select_filters_identity(line: str) -> bool:
    """True for select(.head.sha == ...) / select(.number == ...) on `line`.

    Linear replacement for `select\\([^)]*\\.(?:head\\.sha|number)\\s*==`:
    that regex rescans to the end of the line from every `select(` when no
    `)` follows. Here each parenthesised segment is searched at most once:
    a `select(` inside an already-searched segment has a subset segment.
    """
    identity = re.compile(r'\.(?:head\.sha|number)\s*==')
    searched_until = -1
    pos = 0
    while (start := line.find('select(', pos)) >= 0:
        inner = start + len('select(')
        if inner > searched_until:
            end = line.find(')', inner)
            if end < 0:
                end = len(line)
            if identity.search(line, inner, end):
                return True
            searched_until = end
        pos = inner
    return False


def workflow_steps(text: str) -> list[tuple[int, str]]:
    """Split block-style steps; flow-style workflows remain a reported limitation.

    This does not claim to parse YAML. Supply pins are scanned independently,
    including flow mappings, quoted keys and folded uses values.
    """
    lines = text.splitlines(keepends=True)
    starts = [(i, len(m[1])) for i, line in enumerate(lines)
              if (m := re.match(r'^(\s*)-\s+(?:name|id|uses|run|if|shell|working-directory):', line))]
    result = []
    for i, indent in starts:
        end = i + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and not line.lstrip().startswith('#'):
                depth = len(line) - len(line.lstrip())
                if depth <= indent:
                    break
            end += 1
        result.append((i + 1, ''.join(lines[i:end])))
    return result


def workflow_runs(text: str) -> list[tuple[int, str]]:
    """Extract block-style run bodies; preserve original line numbers for findings."""
    lines = text.splitlines(keepends=True)
    result = []
    for i, line in enumerate(lines):
        match = re.match(r'^(\s*)(?:-\s+)?run:\s*(.*)', line)
        if not match:
            continue
        # For '- run:' the key itself is two spaces deeper than the dash.
        indent = len(match[1]) + (2 if line.lstrip().startswith('- ') else 0)
        value = match[2].strip()
        if value and value[0] not in '|>':
            result.append((i + 1, value.strip("\"'")))
            continue
        body = []
        j = i + 1
        while j < len(lines):
            if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= indent:
                break
            body.append(lines[j])
            j += 1
        nonempty = [len(s) - len(s.lstrip()) for s in body if s.strip()]
        trim = min(nonempty, default=0)
        result.append((i + 2, ''.join(s[trim:] if s.strip() else '\n' for s in body)))
    return result


def action_references(text: str):
    """Read common block/flow uses scalars without interpreting expressions."""
    # [ \t]*, not \s*, after ^: \s crosses newlines, so on a run of blank
    # lines every line start rescans the rest of the run (quadratic). The
    # uses line's own start anchors the same matches.
    pattern = r'''(?m)(?:^[ \t]*(?:-[ \t]*)?|[,{]\s*)["']?uses["']?\s*:\s*(?:[>|][-+]?\s*\n\s*)?["']?([^\s"',}#]+)'''
    line_of = line_counter(text)
    for match in re.finditer(pattern, text):
        yield line_of(match.start()), match[1]


def pinned_action(value: str) -> bool:
    if value.startswith('./'):
        return True
    if value.startswith('docker://'):
        return bool(re.fullmatch(r'docker://[^@]+@sha256:[a-f0-9]{64}', value))
    return bool(re.fullmatch(r'[^@\s]+@[a-fA-F0-9]{40}', value))


def supply_checks(path: str, text: str) -> list[dict]:
    out = []
    suffix = PurePosixPath(path).suffix
    if suffix in {'.yml', '.yaml'}:
        for line, value in action_references(text):
            if not pinned_action(value):
                out.append(finding('supply-action-pin', path, line,
                    'PYCI-02a/SEC-AUTH-04: external uses must end in exactly 40 hex characters; docker actions need a sha256 digest.', 'ERROR'))
        line_of = line_counter(text)
        for match in re.finditer(r'''(?m)^[ \t]*(?:-[ \t]*)?(?:image|container):[ \t]*["']?([^\s"'#]+)''', text):
            value = match[1]
            if value in {'|', '>', '{'} or value.startswith('${{'):
                continue  # expressions need caller-level provenance, not a guessed pin.
            if not re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', value):
                out.append(finding('supply-container-digest', path, line_of(match.start()),
                    'SEC-AUTH-04: container image tag is mutable; pin its sha256 digest.', 'ERROR'))
    if PurePosixPath(path).name.lower().startswith('dockerfile') or suffix == '.dockerfile':
        stages = set()
        line_of = line_counter(text)
        # [ \t]* after ^, as in action_references: ^\s* is quadratic on
        # blank-line runs when no FROM follows them.
        for match in re.finditer(r'(?im)^[ \t]*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?', text):
            image, stage = match[1], match[2]
            if image.lower() != 'scratch' and image.lower() not in stages and not re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', image):
                out.append(finding('supply-container-digest', path, line_of(match.start()),
                    'SEC-AUTH-04: external FROM needs a sha256 digest (ARG-expanded images require explicit provenance).', 'ERROR'))
            if stage:
                stages.add(stage.lower())
    return out


# An exec statement longer than this is treated as an unparsed command
# (conservative: it still flags) instead of being tokenized, so one hostile
# or oversized line cannot make the pure-Python scans unbounded.
MAX_EXEC_TEXT_CHARS = 8192


def collapse_substitutions(line: str) -> str:
    """Replace each balanced `>(...)`, `<(...)` and `$(...)` with one word.

    Quote- and nesting-aware, so a substitution whose body contains quoted
    parentheses or a nested `$(...)` still collapses to a single token
    instead of leaking its interior words to the tokenizer. An unbalanced
    opening is left as literal text.
    """
    out = []
    index = 0
    quote = ''
    # Total inner-scan work is budgeted. A balanced substitution is scanned
    # once and then jumped past, so all successful scans together cost at
    # most one pass over the line; only repeated *unbalanced* openers --
    # each of which rescans to end-of-line -- can spend the budget. Without
    # it, a line of unclosed `$(` openers is quadratic in line length.
    # Spending the budget leaves the rest of the line literal, whose words
    # then tokenize as a command: the conservative direction.
    scan_budget = 4 * len(line) + 1024
    while index < len(line):
        char = line[index]
        if quote == "'":
            out.append(char)
            quote = '' if char == "'" else quote
            index += 1
            continue
        if char == '\\' and index + 1 < len(line):
            out.append(line[index:index + 2])
            index += 2
            continue
        if char == '"':
            quote = '' if quote == '"' else (quote or '"')
            out.append(char)
            index += 1
            continue
        if char == "'" and not quote:
            quote = "'"
            out.append(char)
            index += 1
            continue
        if line.startswith(('>(', '<(', '$('), index) and (not quote or char == '$'):
            depth = 0
            inner_quote = ''
            end = index + 1
            while end < len(line):
                if scan_budget <= 0:
                    return ''.join(out) + line[index:]
                scan_budget -= 1
                inner = line[end]
                if inner_quote == "'":
                    inner_quote = '' if inner == "'" else inner_quote
                elif inner == '\\' and inner_quote != "'":
                    end += 1
                elif inner_quote == '"':
                    inner_quote = '' if inner == '"' else inner_quote
                elif inner in {'"', "'"}:
                    inner_quote = inner
                elif inner == '(':
                    depth += 1
                elif inner == ')':
                    depth -= 1
                    if not depth:
                        break
                end += 1
            if end >= len(line):
                out.append(char)
                index += 1
                continue
            out.append('__substitution__')
            index = end + 1
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def exec_first_command(line: str) -> str:
    """`exec` arguments up to the first unquoted shell command separator.

    Substitutions become a placeholder word first, so
    `exec > >(tee "$log") 2>&1` tokenizes as redirections only, while a
    command word after the substitution is still visible to the caller.
    """
    line = collapse_substitutions(line)
    quote = ''
    escaped = False
    prev = ''
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            prev = ''
            continue
        if char == '\\' and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ''
        elif char in {'"', "'"}:
            quote = char
        # `>|` (and `2>|`, `>>|`) is the clobber redirection, not a pipe:
        # only a `|` whose previous unescaped char is not `>` separates.
        elif char == ';' or (char == '|' and prev != '>') or line.startswith('&&', index):
            return line[:index]
        prev = char
    return line


def shell_checks(path: str, text: str) -> list[dict]:
    out = []
    # Strip comment-only lines, not quoted shell data. These checks are hints;
    # no regex is presented as a control-flow proof or a checksum verifier.
    code = '\n'.join('' if l.lstrip().startswith('#') else l for l in text.splitlines())
    # Positions are indexed once and bisected per match below. Slicing the
    # remainder (or prefix) of the file, rescanning it, or recounting its
    # newlines for every match makes each of these loops quadratic in file
    # size on untrusted input, outside every scanner timeout.
    newlines = [m.start() for m in re.finditer('\n', code)]

    def line_of(offset: int) -> int:
        return bisect_left(newlines, offset) + 1

    def first_at_or_after(starts: list[int], offset: int):
        index = bisect_left(starts, offset)
        return starts[index] if index < len(starts) else None

    def lines_matching(first: str, second: str):
        """Yield (start, line) for lines where `second` matches after `first`.

        A per-line two-step search replaces `^.*first[^\\n]*second` regexes:
        those backtrack from every occurrence of `first` on a long line when
        `second` never follows, which is quadratic in line length. Existence
        per line is equivalent: if some `first` is followed by `second`, the
        leftmost `first` is too. At most one yield per line, as with ^.* .
        One exception: the old regexes let a `\\s` in `second` cross a
        newline, which is real shell when the line ends with `||` (the
        command continues), so that case is retried on the joined lines.
        """
        first_re = re.compile(first)
        second_re = re.compile(second)
        lines = code.split('\n')
        offset = 0
        for index, line in enumerate(lines):
            start = offset
            offset += len(line) + 1
            hit = first_re.search(line)
            if not hit:
                continue
            if second_re.search(line, hit.end()):
                yield start, line
                continue
            # A trailing `||` continues the command on the next line (a
            # scanner masked as `cmd ||` + newline + `true` must still
            # flag). Joining at most one line per line keeps it linear.
            # The joined match must span the newline, as the old regex's
            # `\s` did: a `second` found wholly on the next line belongs
            # to the continued command, not to this line's `first`.
            if index + 1 < len(lines) and line.rstrip().endswith('||'):
                joined = line + '\n' + lines[index + 1]
                spanning = second_re.search(joined, hit.end())
                if spanning and '\n' in spanning[0]:
                    yield start, joined

    checked_starts = [m.start() for m in re.finditer(r'\bsha256sum\s+(?:--check|-c)\b', code)]
    execute_starts = [m.start() for m in re.finditer(
        r'(?:\|\s*(?:sh|bash|tar)\b|\b(?:tar\s|sudo\s+(?:mv|install)|chmod\s+\+x|\./install))', code)]
    for start, _line in lines_matching(r'\b(?:curl|wget)\b',
                                      r'(?:/main/|/master/|releases/download/|\$\{?TAG\}?|\.tar\.gz|\.tgz)'):
        checked_at = first_at_or_after(checked_starts, start)
        execute_at = first_at_or_after(execute_starts, start)
        if checked_at is None or (execute_at is not None and execute_at < checked_at):
            out.append(finding('supply-download-integrity', path, line_of(start),
                'PYCI-02b/SEC-AUTH-04: archive/installer needs immutable provenance and checksum verification before extraction/execution in this step.', 'ERROR'))
    # [ \t]* after ^ in the trap/set searches below: ^\s* crosses newlines,
    # so every blank line start rescans the rest of a blank run (quadratic).
    # Only .end() of these matches is used, and it is unchanged: the first
    # matching line anchors at its own start either way. `trap(?=\s)`, not
    # `trap\s+`: \s+ and [^\n]* overlap on whitespace, so each backoff of
    # \s+ rescans the rest of a long whitespace run (quadratic again).
    trap = re.search(r'(?m)^[ \t]*trap(?=\s)[^\n]*\bEXIT\b', code)
    provision = re.search(r'\b(?:mktemp|lxc\s+(?:init|launch)|pscale\s+branch\s+create)\b', code)
    if provision and not trap and not re.search(r'#\s*sourced-cleanup:\s*\S+', text):
        out.append(finding('shell-provision-cleanup', path, line_of(provision.start()),
            'PYCI-11: provisioned resource has no same-file EXIT cleanup; verify caller-owned cleanup explicitly.'))
    if trap:
        after_trap = code[trap.end():]
        # Located once: searching (and slicing) the prefix before every exec
        # line would make the loop below quadratic in file size.
        trap_off = re.search(r'(?m)^[ \t]*trap\s+-\s+EXIT\b', after_trap)
        trap_off_end = trap_off.end() if trap_off else None
        for execute in re.finditer(r'(?m)^[ \t]*exec\b[^\n]*', after_trap):
            # Join backslash-newline continuations so a command (or its
            # arguments) on the next line is tokenized with the exec line;
            # the finding still reports the exec line via execute.start().
            # Fetch only the next physical line with find() and accumulate
            # parts: splitting the remainder of the file, or concatenating
            # onto the growing text, per exec line is quadratic. A physical
            # line ending in an odd number of backslashes continues, exactly
            # as rechecking the joined text did.
            parts = [execute[0]]
            segment = execute[0]
            total = len(segment)
            pos = execute.end()
            too_long = total > MAX_EXEC_TEXT_CHARS
            while (not too_long and pos < len(after_trap)
                   and (len(segment) - len(segment.rstrip('\\'))) % 2):
                parts[-1] = parts[-1][:-1]
                total -= 1
                start = pos + 1  # the exec match stops at the newline
                end = after_trap.find('\n', start)
                if end < 0:
                    end = len(after_trap)
                segment = after_trap[start:end]
                parts.append(segment)
                total += len(segment)
                too_long = total > MAX_EXEC_TEXT_CHARS
                pos = end
            if too_long:
                words = ['unparsed-command']
            else:
                try:
                    words = shlex.split(exec_first_command(
                        re.sub(r'^[ \t]*exec\b', '', ''.join(parts), count=1)), comments=True)
                except ValueError:
                    # A plain word: the redirection/option branches below must
                    # not consume it, so an unparseable exec still counts as a
                    # command (the shell is replaced either way).
                    words = ['unparsed-command']
            has_command = False
            options_done = False
            while words:
                redirection = re.match(
                    r'^(?:\d+|\{[A-Za-z_]\w*\})?(?:&>>|&>|>>|>\||>&|<&|<<|<>|>|<)(.*)$', words[0])
                if redirection:
                    words.pop(0)
                    if not redirection[1] and words:
                        words.pop(0)
                    continue
                if options_done:
                    has_command = words[0] not in {'&&', '||', ';', '|', '&'}
                    break
                if words[0] == '--':
                    words.pop(0)
                    options_done = True
                elif words[0] in {'-c', '-l'}:
                    words.pop(0)
                elif words[0] == '-a':
                    words.pop(0)
                    if words:
                        words.pop(0)
                elif words[0].startswith('-'):
                    break
                else:
                    has_command = words[0] not in {'&&', '||', ';', '|', '&'}
                    break
            if not has_command:
                continue
            if trap_off_end is None or trap_off_end > execute.start():
                out.append(finding('shell-trap-exec', path, line_of(trap.end() + execute.start()),
                    'PYCI-11: top-level exec replaces the shell and skips EXIT trap; clean explicitly or avoid exec.', 'ERROR'))
                break
    errexit = re.search(r'(?m)^[ \t]*set\s+-[a-z]*e', code)
    errexit_end = errexit.end() if errexit else None
    for start, _line in lines_matching(r'\b(?:gh|git|gitleaks|curl)\b', r'2>\s*/dev/null'):
        # Only the next three lines are evidence; slice to the third newline
        # instead of splitting the remainder of the file for every match.
        nearby_end = start
        for _ in range(3):
            newline = code.find('\n', nearby_end)
            if newline < 0:
                nearby_end = len(code)
                break
            nearby_end = newline + 1
        handled = re.search(r'\brc=\$\?|\|\|\s*(?:exit\s+[1-9]|return\s+[1-9])', code[start:nearby_end])
        if not handled and (errexit_end is None or errexit_end > start):
            out.append(finding('shell-tool-failure-hidden', path, line_of(start),
                'PYCI-07a: forge/tool stderr is discarded without a visible status check; tool failure is not a clean result.'))
    for start, _line in lines_matching(r'\b(?:gh\s+run\s+download|gitleaks|semgrep|trivy)\b',
                                      r'\|\|\s*(?:true|exit\s+0)\b'):
        out.append(finding('shell-tool-failure-hidden', path, line_of(start),
            'PYCI-07a: scan/download failure is converted to success; report incomplete work explicitly.', 'ERROR'))
    scan_counted = re.search(r'(?:commits scanned|scanned[_-]?(?:files|commits)|scan[_-]?count)', code)
    for match in re.finditer(r'\bgitleaks\s+(?:git|detect)\b', code):
        if not scan_counted:
            out.append(finding('shell-scan-proves-work', path, line_of(match.start()),
                'PYCI-07b: scanner success alone does not prove a nonempty range; assert a positive scanned count.'))
    for start, line in lines_matching(r'\bgh\s+(?:api|pr\s+list)\b', r'\.\[0\]'):
        if not select_filters_identity(line):
            out.append(finding('shell-pr-first-match', path, line_of(start),
                'PYCI-05a: first PR result is not an identity match; select full PR/head identity before indexing.'))
    for match in re.finditer(r'--match-head-commit\b([^\n]*)', code):
        tail = match[1].strip()
        if not tail or tail.startswith(('--', '&&', '||', ';')):
            out.append(finding('shell-cli-head-binding', path, line_of(match.start()),
                'PYCI-13: --match-head-commit is missing its evaluated SHA argument.', 'ERROR'))
    return out


def pagination_checks(path: str, text: str) -> list[dict]:
    """Function-scoped for Python, whole-step/file advisory elsewhere."""
    regions = [(1, text)]
    if path.endswith('.py'):
        try:
            tree = ast.parse(text)
            lines = text.splitlines()
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            regions = [(n.lineno, '\n'.join(lines[n.lineno - 1:n.end_lineno])) for n in functions] or regions
        except (SyntaxError, RecursionError, MemoryError, ValueError):
            # MemoryError: the parser reports stack overflow on very long
            # lines that way; ValueError: NUL bytes in the source. Either
            # way untrusted input must fall back to whole-text scanning,
            # not abort the scan.
            pass
    out = []
    for first, body in regions:
        line_of = line_counter(body)
        # Region-invariant: searching the whole body per match is quadratic
        # on match-dense input.
        paginated = re.search(r'(--paginate|hasNextPage|rel=["\']next|next_link|capped-intent:\s*\S)', body)
        for match in re.finditer(r'(?:--limit\s+\d+|per_page\s*[=:]\s*\d+|pulls\[0\])', body):
            if paginated:
                continue
            out.append(finding('api-list-pagination', path, first + line_of(match.start()) - 1,
                'PYCI-06: capped listing has no visible pagination in this function/step; add a page-2 fixture or document newest-N intent.'))
    return out


def workflow_checks(path: str, text: str) -> list[dict]:
    out = []
    # Trigger recognition includes quoted on, flow list and zero-indent list.
    # Restrict to workflow files; prose mentions and run-body strings are not triggers.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r'''\s*["']?on["']?\s*:''', line):
            indent = len(line) - len(line.lstrip())
            block = [line]
            for following in lines[i + 1:]:
                depth = len(following) - len(following.lstrip())
                if following.strip() and depth <= indent and not following.lstrip().startswith(('-', '#')):
                    break
                block.append(following)
            if re.search(r'\bpull_request_target\b', '\n'.join(block)):
                out.append(finding('workflow-pull-request-target', path, i + 1,
                    'SEC-AUTH-07: privileged pull_request_target trigger requires a separate trust review; suppression comments do not make it safe.', 'ERROR'))
    steps = workflow_steps(text)
    for first, block in steps:
        if 'actions/upload-artifact@' not in block:
            continue
        sensitive = re.search(r'(playwright-report|test-results|staging-journeys|stdout|stderr|(?:^|/)logs?(?:/|\s|$))', block)
        gated = re.search(r"(?:steps|needs)\.[\w-]+\.outputs\.swept\s*==\s*'true'", block)
        if sensitive and not gated:
            out.append(finding('workflow-artifact-scrub', path, first,
                'PYCI-04b/SEC-AUTH-03A: sensitive report upload has no swept==true gate; green and failed runs both need scrubbing (including embedded index.html).', 'ERROR'))
    if re.search(r'\b(?:lxc\s+(?:init|launch)|pscale\s+branch\s+create)\b', text):
        cleanups = [b for _, b in steps if has_after_on_line(b, r'if:', r'(?:always\(\)|cancelled\(\))')
                    and re.search(r'\b(?:delete|destroy)\b', b)]
        if not cleanups:
            out.append(finding('workflow-cancel-cleanup', path, 1,
                'PYCI-11: external resource has no visible owning-step always/cancelled cleanup; a grandchild trap is insufficient.'))
    if re.search(r'toJSON\(needs\)', text) and has_after_on_line(text, r'grep', r'''["']result["']:["']failure'''):
        out.append(finding('workflow-required-aggregator', path, 1,
            'PYCI-12: compact-text grep does not parse pretty toJSON(needs); simulate a failed dependency with a JSON parser.', 'ERROR'))
    if 'gitleaks' in text:
        # File-invariant; located once instead of per run block below.
        removes = re.search(r'rm\s+-f\s+(?:--\s+)?\.?/?\.gitleaksignore', text)
        base = has_after_on_line(text, r'git\s+show\s+', r':\.gitleaksignore')
        for first, script in workflow_runs(text):
            if not re.search(r'\bgitleaks\s+(?:git|detect|dir)\b', script):
                continue
            if not removes or not base:
                out.append(finding('security-suppression-source', path, first,
                    'SEC-AUTH-01: verify working-tree ignore removal and a base-blob-only suppression path; the ignore-path flag alone is not proof.'))
    return out


def check_text(path: str, text: str, packs: set[str]) -> list[dict]:
    out = []
    suffix = PurePosixPath(path).suffix
    workflow = suffix in {'.yml', '.yaml'} and ('.github/workflows/' in path or '/sdlc/' in path)
    shell = suffix in {'.sh', '.bash'} or text.startswith(('#!/bin/bash', '#!/usr/bin/env bash', '#!/bin/sh'))
    supply = bool(packs & {'security', 'python-shell-ci'})
    if supply:
        out.extend(supply_checks(path, text))
        if shell:
            out.extend(shell_checks(path, text))
        if workflow:
            out.extend(workflow_checks(path, text))
            for first, script in workflow_runs(text):
                for item in shell_checks(path, script) + pagination_checks(path, script):
                    item['line'] += first - 1
                    out.append(item)
        elif shell or suffix == '.py':
            out.extend(pagination_checks(path, text))
    if 'security' in packs and PurePosixPath(path).name in {'.gitleaksignore', 'gitleaks.toml', '.gitleaks.toml', '.secretlintrc'}:
        out.append(finding('security-suppression-change', path, 1,
            'SEC-AUTH-01: suppression changed; retain base-ref policy and run a hostile-ignore canary before approval.'))
    if 'security' in packs and suffix == '.toml':
        line_of = line_counter(text)
        # ^[ \t]*, not ^\s*: same blank-run quadratic as action_references.
        for match in re.finditer(r'(?m)^[ \t]*TWO_EVENTSUB\s*=\s*["\']?1', text):
            out.append(finding('security-committed-operator-switch', path, line_of(match.start()),
                'SEC-AUTH-07: operator-only TWO_EVENTSUB switch is committed in TOML; keep it runtime-controlled.', 'ERROR'))
    if 'ts' in packs and suffix in {'.ts', '.js', '.mjs'}:
        line_of = line_counter(text)
        # File-invariant; scanning the file per match is quadratic.
        flavor_aware = 'tarExcludeFor(' in text or 'find-prune' in text
        for match in re.finditer(r'''["']--exclude["']\s*,\s*["']([^"']+)["']''', text):
            if not flavor_aware:
                out.append(finding('ts-tar-exclude-flavor', path, line_of(match.start()),
                    'TS-003a: tar exclusion is not flavor-aware; ./ anchors GNU but not bsdtar. Prove nested tracked files survive both.'))
    if 'rust' in packs and suffix == '.rs':
        if re.search(r'fn\s+(?:encode|decode)_(?:b64|base64|b64url)\b', text) and PurePosixPath(path).stem not in {'b64', 'base64', 'codec'}:
            out.append(finding('rust-duplicate-secret-codec', path, 1,
                'RUST-14b: new local base64 codec may diverge from the shared strict codec; test padding/alphabet rejection.'))
        if path.endswith('workers/api/src/library.rs'):
            line_of = line_counter(text)
            for match in re.finditer(r'(?s)fn\s+\w+_statement\b.{0,800}', text):
                body = match[0]
                if re.search(r'\bSELECT\b', body) and not re.search(r'\bLIMIT\b|unpaged-by-design:', body):
                    out.append(finding('rust-sql-list-limit', path, line_of(match.start()),
                        'RUST-04: statement builder has no visible LIMIT; execution/EXPLAIN and paged fixtures decide.'))
    return out


def repo_checks(repo, changed: list[str], packs: set[str], migration_ref: str | None) -> list[dict]:
    """Cross-file checks over Git blobs, never files supplied by the checkout."""
    out = []
    files = repo.files()
    texts = {}

    def read(path):
        if path not in texts:
            texts[path] = repo.text(path)
        return texts[path]

    if packs & {'ts', 'python-shell-ci'}:
        # A mention is only a candidate referrer, not proof of test collection.
        runners = [p for p in files if p.startswith('.github/workflows/') or
                   (p.endswith(('.sh', '.json', '.mjs', '.js', '.ts', 'Makefile')) and
                    any(x in p.lower() for x in ('ci/', 'verify', 'package.json', 'vitest', 'makefile')))]
        references = '\n'.join(read(p) for p in runners if p not in changed or not is_test(p))
        for path in changed:
            if not is_test(path) or any(p in {'fixtures', 'fixture', 'testdata', 'helpers'} for p in PurePosixPath(path).parts) or '.manual.' in path:
                continue
            stem = PurePosixPath(path).stem
            module = path.removesuffix('.py').replace('/', '.')
            explicit = any(s in references for s in (path, PurePosixPath(path).name, stem, module))
            discovery = ('unittest discover' in references and path.endswith('.py')) or ('pytest' in references and path.endswith('.py'))
            if not explicit and not discovery:
                out.append(finding('test-inventory-unreferenced', path, 1,
                    'PYCI-01/TS-005: no lexical CI referrer found; attach actual runner collection output (glob discovery may be valid).'))
    if 'ts' in packs:
        denyfiles = [p for p in files if p.endswith('secret-env-redaction.ts')]
        if denyfiles:
            entries = set(re.findall(r'''["']([A-Z][A-Z0-9_]+)["']''', '\n'.join(read(p) for p in denyfiles)))
            for path in changed:
                if not path.endswith(('.ts', '.js', '.mjs', '.svelte')) or is_test(path):
                    continue
                # line_counter, not line_at: recounting newlines from the
                # start for every match is quadratic on match-dense input.
                line_of = line_counter(read(path))
                for match in re.finditer(r'''(?:process\.env\.|import\.meta\.env\.)([A-Z][A-Z0-9_]*)|(?:process\.env\[|Deno\.env\.get\()["']([A-Z][A-Z0-9_]*)["']''', read(path)):
                    name = match[1] or match[2]
                    if re.search(r'(?:KEY|TOKEN|SECRET|PASSWORD)', name) and name not in entries:
                        out.append(finding('ts-secret-denylist-parity', path, line_of(match.start()),
                            'TS-001/SEC-AUTH-08: credential env read is absent from the literal redaction inventory; verify dynamic injection parity.', 'ERROR'))
        for path in changed:
            if not path.endswith('+page.server.ts'):
                continue
            page = PurePosixPath(path)
            layouts = [str(parent / '+layout.server.ts') for parent in page.parents if str(parent) != '.']
            guarded = any(layout in files and re.search(r'\b(?:redirect|error|require\w*|guard\w*)\s*\(', read(layout)) for layout in layouts)
            body = read(path)
            db = re.search(r'\b(?:dbFor|query|list[A-Z]\w*)\s*\(', body)
            parent = re.search(r'await\s+parent\s*\(', body)
            if guarded and db and (not parent or parent.start() > db.start()):
                out.append(finding('ts-kit-parent-guard', path, line_at(body, db.start()),
                    'TS-010: guarded route has a DB call before visible await parent(); verify real load ordering with a deferred-parent test.', 'ERROR'))
            consumer = re.search(r'(?:delete\w*Cookie|consume\w*Flash|apply\w*FlashConsumption)\s*\(', body)
            line_of = line_counter(body)
            for match in re.finditer(r'\b(?:read|get)(\w*Flash|JoinResult)Cookie\s*\(', body):
                if not consumer:
                    out.append(finding('ts-flash-consumer', path, line_of(match.start()),
                        'TS-007b: one-shot flash read has no visible consumer; prove consume-once on this served route.'))
    if 'rust' in packs:
        dump = 'crates/core/src/backup/dump_file.rs'
        if dump in changed:
            old, new = repo.text(dump, repo.base, missing=True), read(dump)
            def inventory(s):
                # Linear replacement for a text-wide
                # `(?:DUMP_TABLES|\w+_ADDED_TABLES)[^=]*=\s*&?\[(.*?)\]`
                # regex: `\w+` is tried at every offset of a long identifier
                # and `[^=]*` rescans from every candidate. Candidates are
                # located by literal substring search instead, and the `=`
                # / `]` positions are indexed once and bisected per
                # candidate. Only a candidate's end offset matters below,
                # so `_ADDED_TABLES` preceded by a word character stands in
                # for `\w+_ADDED_TABLES`.
                candidates = []
                for needle in ('DUMP_TABLES', '_ADDED_TABLES'):
                    at = s.find(needle)
                    while at >= 0:
                        if needle == 'DUMP_TABLES' or (at > 0 and (s[at - 1].isalnum() or s[at - 1] == '_')):
                            candidates.append((at, at + len(needle)))
                        at = s.find(needle, at + 1)
                candidates.sort()
                equals = [m.start() for m in re.finditer('=', s)]
                closes = [m.start() for m in re.finditer(r'\]', s)]
                names = set()
                seen = set()
                consumed_until = -1
                for start, end in candidates:
                    # The regex this replaces resumed after each match's
                    # `]`, so candidates inside an extracted segment do
                    # not start a new match.
                    if start < consumed_until:
                        continue
                    at = bisect_left(equals, end)
                    if at >= len(equals):
                        break
                    if equals[at] in seen:
                        continue
                    seen.add(equals[at])
                    pos = equals[at] + 1
                    while pos < len(s) and s[pos].isspace():
                        pos += 1
                    if pos < len(s) and s[pos] == '&':
                        pos += 1
                    if pos >= len(s) or s[pos] != '[':
                        continue
                    close = bisect_left(closes, pos)
                    if close >= len(closes):
                        break
                    consumed_until = closes[close] + 1
                    names.update(re.findall(r'''["']([a-z][a-z0-9_]*)["']''', s[pos + 1:closes[close]]))
                return names
            version = lambda s: re.search(r'DUMP_VERSION\s*:\s*\w+\s*=\s*(\d+)', s)
            before, after = version(old), version(new)
            if inventory(new) - inventory(old):
                bumped = before and after and int(after[1]) > int(before[1])
                probe_changed = any('rollback_readiness_probe' in p for p in changed)
                if not bumped or not probe_changed:
                    out.append(finding('rust-dump-version-probe', dump, 1,
                        'RUST-03: dump inventory grew without both a numeric version bump and probe update; old-fixture restore and export/import round-trip still required.', 'ERROR'))
    if packs & {'rust', 'php'}:
        # Numeric SQL migrations have separate namespaces per directory. Laravel
        # timestamp migrations are deliberately not conflated with NNNN files.
        def migrations(paths):
            result = {}
            for path in paths:
                p = PurePosixPath(path)
                match = re.fullmatch(r'(\d{4})_[^/]+\.sql', p.name)
                if match and any('migration' in part for part in p.parts):
                    result[path] = (str(p.parent), int(match[1]))
            return result
        current = migrations(files)
        baseline = migrations(repo.files(migration_ref)) if migration_ref else {}
        for path in changed:
            if path not in current:
                continue
            namespace, number = current[path]
            conflicts = [p for p, pair in {**baseline, **current}.items() if pair == (namespace, number) and p != path]
            if conflicts:
                out.append(finding('migration-number-collision', path, 1,
                    'R2a: migration number collides in this directory against the checked-out/default-branch inventory.', 'ERROR'))
            elif path not in baseline and baseline and number <= max((n for ns, n in baseline.values() if ns == namespace), default=-1):
                out.append(finding('migration-number-order', path, 1,
                    'R2a: new migration does not follow the default-branch maximum in its directory; renumber and update the manifest.', 'ERROR'))
    if 'php' in packs:
        parity_changed = any(is_test(p) and 'parity' in (p + read(p)).lower() for p in changed)
        for path in changed:
            if 'store' in PurePosixPath(path).stem.lower() and not is_test(path) and path.endswith(('.ts', '.php')) and not parity_changed:
                out.append(finding('store-parity-case', path, 1,
                    'R7: store changed with no visible parity-case update; run identical return/order/error vectors against fake and production backends.'))
    return out


def is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith('test_') or any(x in name for x in ('.test.', '.spec.', 'selftest', 'Test.php'))


def merge_audit(repo) -> list[dict]:
    """Find two-parent modify/delete resolutions that discarded modified blobs.

    This is Git evidence, not a ban on normal deletions. Octopus merges are
    reported as incomplete by the runner instead of silently declared safe.
    """
    out = []
    for merge in repo.merges():
        parents = repo.parents(merge)
        if len(parents) != 2:
            raise ValueError('octopus merge requires a separate merge-integrity audit')
        left, right = parents
        ancestor = repo.merge_base(left, right)
        a, b = repo.statuses(ancestor, left), repo.statuses(ancestor, right)
        survivors = set(repo.files(merge))
        for path in set(a) & set(b):
            if {a[path], b[path]} == {'D', 'M'} and path not in survivors:
                out.append(finding('merge-modify-delete', path, 1,
                    'TS-011: merge resolved a proven modify/delete conflict by deleting the modified file; recover or explicitly justify the loss.', 'ERROR'))
    return out
