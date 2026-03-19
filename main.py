from rag_setup import (
    qa_chain_with_context, chat_history,
    retrieve_with_metadata, expand_query,
    classify_and_generate_sql, execute_faculty_sql, format_sql_results,
    validate_faculty_sql, retrieve_supporting_urls,
)
import re
import time
import sys
import math
import os

URL_PATTERN = re.compile(r"https?://[^\s)\]\}>\"']+")

ascii=r"""
███████╗ █████╗ ██╗  ██╗██████╗ ██████╗  █████╗ ██╗   ██╗ █████╗     ██████╗  █████╗  ██████╗    ██╗  ██╗
██╔════╝██╔══██╗██║  ██║██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝██╔══██╗    ██╔══██╗██╔══██╗██╔════╝    ╚██╗██╔╝
███████╗███████║███████║██████╔╝██║  ██║███████║ ╚████╔╝ ███████║    ██████╔╝███████║██║  ███╗    ╚███╔╝ 
╚════██║██╔══██║██╔══██║██╔══██╗██║  ██║██╔══██║  ╚██╔╝  ██╔══██║    ██╔══██╗██╔══██║██║   ██║    ██╔██╗ 
███████║██║  ██║██║  ██║██║  ██║██████╔╝██║  ██║   ██║   ██║  ██║    ██║  ██║██║  ██║╚██████╔╝██╗██╔╝ ██╗
╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝    ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝╚═╝  ╚═╝
                                                                                                                                                                                            
                                                                                                                          """

print(ascii)
print("Sahrdaya RAG Chat Terminal")
print("Type /help for commands, 'exit' to quit\n")

# ─── Session stats tracking ────────────────────────────────────────────────────

session_stats = []          # list of dicts per query
session_start = time.time()

# ─── Helpers ────────────────────────────────────────────────────────────────────

def estimate_tokens(text):
    return len(text) // 4

def build_history_text():
    h = ""
    for i in range(0, len(chat_history), 2):
        if i + 1 < len(chat_history):
            h += f"User: {chat_history[i]}\nAssistant: {chat_history[i+1]}\n\n"
    return h


def _extract_urls(text, limit=8):
    """Extract unique URLs in appearance order."""
    found = URL_PATTERN.findall(text or "")
    cleaned = []
    seen = set()

    def _is_static_asset(u):
        low = u.lower().split("?")[0]
        return low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico", ".css", ".js"))

    def _priority(u):
        low = u.lower()
        if ".pdf" in low or "alt=media" in low:
            return 0
        if any(ext in low for ext in [".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"]):
            return 1
        return 2

    candidates = []
    for url in found:
        u = url.rstrip(".,;:)")
        if not u or u in seen or _is_static_asset(u):
            continue
        seen.add(u)
        candidates.append(u)

    for u in sorted(candidates, key=_priority):
        cleaned.append(u)
        if len(cleaned) >= limit:
            break
    return cleaned


def _query_likely_needs_links(query):
    q = (query or "").lower()
    keywords = [
        "link", "links", "url", "download", "pdf", "document", "docs",
        "placement", "placements", "stats", "statistics", "report", "handbook",
        "regulation", "syllabus", "approval", "audit",
    ]
    return any(k in q for k in keywords)


def _format_fallback_links(query, urls):
    q = (query or "").lower()
    unique = []
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    if "placement" not in q:
        return "Direct links from context:\n" + "\n".join(f"- {u}" for u in unique)

    year_links = []
    extra_links = []
    for u in unique:
        low = u.lower()
        m = re.search(r"tpo%2fplacement%2fsah%2f(\d{4}-\d{2})", low)
        if not m:
            m = re.search(r"/tpo/placement/sah/(\d{4}-\d{2})", low)
        if m:
            year_links.append((m.group(1), u))
        else:
            extra_links.append(u)

    def _year_start(label):
        try:
            return int(label.split("-")[0])
        except Exception:
            return 9999

    year_links.sort(key=lambda x: _year_start(x[0]))

    lines = ["Verified placement report links (year-wise):"]
    for y, u in year_links:
        lines.append(f"- {y}: {u}")
    for u in extra_links:
        lines.append(f"- {u}")
    return "\n".join(lines)


def _harmonize_response_with_links(response, links_appended):
    """Remove contradictory 'no direct URL' claims when links are present below."""
    if not links_appended:
        return response
    text = response or ""
    replacement_map = [
        (r"\*?No\s+direct\s+(?:URL|URLs|link|links?)\s+(?:was|were|is|are)\s+(?:present|provided)\s+in\s+the\s+context\.?\*?", "-"),
        (r"\*No URL provided in the context\*", "-"),
        (r"\*no direct urls? (?:are|were) (?:present|provided) in the context\*", "-"),
        (r"No direct URL \(if any\)\s*[:\-]?\s*No[^\n|.]*", "-"),
        (r"-\s*\*\*Download links\*\*[^\n]*", ""),
        (r"\*\*Download links\*\*[^\n]*", ""),
        (r"the context mentions[^\n]*actual download links?[^\n]*\.", ""),
    ]
    for pat, repl in replacement_map:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ─── Stats box ──────────────────────────────────────────────────────────────────

def format_stats_box(stat):
    """Build a rich stats box from a stat dict."""
    rt      = stat["response_time"]
    p_tok   = stat["prompt_tokens"]
    r_tok   = stat["response_tokens"]
    h_tok   = stat["history_tokens"]
    ctx_tok = stat["context_tokens"]
    total   = p_tok + r_tok + h_tok + ctx_tok
    total_kb = stat["total_kb"]
    chunks  = stat["chunk_ids"]
    n_docs  = stat["num_docs"]
    turn    = stat["turn"]

    chunk_str = ", ".join(c.replace("chunk_", "") for c in chunks) if chunks else "N/A (direct)"
    speed = r_tok / rt if rt > 0 else 0

    lines = [
        f"  Response Time    : {rt:.2f}s",
        f"  Speed            : ~{speed:.0f} tok/s",
        f"  Prompt Tokens    : ~{p_tok}",
        f"  Response Tokens  : ~{r_tok}",
        f"  Context Tokens   : ~{ctx_tok}  ({n_docs} docs)",
        f"  History Tokens   : ~{h_tok}",
        f"  Total Window     : ~{total} tokens ({total_kb:.1f} KB)",
        f"  Chat Turn        : #{turn}",
        f"  Chunks Retrieved : {chunk_str}",
    ]
    width = max(len(l) for l in lines) + 2
    border = "+" + "-" * width + "+"
    title  = "|" + " STATS ".center(width) + "|"
    box = "\n" + border + "\n" + title + "\n" + border + "\n"
    for l in lines:
        box += "|" + l.ljust(width) + "|\n"
    box += border
    return box

# ─── ASCII bar chart helper ─────────────────────────────────────────────────────

def ascii_bar(label, value, max_val, bar_width=30, symbol="█"):
    filled = int((value / max_val) * bar_width) if max_val > 0 else 0
    bar = symbol * filled + "░" * (bar_width - filled)
    return f"  {label:<6} |{bar}| {value:.2f}"

