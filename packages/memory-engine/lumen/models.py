    valid_to: str | None = None
    supersedes_id: str | None = None
    ingest_key: str | None = None   # idempotency (normalized hash, chain-scoped)
    # --- 5-layer additions (Context Graph Platform) ---
    chain_id: str = "default"       # L2 isolation: research/coding/orbit-launch
    template: str = "general"       # extraction template used
    enhanced_content: str | None = None  # user-curated version (Back Side)
    enhancement_level: int = 0      # 0=auto, 1=user-touched, 2=heavily curated
    # --- memory health (never fade what matters, fade the rest) ---
    pinned: int = 0                 # 1 = exempt from archiving, ranks first
    last_seen: str | None = None    # last time recall served this card (decay signal)
    snippet: str | None = None      # Highlighted search match snippet if available
    created_at: str = field(default_factory=now_iso)

    @property
    def display_content(self) -> str:
        return self.enhanced_content or self.content

    @property
    def effective_confidence(self) -> float:
        """Traversal weighting: curation boosts trust."""
