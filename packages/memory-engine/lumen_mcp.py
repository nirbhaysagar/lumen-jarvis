                ]
                by_type = res.get("by_type", {})
                if by_type:
                    lines.append("\n**Discovered edge breakdown:**")
                    for t, c in by_type.items():
                        lines.append(f"- `{t}`: {c}")
                return {"content": [{"type": "text", "text": "\n".join(lines)}], "isError": False}

            elif name == "consolidate_memories":
                chain_id = args.get("chain_id") or None
                arch = bool(args.get("archive_constituents", False))
                res = L.consolidate(chain_id=chain_id, archive_constituents=arch, path=self.db_path)
                lines = [
                    f"### Sleep Cycle Consolidation ({res.get('chain_id', 'all')})",
                    f"- **Scanned memories**: {res.get('scanned', 0)}",
                    f"- **Clusters identified**: {res.get('clusters_found', 0)}",
                    f"- **Synthesis cards created**: {res.get('syntheses_created', 0)}"
                ]
                details = res.get("details", [])
                if details:
                    lines.append("\n**Syntheses created:**")
                    for d in details:
                        lines.append(f"- **{d['topic']}** ({d['constituent_count']} cards consolidated):\n  _{d['content']}_")
                return {"content": [{"type": "text", "text": "\n".join(lines)}], "isError": False}

            else:
