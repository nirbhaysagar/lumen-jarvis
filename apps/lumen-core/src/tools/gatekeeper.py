"""
AST Command Gatekeeper: Ring 1 of Safe Computer Control.
Parses and tokenizes shell commands before execution to identify risk levels
and block destructive operations (rm -rf /, fork bombs, drive wipes).
"""
from __future__ import annotations
import re
import shlex
from .models import CommandRiskLevel, CommandEvaluation


class ASTGatekeeper:
    # Strictly prohibited patterns (Immediate FORBIDDEN)
    DESTRUCTIVE_PATTERNS = [
        # Recursive root/wildcard deletes regex
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
    ]

    # Safe read-only commands (Auto-approved)
    SAFE_COMMANDS = {
        "ls", "dir", "cat", "head", "tail", "grep", "egrep", "fgrep",
        "find", "pwd", "whoami", "echo", "which", "whereis", "uname",
        "date", "wc", "diff", "env", "printenv", "ps", "tree", "id",
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

        # 1. Check for Destructive / Forbidden Patterns
        for pattern in cls.DESTRUCTIVE_PATTERNS:
            if re.search(pattern, raw, re.IGNORECASE):
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.FORBIDDEN,
                    reason=f"Matched prohibited destructive pattern: {pattern}",
                    is_allowed=False,
                    requires_approval=False,
                )

        # 2. Tokenize command via shlex
        try:
            tokens = shlex.split(raw)
        except ValueError as e:
            # Unbalanced quotes or malformed syntax
            return CommandEvaluation(
                command=raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason=f"Syntax tokenization warning: {e}",
                is_allowed=True,
                requires_approval=True,
            )

        if not tokens:
            return CommandEvaluation(
                command=raw,
                risk_level=CommandRiskLevel.SAFE,
                reason="No tokens found.",
                is_allowed=True,
                requires_approval=False,
            )

        base_bin = tokens[0].lower()

        # Check rm commands with AST token precision
        if base_bin == "rm":
            is_recursive = any("r" in t.lower() for t in tokens if t.startswith("-")) or "--recursive" in tokens
            dangerous_targets = {"/", "/*", "~", "~/*", "*", ".", "..", "/..", "/."}
            if is_recursive:
                for target in tokens[1:]:
                    if not target.startswith("-") and target in dangerous_targets:
                        return CommandEvaluation(
                            command=raw,
                            risk_level=CommandRiskLevel.FORBIDDEN,
                            reason=f"Recursive deletion targeting dangerous path: '{target}'",
                            is_allowed=False,
                            requires_approval=False,
                        )

        # 3. Check for Git Safe vs Caution subcommands
        if base_bin == "git" and len(tokens) > 1:
            subcmd = tokens[1].lower()
            if subcmd in cls.SAFE_GIT_SUBCOMMANDS:
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.SAFE,
                    reason=f"Read-only git command: git {subcmd}",
                    is_allowed=True,
                    requires_approval=False,
                )
            else:
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.CAUTION,
                    reason=f"State-modifying git command: git {subcmd}",
                    is_allowed=True,
                    requires_approval=True,
                )

        # 4. Check against Safe Read-Only commands
        if base_bin in cls.SAFE_COMMANDS:
            # Check if command contains write redirection operators (> or >>)
            if ">" in tokens or ">>" in tokens or any(">" in t for t in tokens):
                return CommandEvaluation(
                    command=raw,
                    risk_level=CommandRiskLevel.CAUTION,
                    reason="Read-only command contains output file redirection (>).",
                    is_allowed=True,
                    requires_approval=True,
                )
            return CommandEvaluation(
                command=raw,
                risk_level=CommandRiskLevel.SAFE,
                reason=f"Standard read-only utility: {base_bin}",
                is_allowed=True,
                requires_approval=False,
            )

        # 5. Check against Caution list
        if base_bin in cls.CAUTION_COMMANDS:
            return CommandEvaluation(
                command=raw,
                risk_level=CommandRiskLevel.CAUTION,
                reason=f"Executable capable of state modification: {base_bin}",
                is_allowed=True,
                requires_approval=True,
            )

        # 6. Default to CAUTION for unknown binaries (Defense in Depth)
        return CommandEvaluation(
            command=raw,
            risk_level=CommandRiskLevel.CAUTION,
            reason=f"Unrecognized binary '{base_bin}'; defaulting to CAUTION policy.",
            is_allowed=True,
            requires_approval=True,
        )
