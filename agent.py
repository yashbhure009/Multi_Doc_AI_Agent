import os
from typing import Literal, TypedDict
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from safe_exec import clean_code, result_text, run_code, to_frame, validate_code
from store import FileStore
load_dotenv()
MODEL = "openai/gpt-oss-120b"
def get_llm() -> ChatGroq:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is missing. Add it to your .env file.")
    return ChatGroq(model=MODEL, temperature=0, api_key=key)
class RoutePlan(BaseModel):
    reasoning: str = Field(description="One or two sentences: what subject the question is about and which files match that subject.")
    standalone_question: str = Field(description="Question rewritten so it needs no chat history.")
    pdf_files: list[str] = Field(default_factory=list, description="Exact PDF names likely to answer it.")
    table_keys: list[str] = Field(default_factory=list, description="Exact table keys likely to answer it.")
class AgentState(TypedDict, total=False):
    question: str
    history: str
    standalone: str
    route_reason: str
    route: Literal["pdf", "data", "both", "none"]
    pdf_files: list
    table_keys: list
    pdf_evidence: list
    data_results: list
    answer: str
def format_history(messages: list, n: int = 6) -> str:
    lines = []
    for m in messages[-n:]:
        who = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{who}: {m['content'][:600]}")
    return "\n".join(lines) or "(none)"
def _match(raw: list, valid: dict) -> list:
    lower = {k.lower(): k for k in valid}
    out = []
    for r in raw:
        name = str(r).strip().lower()
        k = lower.get(name)
        if k is None and name:  # tolerate small naming differences (e.g. missing extension)
            hits = [orig for low, orig in lower.items() if name in low or low in name]
            k = hits[0] if len(hits) == 1 else None
        if k and k not in out:
            out.append(k)
    return out
