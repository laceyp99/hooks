"""Read shell command lines as words, so the secret guard can tell data from file access.

The secret guard denies a protected name wherever it appears in a command. A few argument
positions only ever carry text that the command never opens: a commit or PR message, a search
pattern, an exclusion, and a line appended to an ignore file. ``mask_data_arguments`` blanks
those positions before the guard looks at the command, so ``git commit -m "fix .env loading"``
passes while ``git commit -m "$(cat .env)"`` and ``rg TOKEN .env`` are still checked.

``find_opaque_command`` reports commands whose real work is hidden from text inspection:
``Invoke-Expression``/``iex`` when it is the command being run, and ``Start-Process`` when the
program it launches is a variable or a shell.

This is a tokenizer, not a shell. Anything it cannot read as a plain literal stays in the text
and is checked as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ``"$(cat <<'EOF' ... EOF)"`` is how Claude Code passes a multi-line commit or PR message. With a
# quoted delimiter the body is literal text, so it counts as a literal argument.
QUOTED_HEREDOC_SUBSTITUTION_RE = re.compile(
    r"\$\(\s*cat\s+<<-?\s*(['\"])(\w+)\1[ \t]*\r?\n.*?\r?\n[ \t]*\2[ \t]*\r?\n?\s*\)",
    re.DOTALL,
)

SEGMENT_BREAKS = ";&|\r\n"
WORD_BREAKS = frozenset(" \t" + SEGMENT_BREAKS + "()<>")
ASSIGNMENT_PREFIX_RE = re.compile(r"^\w+=")
IGNORE_FILE_RE = re.compile(r"^\.[a-z0-9_-]*ignore$")

# Message options of Git subcommands that record a message rather than read a file.
GIT_MESSAGE_SUBCOMMANDS = frozenset({"commit", "merge", "notes", "stash", "tag"})
GIT_MESSAGE_FLAGS = frozenset({"-m", "--message"})
# History searches whose value is a pattern.
GIT_SEARCH_SUBCOMMANDS = frozenset({"log", "rev-list", "shortlog", "show", "whatchanged"})
GIT_SEARCH_FLAGS = frozenset({"--grep", "-S", "-G"})
# Git global options that consume the next word, so the subcommand search can step past them.
GIT_GLOBAL_VALUE_FLAGS = frozenset(
    {"-C", "-c", "--config-env", "--exec-path", "--git-dir", "--namespace", "--work-tree"}
)

GH_TEXT_FLAGS = frozenset({"--title", "-t", "--body", "-b", "--subject", "--notes", "-n"})

# Options whose value is a search pattern, and options whose value is a file of patterns (read,
# so still checked). Every other listed option consumes a value that is checked as usual.
PATTERN_FLAGS = frozenset({"-e", "--regexp"})
PATTERN_FILE_FLAGS = frozenset({"-f", "--file"})
RG_VALUE_FLAGS = (
    PATTERN_FLAGS
    | PATTERN_FILE_FLAGS
    | frozenset(
        {
            "-A",
            "-B",
            "-C",
            "-E",
            "-M",
            "-T",
            "-d",
            "-g",
            "-j",
            "-m",
            "-r",
            "-t",
            "--after-context",
            "--before-context",
            "--color",
            "--colors",
            "--context",
            "--context-separator",
            "--encoding",
            "--engine",
            "--glob",
            "--iglob",
            "--ignore-file",
            "--max-columns",
            "--max-count",
            "--max-depth",
            "--max-filesize",
            "--path-separator",
            "--pre",
            "--pre-glob",
            "--replace",
            "--sort",
            "--sortr",
            "--threads",
            "--type",
            "--type-add",
            "--type-not",
        }
    )
)
GREP_VALUE_FLAGS = (
    PATTERN_FLAGS
    | PATTERN_FILE_FLAGS
    | frozenset(
        {
            "-A",
            "-B",
            "-C",
            "-D",
            "-d",
            "-m",
            "--after-context",
            "--before-context",
            "--binary-files",
            "--color",
            "--colour",
            "--context",
            "--devices",
            "--directories",
            "--exclude",
            "--exclude-dir",
            "--exclude-from",
            "--include",
            "--label",
            "--max-count",
        }
    )
)
SEARCH_PROGRAMS = {
    "rg": RG_VALUE_FLAGS,
    "grep": GREP_VALUE_FLAGS,
    "egrep": GREP_VALUE_FLAGS,
    "fgrep": GREP_VALUE_FLAGS,
}

SELECT_STRING_PROGRAMS = frozenset({"select-string", "sls"})
SELECT_STRING_VALUE_FLAGS = frozenset(
    {
        "-pattern",
        "-path",
        "-literalpath",
        "-encoding",
        "-context",
        "-include",
        "-exclude",
        "-inputobject",
    }
)

ECHO_PROGRAMS = frozenset({"echo", "printf", "write-output"})
CONTENT_WRITE_PROGRAMS = frozenset({"add-content", "ac", "set-content"})
CONTENT_PATH_FLAGS = frozenset({"-path", "-literalpath", "-filepath"})

# Option names (any program) whose value names something to leave out.
EXCLUDE_FLAGS = frozenset({"--exclude", "--exclude-dir"})
NEGATED_GLOB_FLAGS = frozenset({"-g", "--glob", "--iglob"})

INVOKE_EXPRESSION_PROGRAMS = frozenset({"iex", "invoke-expression"})
# ``iex -S mix`` is Elixir's shell, not ``Invoke-Expression``.
ELIXIR_IEX_ARGUMENT_RE = re.compile(r"^(?:-S|-v|-h|--[a-z][\w-]*)$")
START_PROCESS_PROGRAMS = frozenset({"start-process", "saps"})
START_PROCESS_TARGET_FLAGS = frozenset({"-filepath", "-path", "-pspath"})
START_PROCESS_SWITCHES = frozenset(
    {
        "-wait",
        "-nonewwindow",
        "-passthru",
        "-usenewenvironment",
        "-loaduserprofile",
        "-whatif",
        "-confirm",
    }
)
# Programs that run a command line or script handed to them. Starting one hides what runs.
SHELL_PROGRAMS = frozenset(
    {
        "bash",
        "bun",
        "cmd",
        "cscript",
        "dash",
        "deno",
        "fish",
        "ksh",
        "mshta",
        "node",
        "perl",
        "php",
        "powershell",
        "pwsh",
        "py",
        "python",
        "python3",
        "ruby",
        "rundll32",
        "sh",
        "wscript",
        "wsl",
        "zsh",
    }
)
# Flags after which a shell's remaining words are the command it runs.
SHELL_COMMAND_FLAG_RE = re.compile(r"(?i)^(?:-c|-co|-com|-comm|-comma|-comman|-command|/c|/k)$")
MAX_NESTED_SHELLS = 3


@dataclass(frozen=True)
class Token:
    kind: str  # "word", "redirect", "open", or "close"
    start: int
    end: int
    raw: str
    value: str


@dataclass
class Segment:
    tokens: list[Token]
    piped: bool  # input comes from a single ``|``


def _mask_quoted_heredocs(command: str) -> str:
    """Replace each literal heredoc substitution with filler of the same length."""
    return QUOTED_HEREDOC_SUBSTITUTION_RE.sub(lambda m: "x" * len(m.group(0)), command)


def tokenize(command: str) -> list[Segment]:
    """Split ``command`` into segments of tokens, keeping each token's span in ``command``."""
    segments = [Segment([], False)]
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in " \t":
            i += 1
        elif ch in SEGMENT_BREAKS:
            j = i
            while j < n and command[j] in SEGMENT_BREAKS:
                j += 1
            segments.append(Segment([], command[i:j].strip(" \t") == "|"))
            i = j
        elif ch in "()":
            segments[-1].tokens.append(Token("open" if ch == "(" else "close", i, i + 1, ch, ch))
            i += 1
        elif ch in "<>":
            j = i
            while j < n and command[j] in "<>&":
                j += 1
            segments[-1].tokens.append(Token("redirect", i, j, command[i:j], command[i:j]))
            i = j
        else:
            start, value = i, []
            while i < n and command[i] not in WORD_BREAKS:
                c = command[i]
                if c in "'\"":
                    i += 1
                    while i < n and command[i] != c:
                        if c == '"' and command[i] == "\\" and i + 1 < n:
                            value.append(command[i + 1])
                            i += 2
                            continue
                        value.append(command[i])
                        i += 1
                    i += 1
                    continue
                value.append(c)
                i += 1
            segments[-1].tokens.append(Token("word", start, i, command[start:i], "".join(value)))
    return [segment for segment in segments if segment.tokens]


