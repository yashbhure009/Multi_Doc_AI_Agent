import streamlit as st
from agent import build_graph, format_history
from store import FileStore
st.set_page_config(page_title="Multi-File AI Assistant", layout="wide")
st.title("Multi-File AI Assistant")
st.caption("Upload PDFs, CSV and Excel files. The agent picks the right files and answers only from them.")
if "store" not in st.session_state:
    st.session_state.store = FileStore()
    st.session_state.messages = []
    st.session_state.graph = None
store: FileStore = st.session_state.store
with st.sidebar:
    st.header("Your files")
    uploaded = st.file_uploader(
        "Upload PDF, CSV or Excel files",
        type=["pdf", "csv", "xlsx", "xlsm", "xls"],
        accept_multiple_files=True,
    )
    current = {f.name for f in uploaded}
    for name in store.file_names() - current:
        store.remove_file(name)
    if uploaded:
        with st.spinner("Indexing files..."):
            for f in uploaded:
                try:
                    store.add_file(f.name, f.getvalue())
                except ValueError as e:
                    st.error(f"{f.name}: {e}")
    if store.pdfs:
        st.subheader("PDFs")
        for p in store.pdfs.values():
            note = f" ({p.empty_pages} blank/scanned pages skipped)" if p.empty_pages else ""
            st.write(f"- {p.name}: {p.pages} pages{note}")
    if store.tables:
        st.subheader("Tables")
        for t in store.tables.values():
            st.write(f"- {t.key}: {len(t.df)} rows x {len(t.df.columns)} cols")
    if store.is_empty():
        st.info("No files loaded yet.")
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()
def render_trace(trace: dict):
    if not trace:
        return
    with st.expander("How this was answered"):
        st.write(f"**Route:** {trace['route']}")
        if trace.get("pdf_files"):
            st.write("**PDFs searched:** " + ", ".join(trace["pdf_files"]))
        if trace.get("table_keys"):
            st.write("**Tables used:** " + ", ".join(trace["table_keys"]))
        if trace.get("code"):
            st.markdown("**Generated pandas code**")
            st.code(trace["code"], language="python")
        if trace.get("error"):
            st.error(trace["error"])
        if trace.get("frame") is not None:
            st.dataframe(trace["frame"])
        for s in trace.get("sources", []):
            st.caption(f"{s['file']} - page {s['page']}")
            st.text(s["text"][:400])
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        render_trace(m.get("trace"))
if question := st.chat_input("Ask about your uploaded files..."):
    if store.is_empty():
        st.warning("Upload at least one file first.")
        st.stop()
    history = format_history(st.session_state.messages)
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            if st.session_state.graph is None:
                st.session_state.graph = build_graph(store)
            with st.spinner("Thinking..."):
                out = st.session_state.graph.invoke({"question": question, "history": history})
        except Exception as e:
            st.error(f"{type(e).__name__}: {e}")
            st.stop()
        answer = out.get("answer", "No answer produced.")
        data = (out.get("data_results") or [{}])[0]
        trace = {
            "route": out.get("route", "none"),
            "pdf_files": out.get("pdf_files", []),
            "table_keys": out.get("table_keys", []),
            "code": data.get("code"),
            "error": data.get("error"),
            "frame": data.get("frame"),
            "sources": out.get("pdf_evidence", []),
        }
        st.markdown(answer)
        render_trace(trace)
    st.session_state.messages.append({"role": "assistant", "content": answer, "trace": trace})