def sparkline(values, width=40):
    """Tiny sparkline using unicode blocks."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    blocks = " ▁▂▃▄▅▆▇█"
    out = ""
    # Resample to width
    step = max(1, len(values) / width)
    i = 0.0
    while i < len(values) and len(out) < width:
        idx = int(min(8, ((values[int(i)] - mn) / rng) * 8))
        out += blocks[idx]
        i += step
    return out

# ─── /graph command ─────────────────────────────────────────────────────────────

def show_graph():
    if not session_stats:
        print("  No queries yet. Ask something first!\n")
        return

    elapsed = time.time() - session_start
    print()
    print("+" + "=" * 62 + "+")
    print("|" + " SESSION DASHBOARD ".center(62) + "|")
    print("+" + "=" * 62 + "+")
    print(f"  Session Duration : {elapsed:.0f}s  |  Queries : {len(session_stats)}")
    print()

    # ── Response time bar chart ──
    times = [s["response_time"] for s in session_stats]
    max_t = max(times) if times else 1
    print("  RESPONSE TIME (seconds)")
    print("  " + "-" * 48)
    for i, t in enumerate(times):
        print(ascii_bar(f"Q{i+1}", t, max_t))
    print()

    # ── Sparkline of response times ──
    print(f"  Timeline : [{sparkline(times)}]")
    print(f"             min={min(times):.2f}s  avg={sum(times)/len(times):.2f}s  max={max(times):.2f}s")
    print()

    # ── Token usage per query ──
    print("  TOKEN USAGE PER QUERY")
    print("  " + "-" * 48)
    for i, s in enumerate(session_stats):
        total = s["prompt_tokens"] + s["response_tokens"] + s["history_tokens"] + s["context_tokens"]
        print(ascii_bar(f"Q{i+1}", total, total * 1.2, symbol="▓"))
    print()

    # ── Chunk heatmap ──
    all_chunks = {}
    for s in session_stats:
        for c in s["chunk_ids"]:
            all_chunks[c] = all_chunks.get(c, 0) + 1
    if all_chunks:
        print("  CHUNK RETRIEVAL HEATMAP (most used)")
        print("  " + "-" * 48)
        sorted_chunks = sorted(all_chunks.items(), key=lambda x: -x[1])[:15]
        max_c = sorted_chunks[0][1] if sorted_chunks else 1
        for cid, cnt in sorted_chunks:
            bar_len = int((cnt / max_c) * 20)
            print(f"    {cid:<12} {'█' * bar_len}{'░' * (20 - bar_len)} ({cnt}x)")
        print()

    # ── Context window growth ──
    totals = []
    for s in session_stats:
        totals.append(s["prompt_tokens"] + s["response_tokens"] + s["history_tokens"] + s["context_tokens"])
    print(f"  Context Growth : [{sparkline(totals)}]")
    print(f"                   {totals[0]} tok -> {totals[-1]} tok")
    print()

    # ── Summary table ──
    print("  SUMMARY")
    print("  " + "-" * 48)
    avg_time = sum(times) / len(times)
    total_tokens = sum(s["prompt_tokens"] + s["response_tokens"] + s["history_tokens"] + s["context_tokens"] for s in session_stats)
    total_docs = sum(s["num_docs"] for s in session_stats)
    print(f"    Total Queries   : {len(session_stats)}")
    print(f"    Avg Response    : {avg_time:.2f}s")
    print(f"    Total Tokens    : ~{total_tokens}")
    print(f"    Total Docs Used : {total_docs}")
    print(f"    Unique Chunks   : {len(all_chunks)}")
    print("+" + "=" * 62 + "+")
    print()

# ─── /chunks command ─────────────────────────────────────────────────────────────

def show_last_chunks():
    if not session_stats:
        print("  No queries yet.\n")
        return
    last = session_stats[-1]
    print(f"\n  Last query used {last['num_docs']} chunks:")
    for cid in last["chunk_ids"]:
        print(f"    -> {cid}")
    print()

# ─── /history command ────────────────────────────────────────────────────────────

def show_history():
    if not chat_history:
        print("  No conversation history yet.\n")
        return
    print()
    for i in range(0, len(chat_history), 2):
        turn = i // 2 + 1
        print(f"  [{turn}] You: {chat_history[i][:80]}{'...' if len(chat_history[i]) > 80 else ''}")
        if i + 1 < len(chat_history):
            reply = chat_history[i + 1]
            print(f"      Bot: {reply[:80]}{'...' if len(reply) > 80 else ''}")
    print()

# ─── /help command ───────────────────────────────────────────────────────────────

def show_help():
    print("""
  ╔══════════════════════════════════════════════════╗
  ║              AVAILABLE COMMANDS                  ║
  ╠══════════════════════════════════════════════════╣
  ║  /graph     Session dashboard with ASCII charts  ║
  ║  /chunks    Show chunks used in last retrieval   ║
  ║  /history   Show conversation history            ║
  ║  /stats     Re-show last query stats box         ║
  ║  /clear     Clear conversation history           ║
  ║  /reset     Reset session stats                  ║
  ║  /help      Show this help message               ║
  ║  exit       Quit the program                     ║
  ╚══════════════════════════════════════════════════╝