@dataclass
class Command:
    words: list[Token]
    redirect_targets: list[Token]
    tokens: list[Token]  # everything from the program on, including parentheses
    piped: bool


def _commands(segment: Segment) -> list[Command]:
    """Split a segment at parentheses; each part runs as its own command."""
    commands: list[Command] = []
    current: list[Token] = []
    piped = segment.piped

    def flush(rest: list[Token]) -> None:
        nonlocal piped
        if current:
            words, targets = [], []
            after_redirect = False
            for token in current:
                if token.kind == "redirect":
                    after_redirect = True
                    continue
                (targets if after_redirect else words).append(token)
                after_redirect = False
            commands.append(Command(words, targets, current + rest, piped))
        piped = False

    for index, token in enumerate(segment.tokens):
        if token.kind in ("open", "close"):
            flush(segment.tokens[index:])
            current = []
        else:
            current.append(token)
    flush([])
    return commands


def program_name(word: str) -> str:
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _program_index(words: list[Token]) -> int | None:
    """Index of the program word, past ``sudo``, ``VAR=value`` and ``$x =`` prefixes."""
    index = 0
    while index < len(words):
        value = words[index].value
        if value == "sudo" or ASSIGNMENT_PREFIX_RE.match(value):
            index += 1
        elif value.startswith("$") and index + 1 < len(words) and words[index + 1].value == "=":
            index += 2
        else:
            return index
    return None


