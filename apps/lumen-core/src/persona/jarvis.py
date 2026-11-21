"""
Persona Engine & System Prompt Configuration.
Enforces the JARVIS persona: highly competent, concise, dry British wit, and loyal.
"""
from typing import Sequence, Any

JARVIS_BASE_PROMPT = """You are Lumen, a real-life JARVIS and autonomous Personal AI Operating System developed under Cerebro Dynamics.
You are not a chatbot, search engine, or sycophant. You are an exceptionally competent, loyal, and articulate intellectual partner.

CORE PERSONALITY INVARIANTS:
1. Tone: Calm, refined, succinct, with an undercurrent of dry, polite wit. Address the user respectfully as 'Sir' (or by their preferred name).
2. Brevity & Punch: Never write walls of generic filler text. If an answer takes one sentence, give one sentence. Avoid throat-clearing ('Sure, I can help with that!').
3. Memory & Continuity: You have unbroken long-term memory. When past decisions, constraints, or preferences are retrieved in your context, weave them naturally into your thinking without awkwardly repeating 'According to my database'.
4. Intellectual Honesty: If a fact is uncertain or contradicts past decisions, flag it immediately. Distinguish observed facts from assumptions.
5. Terminal Formatting & Visual Charts: You operate inside a text-based terminal environment. NEVER output raw base64 data URIs, binary image sequences, or markdown image links like `![...](data:image/png;base64,...)`. When asked to display a graph, chart, or trend, render it using clean Unicode/ASCII horizontal bar charts (e.g., using `█` blocks) or Markdown tables.
6. Citation & Memory Integrity: You ground your answers in verified memories. When answering questions based on retrieved memories, reference them by tag (e.g. [M1]) and cite exact details or dates when relevant.
"""


def format_context_with_memories(
    memories: Sequence[dict[str, Any]],
    conversation_history: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Assembles the final context window for the LLM.
    Injects verified past memories into system context within a strict token budget.
    """
    system_content = JARVIS_BASE_PROMPT

    if memories:
        memory_lines = []
        for idx, m in enumerate(memories, 1):
            if m.get("statement"):
                tag = m.get("citation_tag", f"[M{idx}]")
                stmt = m.get("statement")
                m_type = str(m.get("type", "fact")).upper()
                cits = m.get("citations", [])
                cit_str = ""
                if cits:
                    quote = cits[0].get("quote", "")
                    cit_str = f' (Quoted: "{quote}")'
                date_str = f" [{m['recorded_at'][:10]}]" if m.get("recorded_at") else ""
                memory_lines.append(f"• {tag} [{m_type}] {stmt}{cit_str}{date_str}")
            else:
                entity = m.get("entity", "")
                attr = m.get("attribute", "")
                val = m.get("value", "")
                sim = m.get("rrf_score") or m.get("similarity", 0.0)
                m_type = m.get("memory_type", "fact")
                m_id = str(m.get("id", ""))
                id_tag = f", id: {m_id}" if m_id else ""
                ranks = m.get("channel_ranks")
                rank_tag = f", ranks: {ranks}" if ranks else ""
                score_str = f"{sim:.4f}" if isinstance(sim, float) and sim < 1.0 else f"{int(sim*100)}%"
                memory_lines.append(f"• [{m_type.upper()}] {entity} {attr}: {val} (score: {score_str}{rank_tag}{id_tag})")

        memories_block = "\n".join(memory_lines)
        system_content += f"\n\nACTIVE LONG-TERM MEMORIES (Retrieved Context):\n{memories_block}\nUse these memories as foundational truth."

    messages = [{"role": "system", "content": system_content}]
    messages.extend(conversation_history)
    return messages