""")

# ─── Main loop ──────────────────────────────────────────────────────────────────

while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye!")
        break

    if not user_input:
        continue

    if user_input.lower() in ["exit", "quit"]:
        print("Goodbye!")
        break

    # ── Slash commands ──
    cmd = user_input.lower().strip()
    if cmd == "/help":
        show_help()
        continue
    if cmd == "/graph":
        show_graph()
        continue
    if cmd == "/chunks":
        show_last_chunks()
        continue
    if cmd == "/history":
        show_history()
        continue
    if cmd == "/stats":
        if session_stats:
            print(format_stats_box(session_stats[-1]))
        else:
            print("  No queries yet.\n")
        continue
    if cmd == "/clear":
        chat_history.clear()
        print("  Conversation history cleared.\n")
        continue
    if cmd == "/reset":
        session_stats.clear()
        chat_history.clear()
        print("  Session stats & history reset.\n")
        continue

    print("Assistant: ", end="", flush=True)

    history_text = build_history_text()

    # ── Step 1: Try SQL path (faculty database) ──────────────────────────────
    t_start = time.time()
    sql_query = classify_and_generate_sql(user_input, history_text)

    if sql_query:
        # Validate that SQL references only existing columns
        if not validate_faculty_sql(sql_query):
            print("  [SQL fallback] Invalid columns in query, using RAG...\n  ", end="")
            sql_query = None  # force RAG path

    if sql_query:
        sql_result = execute_faculty_sql(sql_query)
        if sql_result:
            columns, rows = sql_result
            # If SQL returned 0 rows, fall back to RAG instead of showing empty result
            if not rows:
                print("  [SQL fallback] No results, using RAG...\n  ", end="")
                sql_query = None  # force RAG path below
            else:
                response = format_sql_results(columns, rows, user_input)
                t_end = time.time()
                print(response)
                print(f"\n  [SQL] {sql_query}")

                chat_history.append(user_input)
                # Store a compact summary in history instead of the full table
                # to prevent token overflow on subsequent LLM calls.
                if len(rows) > 5:
                    summary = f"[SQL result: {len(rows)} rows from faculty database for query: {sql_query}]"
                else:
                    summary = response
                chat_history.append(summary)
                h_text = build_history_text()
                stat = {
                    "response_time": t_end - t_start,
                    "prompt_tokens": estimate_tokens(user_input),
                    "response_tokens": estimate_tokens(response),
                    "history_tokens": estimate_tokens(h_text),
                    "context_tokens": 0,
                    "total_kb": len((user_input + response + h_text).encode("utf-8")) / 1024,
                    "chunk_ids": [],
                    "num_docs": 0,
                    "turn": len(chat_history) // 2,
                    "query": user_input,
                    "sql": sql_query,
                }
                session_stats.append(stat)
                print(format_stats_box(stat))
                print()
                continue
        elif sql_result is None:
            # SQL generated but execution failed — fall through to RAG
            print("  [SQL fallback] Query failed, using RAG...\n  ", end="")

    # ── Step 2: Regular RAG query with chunk tracking ──────────────────────────
    context_str, chunk_ids, num_docs = retrieve_with_metadata(user_input)

    t_start_rag = time.time()
    response = qa_chain_with_context.invoke({
        "question": user_input,
        "chat_history": history_text,
        "context": context_str,
    })

    # Terminal users need visible plain URLs; append from context if model omitted them.
    if _query_likely_needs_links(user_input) and not URL_PATTERN.search(response or ""):
        fallback_urls = _extract_urls(context_str, limit=6)
        if not fallback_urls:
            fallback_urls = retrieve_supporting_urls(user_input, limit=6)
        if fallback_urls:
            response = response.rstrip() + "\n\n" + _format_fallback_links(user_input, fallback_urls)
            response = _harmonize_response_with_links(response, links_appended=True)

    t_end = time.time()

    # Use total time (includes SQL classification attempt if it returned NOT_SQL)
    total_time = t_end - t_start

    assistant_reply = response
    print(assistant_reply)

    chat_history.append(user_input)
    chat_history.append(assistant_reply)

    h_text = build_history_text()
    stat = {
        "response_time": total_time,
        "prompt_tokens": estimate_tokens(user_input),
        "response_tokens": estimate_tokens(assistant_reply),
        "history_tokens": estimate_tokens(h_text),
        "context_tokens": estimate_tokens(context_str),
        "total_kb": len((user_input + assistant_reply + h_text + context_str).encode("utf-8")) / 1024,
        "chunk_ids": chunk_ids,
        "num_docs": num_docs,
        "turn": len(chat_history) // 2,
        "query": user_input,
    }
    session_stats.append(stat)
    print(format_stats_box(stat))
    print()