def _is_literal(token: Token) -> bool:
    return "$" not in token.raw and "`" not in token.raw


def _option(word: Token) -> tuple[str, int | None]:
    """Split ``--name=value`` into its name and the raw offset of the value."""
    if word.raw.startswith("--") and "=" in word.raw:
        name, _ = word.raw.split("=", 1)
        return name, len(name) + 1
    return word.value, None


@dataclass
class _Args:
    options: list[tuple[str, Token, int]]  # name, token holding the value, value offset
    positionals: list[Token]


def _parse(args: list[Token], value_flags: frozenset[str], *, fold_case: bool = False) -> _Args:
    parsed = _Args([], [])
    index = 0
    while index < len(args):
        word = args[index]
        if word.value == "--":
            parsed.positionals.extend(args[index + 1 :])
            break
        if word.value.startswith("-") and len(word.value) > 1:
            name, offset = _option(word)
            key = name.lower() if fold_case else name
            if offset is not None:
                parsed.options.append((key, word, offset))
            elif key in value_flags and index + 1 < len(args):
                parsed.options.append((key, args[index + 1], 0))
                index += 1
        else:
            parsed.positionals.append(word)
        index += 1
    return parsed


def _span(token: Token, offset: int = 0) -> list[tuple[int, int]]:
    if not _is_literal(token) or token.start + offset >= token.end:
        return []
    return [(token.start + offset, token.end)]


def _exclusion_spans(program: str, args: list[Token]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for index, word in enumerate(args):
        name, offset = _option(word)
        following = args[index + 1] if index + 1 < len(args) else None
        if name in EXCLUDE_FLAGS:
            if offset is not None:
                spans += _span(word, offset)
            elif following is not None:
                spans += _span(following)
        elif name.lower() == "-exclude" and following is not None:
            spans += _span(following)
        elif name in NEGATED_GLOB_FLAGS:
            if offset is not None and word.value[offset:].startswith("!"):
                spans += _span(word, offset)
            elif offset is None and following is not None and following.value.startswith("!"):
                spans += _span(following)

    # zip -x and robocopy /XF /XD take a list of names up to the next option.
    list_flags = {"zip": ("-x", "-"), "robocopy": ("/xf", "/")}.get(program)
    if program == "robocopy":
        starts = {"/xf", "/xd"}
    elif list_flags:
        starts = {list_flags[0]}
    else:
        return spans
    prefix = list_flags[1]
    collecting = False
    for word in args:
        if word.value.lower() in starts:
            collecting = True
        elif word.value.startswith(prefix):
            collecting = False
        elif collecting:
            spans += _span(word)
    return spans


def _search_spans(args: list[Token], value_flags: frozenset[str]) -> list[tuple[int, int]]:
    parsed = _parse(args, value_flags)
    spans: list[tuple[int, int]] = []
    named_pattern = False
    for name, token, offset in parsed.options:
        if name in PATTERN_FLAGS:
            spans += _span(token, offset)
            named_pattern = True
        elif name in PATTERN_FILE_FLAGS:
            named_pattern = True
    if not named_pattern and parsed.positionals:
        spans += _span(parsed.positionals[0])
    return spans


def _select_string_spans(args: list[Token]) -> list[tuple[int, int]]:
    parsed = _parse(args, SELECT_STRING_VALUE_FLAGS, fold_case=True)
    spans: list[tuple[int, int]] = []
    named_pattern = False
    for name, token, offset in parsed.options:
        if name == "-pattern":
            spans += _span(token, offset)
            named_pattern = True
    if not named_pattern and parsed.positionals:
        spans += _span(parsed.positionals[0])
    return spans


def _git_spans(args: list[Token]) -> list[tuple[int, int]]:
    index = 0
    while index < len(args) and args[index].value.startswith("-"):
        index += 2 if args[index].value in GIT_GLOBAL_VALUE_FLAGS else 1
    if index >= len(args):
        return []
    subcommand, rest = args[index].value, args[index + 1 :]

    if subcommand == "grep":
        return _search_spans(rest, GREP_VALUE_FLAGS)

    spans: list[tuple[int, int]] = []
    if subcommand in GIT_MESSAGE_SUBCOMMANDS:
        for position, word in enumerate(rest):
            name, offset = _option(word)
            following = rest[position + 1] if position + 1 < len(rest) else None
            if name == "--message" and offset is not None:
                spans += _span(word, offset)
            elif following is not None and (
                name in GIT_MESSAGE_FLAGS or re.fullmatch(r"-[a-zA-Z]*m", name)
            ):
                spans += _span(following)
    elif subcommand in GIT_SEARCH_SUBCOMMANDS:
        for position, word in enumerate(rest):
            name, offset = _option(word)
            following = rest[position + 1] if position + 1 < len(rest) else None
            if name == "--grep" and offset is not None:
                spans += _span(word, offset)
            elif name in GIT_SEARCH_FLAGS and following is not None:
                spans += _span(following)
    return spans


def _gh_spans(args: list[Token]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for index, word in enumerate(args):
        name, offset = _option(word)
        if name not in GH_TEXT_FLAGS:
            continue
        if offset is not None:
            spans += _span(word, offset)
        elif index + 1 < len(args):
            spans += _span(args[index + 1])
    return spans


def _is_ignore_file(token: Token) -> bool:
    return bool(IGNORE_FILE_RE.match(program_name(token.value)))


def _ignore_append_spans(
    program: str, command: Command, args: list[Token]
) -> list[tuple[int, int]]:
    if program in ECHO_PROGRAMS:
        if command.redirect_targets and all(_is_ignore_file(t) for t in command.redirect_targets):
            return [span for word in args for span in _span(word)]
        return []

    parsed = _parse(args, CONTENT_PATH_FLAGS | {"-value", "-encoding"}, fold_case=True)
    targets = [token for name, token, _ in parsed.options if name in CONTENT_PATH_FLAGS]
    values = [(token, offset) for name, token, offset in parsed.options if name == "-value"]
    positionals = parsed.positionals
    if not targets and positionals:
        targets, positionals = [positionals[0]], positionals[1:]
    if not targets or not all(_is_ignore_file(t) for t in targets):
        return []
    spans = [span for token, offset in values for span in _span(token, offset)]
    return spans + [span for word in positionals for span in _span(word)]


def _data_spans(command: Command) -> list[tuple[int, int]]:
    index = _program_index(command.words)
    if index is None:
        return []
    program = program_name(command.words[index].value)
    args = command.words[index + 1 :]

    spans = _exclusion_spans(program, args)
    if program == "git":
        spans += _git_spans(args)
    elif program == "gh":
        spans += _gh_spans(args)
    elif program in SEARCH_PROGRAMS:
        spans += _search_spans(args, SEARCH_PROGRAMS[program])
    elif program in SELECT_STRING_PROGRAMS:
        spans += _select_string_spans(args)
    elif program in ECHO_PROGRAMS or program in CONTENT_WRITE_PROGRAMS:
        spans += _ignore_append_spans(program, command, args)
    return spans


def mask_data_arguments(command: str) -> str:
    """Return ``command`` with literal data arguments replaced by ``_``.

    Data arguments are commit/tag/merge/stash/notes messages, ``gh`` titles, bodies and notes,
    search patterns (``rg``, ``grep``, ``git grep``, ``Select-String``, ``git log --grep``),
    exclusion values (``--exclude``, ``-Exclude``, ``--glob '!x'``, ``zip -x``,
    ``robocopy /XF /XD``), and text appended to an ignore file. An argument containing ``$`` or
    a backtick can run code, so it is never masked.
    """
    masked = _mask_quoted_heredocs(command)
    spans: list[tuple[int, int]] = []
    for segment in tokenize(masked):
        for parsed in _commands(segment):
            spans += _data_spans(parsed)
    if not spans:
        return command

    result, last = [], 0
    for start, end in sorted(set(spans)):
        if start < last:
            continue
        result.append(command[last:start])
        result.append("_")
        last = end
    result.append(command[last:])
    return "".join(result)


def _start_process_hides_program(args: list[Token]) -> bool:
    target = None
    index = 0
    while index < len(args):
        name = args[index].value.lower()
        if name in START_PROCESS_TARGET_FLAGS and index + 1 < len(args):
            target = args[index + 1]
            break
        if name.startswith("-"):
            index += 1 if name in START_PROCESS_SWITCHES else 2
            continue
        target = args[index]
        break
    if target is None or not _is_literal(target) or target.raw.startswith(("@", "(")):
        return True
    return program_name(target.value) in SHELL_PROGRAMS


def _invoke_expression_runs_code(command: Command, program_token: Token) -> bool:
    if command.piped and command.words and command.words[0] is program_token:
        return True
    position = command.tokens.index(program_token)
    following = command.tokens[position + 1 :]
    if not following:
        return False
    first = following[0]
    return not (first.kind == "word" and ELIXIR_IEX_ARGUMENT_RE.match(first.value))


def find_opaque_command(command: str, depth: int = 0) -> bool:
    """Return whether a command runs code its text does not show.

    ``iex``/``Invoke-Expression`` counts only as the program being run, and ``Start-Process``
    only when its program is a variable, an expression, or a shell. A shell's ``-c``/
    ``-Command``/``/c`` script is read the same way, a few levels deep.
    """
    for segment in tokenize(_mask_quoted_heredocs(command)):
        for parsed in _commands(segment):
            index = _program_index(parsed.words)
            if index is None:
                continue
            program_token = parsed.words[index]
            program = program_name(program_token.value)
            args = parsed.words[index + 1 :]
            if program in INVOKE_EXPRESSION_PROGRAMS:
                if _invoke_expression_runs_code(parsed, program_token):
                    return True
            elif program in START_PROCESS_PROGRAMS:
                if _start_process_hides_program(args):
                    return True
            elif program in SHELL_PROGRAMS and depth < MAX_NESTED_SHELLS:
                for word in args:
                    if SHELL_COMMAND_FLAG_RE.match(word.value):
                        # The script runs to the end of the segment, parentheses included.
                        rest = parsed.tokens[parsed.tokens.index(word) + 1 :]
                        script = " ".join(token.value for token in rest)
                        if find_opaque_command(script, depth + 1):
                            return True
                        break
    return False