def build_graph(store: FileStore):
    llm = get_llm()
    router_llm = llm.with_structured_output(RoutePlan)
    # ====================================================================
    # TOOL 1: rag_pdf_tool
    # ====================================================================
    @tool
    def rag_pdf_tool(question: str, files: list[str]) -> list[dict]:
        """Search the uploaded PDF documents for passages that answer a question.
        Use this for any question about the content of a PDF file.
        Arguments: question is a standalone question; files is the list of exact
        uploaded PDF file names to search.
        Returns a list of excerpts, each with file, page, text and distance."""
        k = 4 if len(files) <= 3 else 3
        return store.search_pdfs(question, files, k_per_file=k)
    # ====================================================================
    # TOOL 2: query_generator_tool
    # ====================================================================
    @tool
    def query_generator_tool(question: str, table_keys: list[str]) -> dict:
        """Answer a question about uploaded CSV or Excel tables by generating and
        running pandas code. Use this for counting, filtering, aggregating, ranking,
        comparing or merging tabular data.
        Arguments: question is a standalone question; table_keys is the list of exact
        table keys to use (a CSV file name, or 'book.xlsx::Sheet1' for an Excel sheet).
        Returns a dict with tables, code, error, text and frame."""
        tables = {k: store.tables[k].df for k in table_keys}
        descriptions = "\n".join(store.describe_table(k) for k in table_keys)
        code_prompt = f"""You are a careful data analyst. Write pandas code that answers the question
using ONLY the tables below.

TABLES (access each as dfs["<key>"]; when there is only one table it is also available as df):
{descriptions}

QUESTION: {question}

RULES:
1. pd and np are preloaded. No imports, no file or network access.
2. Assign the final answer to a variable named result (scalar, string, Series or DataFrame).
3. Output raw Python only: no markdown fences, no comments, no explanations.
4. Never hardcode values you did not compute. Never invent columns.
5. Match text case-insensitively and ignore surrounding spaces (.str.strip().str.lower());
   use .str.contains(..., case=False, na=False) for partial name matching.
6. If a numeric/date column has dtype object, convert it first with
   pd.to_numeric(..., errors="coerce") or pd.to_datetime(..., errors="coerce").
7. Keep every requested filter, grouping, calculation, sort order, limit and output column.
   For top-N-per-group: sort, then groupby(...).head(N).
8. To combine tables use merge on the appropriate columns.
9. Return at most 200 rows and include identifying columns (e.g. names) next to computed values.
10. If the tables lack what the question needs, set:
    result = "Missing information: <what is missing>"
11. If the question also needs facts from documents, compute only the part the tables can answer."""
        code = clean_code(llm.invoke(code_prompt).content)
        result, error = None, ""
        for attempt in range(3):
            error = validate_code(code) or ""
            if not error:
                try:
                    result = run_code(code, tables)
                    if result is None:
                        error = "The variable `result` was not assigned."
                    else:
                        break
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
            if attempt == 2:
                break
            retry = (f"{code_prompt}\n\nYour previous code:\n{code}\n\n"
                     f"It failed with:\n{error}\n\nReturn corrected raw Python code only.")
            code = clean_code(llm.invoke(retry).content)
        ok = result is not None and not error
        frame = to_frame(result) if ok else None
        return {
            "tables": table_keys,
            "code": code,
            "error": "" if ok else error,
            "text": result_text(result) if ok else "",
            "frame": frame.head(500) if frame is not None else None,
        }
    # ====================================================================
    # NODE: supervisor (decides which tools to use)
    # ====================================================================
    def supervisor(state: AgentState):
        question = state["question"]
        if store.is_empty():
            return {"standalone": question, "route": "none", "pdf_files": [], "table_keys": []}
        prompt = f"""You are the routing supervisor of a document-and-data assistant.
Answers must come ONLY from the uploaded files listed below.

FILES:
{store.catalog()}

CONVERSATION SO FAR:
{state.get("history", "(none)")}

NEW QUESTION: {question}

Decide which files are NEEDED to answer the question. First work out the subject of the
question (reasoning), then match that subject against each file.

ROUTING RULES:
1. A table is relevant only if its columns and sample rows match what the question asks about
   (records, counts, salaries, averages, filters, rankings, groupings, comparisons of rows).
2. A PDF is relevant only if its title/topic matches what the question asks about
   (explanations, descriptions, specifications, policies, facts stated in a document).
3. Questions about rows, columns, employees, sales, scores or any other tabular data must NOT
   select a PDF unless the PDF's topic is clearly about the same subject.
4. Select BOTH a PDF and a table only when the question explicitly needs facts from a document
   AND data from a table, or clearly refers to both.
5. Being unsure is NOT a reason to select extra files. Choose the smallest set that can answer.
6. If no uploaded file relates to the subject of the question, select nothing.
7. If the question is only a greeting or small talk, select nothing.
8. Use exact file names / table keys from the FILES list above.

OUTPUT FIELDS:
- reasoning: one or two sentences naming the subject and the matching files.
- standalone_question: rewrite the question so it is understandable without the conversation
  (resolve "it", "that file", "them"). Keep every name, number and filter exactly. If it is
  already standalone, copy it unchanged.
- pdf_files: exact names of the PDFs needed (empty list if none).
- table_keys: exact keys of the tables needed (empty list if none). Include every relevant
  table when the question compares or combines files."""
        plan = None
        for _ in range(2):
            try:
                plan = router_llm.invoke(prompt)
                break
            except Exception:
                plan = None
        if plan is None:  # router failed twice: fall back to every file
            standalone, reason = question, "Router unavailable; searched all files."
            pdfs, tables = list(store.pdfs), list(store.tables)
        else:
            standalone = plan.standalone_question.strip() or question
            reason = plan.reasoning.strip()
            pdfs = _match(plan.pdf_files, store.pdfs)
            tables = _match(plan.table_keys, store.tables)
        route = "both" if pdfs and tables else "pdf" if pdfs else "data" if tables else "none"
        return {"standalone": standalone, "route": route, "route_reason": reason,
                "pdf_files": pdfs, "table_keys": tables}
    # ====================================================================
    # NODE: pdf_search  -> calls rag_pdf_tool
    # ====================================================================
    def pdf_search(state: AgentState):
        evidence = rag_pdf_tool.invoke({
            "question": state["standalone"],
            "files": state["pdf_files"],
        })
        return {"pdf_evidence": evidence}
    # ====================================================================
    # NODE: data_query  -> calls query_generator_tool
    # ====================================================================
    def data_query(state: AgentState):
        item = query_generator_tool.invoke({
            "question": state["standalone"],
            "table_keys": state["table_keys"],
        })
        return {"data_results": [item]}
    # ====================================================================
    # NODE: synthesize (final grounded answer)
    # ====================================================================
    def synthesize(state: AgentState):
        question = state["standalone"]
        parts = []
        for e in state.get("pdf_evidence", []):
            parts.append(f"[PDF EXCERPT | {e['file']} | p.{e['page']}]\n{e['text']}")
        for d in state.get("data_results", []):
            if d["error"]:
                parts.append(f"[TABLE QUERY FAILED | tables: {d['tables']}]\nError: {d['error']}")
            else:
                parts.append(f"[TABLE RESULT | tables: {d['tables']}]\n"
                             f"Code:\n{d['code']}\nOutput:\n{d['text']}")
        evidence = "\n\n".join(parts) or "(no evidence retrieved)"
        files = ", ".join(list(store.pdfs) + list(store.tables)) or "(none)"
        prompt = f"""You answer questions strictly from the evidence below, which comes from the user's
uploaded files.

Uploaded files: {files}

RULES:
1. Use only the evidence. Do not use outside knowledge, even if you know the answer.
2. If the evidence does not contain the answer, say exactly which part is not found in the
   uploaded files. Never guess or estimate.
3. Copy numbers, names, currency symbols and units exactly as they appear. Never recompute or
   round table results.
4. Cite document facts as (file, p.N). For table results mention the table used.
5. If documents and tables are both relevant, combine them and say which fact came from where.
   If they conflict, point out the conflict.
6. If a table query failed, say so and show the error instead of guessing.
7. Lead with the answer and keep it concise. Use a small markdown table for multi-row results.
8. If the user message is only a greeting or small talk, reply briefly and say they can ask
   about the uploaded files. If no files are uploaded, ask them to upload some.

QUESTION: {question}

EVIDENCE:
{evidence}

ANSWER:"""
        return {"answer": llm.invoke(prompt).content.strip()}
    # ====================================================================
    # GRAPH
    # ====================================================================
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor)
    g.add_node("pdf_search", pdf_search)
    g.add_node("data_query", data_query)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        lambda s: s["route"],
        {"pdf": "pdf_search", "both": "pdf_search", "data": "data_query", "none": "synthesize"},
    )
    g.add_conditional_edges(
        "pdf_search",
        lambda s: "data_query" if s["route"] == "both" else "synthesize",
        {"data_query": "data_query", "synthesize": "synthesize"},
    )
    g.add_edge("data_query", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()