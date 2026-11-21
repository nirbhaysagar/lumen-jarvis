"""
AST Command Gatekeeper: Ring 1 of Safe Computer Control.
Parses, tokenizes, and recursively analyzes shell commands, compound pipelines,
subshells, protected credential paths, and inline Python AST to block destructive
operations, reverse shells, secret exfiltration, and drive wipes.
"""
from __future__ import annotations
import ast
import os
from pathlib import Path
import re
import shlex
from typing import Sequence
from .models import CommandRiskLevel, CommandEvaluation


class PythonSecurityASTVisitor(ast.NodeVisitor):
    """
    Inspects Python Abstract Syntax Trees for dangerous standard library calls,
    reverse shells, and destructive filesystem modifications.
    """
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.extracted_shell_commands: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {"ctypes", "pty"}:
                self.violations.append(f"Import of low-level security-sensitive module: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"ctypes", "pty"}:
            self.violations.append(f"Import from low-level security-sensitive module: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            val_id = getattr(node.func.value, "id", "")
            func_name = f"{val_id}.{node.func.attr}"

        # Destructive or breakout calls
        if func_name in {"shutil.rmtree", "os.unlink", "os.remove"}:
            # Check target argument
            if node.args and isinstance(node.args[0], ast.Constant):
                target_str = str(node.args[0].value)
                if target_str in {"/", "/*", "~", "*", ".", ".."}:
                    self.violations.append(f"Destructive Python call targeting root/critical path: {func_name}('{target_str}')")
            else:
                self.violations.append(f"Filesystem deletion call: {func_name}")

        elif func_name in {"os.system", "subprocess.call", "subprocess.Popen", "subprocess.run"}:
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                self.extracted_shell_commands.append(node.args[0].value)
            else:
                self.violations.append(f"Subprocess breakout call: {func_name}")

        elif func_name in {"eval", "exec", "compile"}:
            self.violations.append(f"Dynamic Python code execution: {func_name}")

        elif func_name in {"socket.connect", "s.connect"}:
            self.violations.append(f"Direct raw network socket connection: {func_name}")

        self.generic_visit(node)


class ASTGatekeeper:
    # Strictly prohibited patterns (Immediate FORBIDDEN)
    DESTRUCTIVE_PATTERNS = [
        # Recursive root/wildcard deletes
        r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f*|-[a-zA-Z]*f*r[a-zA-Z]*)\s+(/|/\*|~|~\*|\*|\.\.?|/\.\.)(?:\s|$|;|&|\|)",
        r"\brm\s+-[a-zA-Z]*\s+--no-preserve-root\b",
        # Fork bombs
        r":\(\)\s*\{\s*:\|:&\s*\};:",
        r"\bfork\s+while\s+fork\b",
        # Raw block device writes
        r"\bdd\s+.*of=/dev/(sd[a-z]|nvme[0-9]|disk[0-9])",
        r"\bmkfs(\.[a-z0-9]+)?\s+/dev/",
        r"\bfdisk\s+/dev/",
        # Dangerous root permission tampering
        r"\bchmod\s+(-R\s+)?(777|000)\s+(/|/\*|~)\b",
        r"\bchown\s+(-R\s+)?root\s+(/|/\*)\b",
        # Database destruction
        r"\bDROP\s+(DATABASE|SCHEMA|TABLE)\b",
        r"\bTRUNCATE\s+(TABLE)?\b",
        # Reverse shells & socket tunnels
        r"\bnc(\.traditional)?\s+.*-e\s+/bin/",
        r"\bbash\s+-i\s+>&?\s*/dev/tcp/",
        r"\bsocat\s+.*exec:",
        r"\bmkfifo\s+/tmp/.*nc\s+",
        # Shell startup persistence tampering
        r"(>>|>)\s*~?/?\.(bashrc|zshrc|bash_profile|zprofile|profile)\b",
    ]

    # Protected paths: credential vaults, environment secrets, system identity
    PROTECTED_PATH_PARTS = {
        ".ssh", ".aws", ".gnupg", ".netrc", ".azure", ".kube",
        ".env", ".env.local", ".env.production", ".env.staging",
        "shadow", "sudoers", "master.passwd",
        ".bashrc", ".zshrc", ".bash_profile", ".zprofile",
    }
    SENSITIVE_EXTENSIONS = {".pem", ".key", ".pfx", ".p12"}

    # Safe read-only commands (Auto-approved if no redirection or sensitive target)
    SAFE_COMMANDS = {
        "ls", "dir", "cat", "head", "tail", "grep", "egrep", "fgrep",
        "find", "pwd", "whoami", "echo", "which", "whereis", "uname",
        "date", "wc", "diff", "env", "printenv", "ps", "tree", "id", "sleep",
    }

    # Safe subcommands for git
    SAFE_GIT_SUBCOMMANDS = {
        "status", "log", "diff", "branch", "show", "tag", "remote", "rev-parse",
    }

    # Caution commands (Require Two-Man Rule confirmation)
    CAUTION_COMMANDS = {
        "mkdir", "cp", "mv", "touch", "rm", "python", "python3", "pytest",
        "pip", "pip3", "npm", "pnpm", "npx", "yarn", "docker", "docker-compose",
        "curl", "wget", "git", "bash", "sh", "zsh", "make", "cargo",
    }

    @classmethod
    def evaluate(cls, command: str) -> CommandEvaluation:
        """
        Evaluates a shell command string and determines its risk level.
        Handles chained pipelines (&&, ;, ||, |), subshell substitutions,
        protected credential paths, and inline Python AST inspection.
        """
        raw = command.strip()
        if not raw:
            return CommandEvaluation(
                command="",
                risk_level=CommandRiskLevel.SAFE,
                reason="Empty command.",
                is_allowed=True,
                requires_approval=False,
            )

        # 1. Global Regex Gate: Destructive / Reverse Shell Patterns
        for pattern in cls.DESTRUCTIVE_PATTERNS:
            if re.search(pattern, raw, re.IGNORECASE):
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.FORBIDDEN,
                    reason=f"Matched prohibited security pattern: {pattern}",
                    is_allowed=False,
                    requires_approval=False,
                )

        # 2. Extract and Recursively Evaluate Subshell Substitutions: $(...) and `...`
        subshells = re.findall(r"\$\((.*?)\)|`([^`]+)`", raw)
        for s1, s2 in subshells:
            sub_cmd = s1 or s2
            sub_eval = cls.evaluate(sub_cmd)
            if sub_eval.risk_level == CommandRiskLevel.FORBIDDEN:
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.FORBIDDEN,
                    reason=f"Subshell command prohibited: '{sub_cmd}' ({sub_eval.reason})",
                    is_allowed=False,
                    requires_approval=False,
                )

        # 3. Lexical Pipeline Splitter: Break chained commands (&&, ||, ;, |, &)
        segments = cls._split_into_segments(raw)
        if not segments:
            segments = [shlex.split(raw)]

        # 4. Evaluate Each Segment Individually and Compute Maximum Risk
        segment_evaluations: list[CommandEvaluation] = []
        for seg_tokens in segments:
            if not seg_tokens:
                continue
            seg_eval = cls._evaluate_segment(seg_tokens, raw)
            segment_evaluations.append(seg_eval)
            if seg_eval.risk_level == CommandRiskLevel.FORBIDDEN:
                return seg_eval

        # 5. Aggregate Results
        has_caution = any(e.risk_level == CommandRiskLevel.CAUTION for e in segment_evaluations)
        if has_caution:
            caution_reasons = [e.reason for e in segment_evaluations if e.risk_level == CommandRiskLevel.CAUTION]
            return CommandEvaluation(
                command=raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason="; ".join(caution_reasons),
                is_allowed=True,
                requires_approval=True,
            )

        # All segments evaluated as SAFE
        return CommandEvaluation(
            command=raw,
            risk_level=CommandRiskLevel.SAFE,
            reason="Read-only operations authorized.",
            is_allowed=True,
            requires_approval=False,
        )

    @classmethod
    def _split_into_segments(cls, raw: str) -> list[list[str]]:
        """Splits a compound command string into pipeline segments across all command delimiters and lines."""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return []

        delimiters = {";", "&&", "||", "|", "&"}
        segments: list[list[str]] = []

        for line in lines:
            try:
                lexer = shlex.shlex(line, punctuation_chars=True)
                lexer.whitespace_split = True
                tokens = list(lexer)
            except Exception:
                try:
                    tokens = shlex.split(line)
                except Exception:
                    tokens = line.split()

            current: list[str] = []
            for t in tokens:
                if t in delimiters:
                    if current:
                        segments.append(current)
                        current = []
                else:
                    # Strip wrapping quotes if preserved by shlex
                    if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
                        t = t[1:-1]
                    current.append(t)
            if current:
                segments.append(current)

        return segments

    @classmethod
    def _check_protected_paths(cls, tokens: Sequence[str]) -> str | None:
        """Checks if any token targets a sensitive credential or secret file."""
        for token in tokens:
            if token.startswith("-"):
                continue
            try:
                p = Path(token).expanduser()
                for part in p.parts:
                    if part in cls.PROTECTED_PATH_PARTS:
                        return token
                if p.suffix.lower() in cls.SENSITIVE_EXTENSIONS:
                    return token
                if p.name.startswith("id_rsa") or p.name.startswith("id_ed25519"):
                    return token
            except Exception:
                continue
        return None

    @classmethod
    def _evaluate_segment(cls, tokens: list[str], full_raw: str) -> CommandEvaluation:
        """Evaluates an individual command segment."""
        base_bin = tokens[0].lower()

        # Check rm commands with AST token precision
        if base_bin == "rm":
            is_recursive = any("r" in t.lower() for t in tokens if t.startswith("-")) or "--recursive" in tokens
            dangerous_targets = {"/", "/*", "~", "~/*", "*", ".", "..", "/..", "/."}
            if is_recursive:
                for target in tokens[1:]:
                    if not target.startswith("-") and target in dangerous_targets:
                        return CommandEvaluation(
                            command=full_raw,
                            risk_level=CommandRiskLevel.FORBIDDEN,
                            reason=f"Recursive deletion targeting dangerous path: '{target}'",
                            is_allowed=False,
                            requires_approval=False,
                        )

        # Check for Protected Credential or Secret Paths
        protected_target = cls._check_protected_paths(tokens[1:])
        if protected_target:
            # If modifying (rm, mv, chmod, redirect), treat as FORBIDDEN
            is_modifying = base_bin in {"rm", "mv", "touch", "chmod", "chown", "truncate", "shred"}
            has_redirection = any(">" in t for t in tokens)
            if is_modifying or has_redirection:
                return CommandEvaluation(
                    command=full_raw,
                    risk_level=CommandRiskLevel.FORBIDDEN,
                    reason=f"Modifying or deleting protected credential asset: '{protected_target}'",
                    is_allowed=False,
                    requires_approval=False,
                )
            # If read-only (cat, grep, head), elevate to CAUTION
            return CommandEvaluation(
                command=full_raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason=f"Protected credential asset access: command targets sensitive path '{protected_target}'",
                is_allowed=True,
                requires_approval=True,
            )

        # Check Python Inline Code via AST
        if base_bin in {"python", "python3"} and "-c" in tokens:
            try:
                c_idx = tokens.index("-c")
                if c_idx + 1 < len(tokens):
                    py_code = tokens[c_idx + 1]
                    tree = ast.parse(py_code)
                    visitor = PythonSecurityASTVisitor()
                    visitor.visit(tree)

                    if visitor.violations:
                        return CommandEvaluation(
                            command=full_raw,
                            risk_level=CommandRiskLevel.FORBIDDEN,
                            reason=f"Prohibited Python AST construct: {'; '.join(visitor.violations)}",
                            is_allowed=False,
                            requires_approval=False,
                        )

                    # Recursively check any shell commands spawned inside python
                    for sub_shell in visitor.extracted_shell_commands:
                        sub_eval = cls.evaluate(sub_shell)
                        if sub_eval.risk_level == CommandRiskLevel.FORBIDDEN:
                            return CommandEvaluation(
                                command=full_raw,
                                risk_level=CommandRiskLevel.FORBIDDEN,
                                reason=f"Python code executes prohibited shell command: '{sub_shell}'",
                                is_allowed=False,
                                requires_approval=False,
                            )
            except SyntaxError:
                # Malformed Python code -> CAUTION
                pass

        # Check Git Subcommands
        if base_bin == "git" and len(tokens) > 1:
            subcmd = tokens[1].lower()
            if subcmd in cls.SAFE_GIT_SUBCOMMANDS:
                return CommandEvaluation(
                    command=full_raw,
                    risk_level=CommandRiskLevel.SAFE,
                    reason=f"Read-only git command: git {subcmd}",
                    is_allowed=True,
                    requires_approval=False,
                )
            else:
                return CommandEvaluation(
                    command=full_raw,
                    risk_level=CommandRiskLevel.CAUTION,
                    reason=f"State-modifying git command: git {subcmd}",
                    is_allowed=True,
                    requires_approval=True,
                )

        # Check Output Redirection Operators
        if any(">" in t for t in tokens):
            return CommandEvaluation(
                command=full_raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason="Command contains output file redirection (> or >>).",
                is_allowed=True,
                requires_approval=True,
            )

        # Check Safe Read-Only commands
        if base_bin in cls.SAFE_COMMANDS:
            return CommandEvaluation(
                command=full_raw,
                risk_level=CommandRiskLevel.SAFE,
                reason=f"Standard read-only utility: {base_bin}",
                is_allowed=True,
                requires_approval=False,
            )

        # Check Caution list
        if base_bin in cls.CAUTION_COMMANDS:
            return CommandEvaluation(
                command=full_raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason=f"Executable capable of state modification: {base_bin}",
                is_allowed=True,
                requires_approval=True,
            )

        # Default to CAUTION for unknown binaries (Defense in Depth)
        return CommandEvaluation(
            command=full_raw,
            risk_level=CommandRiskLevel.CAUTION,
            reason=f"Unrecognized binary '{base_bin}'; defaulting to CAUTION policy.",
            is_allowed=True,
            requires_approval=True,
        )
