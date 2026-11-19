        return 0

    elif action == "install-git":
        try:
            hook_file = install_git_hook(repo_path=args.repo, strict=args.strict)
            print(c(f"✓ Git pre-commit hook installed successfully at:", "green", "bold"))
            print(f"  {hook_file}")
            return 0
        except Exception as e:
            print(c(f"Error installing Git hook: {e}", "red", "bold"), file=sys.stderr)
            return 1

    elif action == "prompt":
        prompt_str = generate_shell_prompt(chain_id=args.chain, path=args.db)
        print(prompt_str)
        return 0

    return 0


# -------------------------------------------------------------
# CLI Entrypoint
# -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lumen",
        description="Lumen: Ambient Developer Long-Term Memory Engine (Zero External Dependencies)",
    )
    parser.add_argument("--db", default="lumen.db", help="Path to SQLite database (default: lumen.db)")
    parser.add_argument("--chain", default=None, help="Workspace / Chain ID (default: all or auto)")

    sub = parser.add_subparsers(dest="command", required=True)

    # 1. remember
    p_rem = sub.add_parser("remember", help="Ingest raw text and extract canonical memory cards")
    p_rem.add_argument("text", nargs="+", help="Content or observation to remember")
    p_rem.add_argument("--trust", type=float, default=0.5, help="Source trust rating (0.0 to 1.0)")
    p_rem.add_argument("--origin", default="cli", help="Origin label (e.g. terminal, slack, meeting)")

    # 2. recall
    p_rec = sub.add_parser("recall", help="Hybrid search (BM25 + Dense Vectors) across memories")
    p_rec.add_argument("query", nargs="+", help="Keywords or conceptual search query")
    p_rec.add_argument("--limit", type=int, default=8, help="Max results to return")
    p_rec.add_argument("--json", action="store_true", help="Output results as JSON")

    # 3. context
    p_ctx = sub.add_parser("context", help="Assemble prompt-ready context block for LLMs")
    p_ctx.add_argument("query", nargs="+", help="Query / prompt topic")
    p_ctx.add_argument("--limit", type=int, default=12, help="Max memories in context")
    p_ctx.add_argument("--copy", action="store_true", help="Copy context block to system clipboard")

    # 4. ask
    p_ask = sub.add_parser("ask", help="Synthesize answers based on long-term memory")
    p_ask.add_argument("query", nargs="+", help="Question to ask")

    # 5. status
    p_stat = sub.add_parser("status", help="Show terminal dashboard of memory health and stats")

    # 6. sleep
    p_slp = sub.add_parser("sleep", help="Run Sleep Cycle consolidation into executive syntheses")
    p_slp.add_argument("--archive", action="store_true", help="Archive constituent memories into synthesis")

    # 7. sync
    p_syn = sub.add_parser("sync", help="Backfill missing dense vector embeddings")

    # 8. review
    p_rev = sub.add_parser("review", help="List ambiguous conflicts and low-confidence items")

    # 9. hook
    p_hk = sub.add_parser("hook", help="Git and Shell context hooks")
    p_hk.add_argument("hook_action", choices=["check-commit", "install-git", "prompt"], help="Hook action")
    p_hk.add_argument("--repo", default=".", help="Path to git repository (default: current directory)")
    p_hk.add_argument("--strict", action="store_true", help="Fail with exit code 1 if constraints exist")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

