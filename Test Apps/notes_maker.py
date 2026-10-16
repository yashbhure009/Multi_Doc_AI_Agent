from fpdf import FPDF
from langchain_groq import ChatGroq
import streamlit as st
import re
llm = ChatGroq(model="openai/gpt-oss-120b", temperature=1, api_key="API KEY")
st.title("AI Powered Notes Maker Via API")


year_input = st.text_input("Enter the year:")
branch_input = st.text_input("Enter the branch:")
subject_input = st.text_input("Enter the subject:")
topics_input = st.text_area("Enter the topics (separated by commas):")
prompt = f"""
You are an expert academic notes generator for engineering students.
Create exam-ready notes using these details:

Year: {year_input}
Branch: {branch_input}
Subject: {subject_input}
Topics: {topics_input}

STRICT REQUIREMENTS:
1. EXPLANATION: For EACH topic, give a clear explanation (definition, key concepts,
   step-by-step logic where relevant). Use ## for each topic and ### for sub-sections.
2. TABLE: Include at least one Markdown table per topic (comparison, formulas,
   or key-terms-vs-meaning).
3. SOURCES: After each topic, add a "Sources" list with real reference books,
   papers, or reputable websites (author + title or site name).
4. IMAGES: For every topic add 1-2 Markdown image links to relevant diagrams,
   e.g. ![Diagram](https://upload.wikimedia.org/...). Use Wikimedia Commons or
   Wikipedia URLs only; if unsure of the exact file URL, use the concept's
   Wikipedia page URL and label it clearly.
5. End with a "Quick Revision Summary" section with the most important points.

Be accurate, structured, and student-friendly. Output only Markdown.
"""
def generate_pdf(notes_text):
    def clean(t):
        for a, b in {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                     "\u2014": "-", "\u2212": "-", "\u2018": "'", "\u2019": "'",
                     "\u201c": '"', "\u201d": '"', "\u2022": "*", "\u2192": "->",
                     "\u2264": "<=", "\u2265": ">=", "\u00a0": " ", "\u202f": " "}.items():
            t = t.replace(a, b)
        return t.encode("latin-1", "ignore").decode("latin-1")
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    lines = clean(notes_text).replace("**", "").replace("`", "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        pdf.set_x(pdf.l_margin)
        if line.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.fullmatch(r"[\s|:\-]+", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            pdf.set_font("helvetica", "", 9)
            with pdf.table() as table:
                for r in rows:
                    row = table.row()
                    for c in r:
                        row.cell(c)
            pdf.ln(3)
            continue
        if line.startswith("### "):
            pdf.set_font("helvetica", "B", 11); line = line[4:]
        elif line.startswith("## "):
            pdf.set_font("helvetica", "B", 13); line = line[3:]
        elif line.startswith("# "):
            pdf.set_font("helvetica", "B", 16); line = line[2:]
        else:
            pdf.set_font("helvetica", "", 10)
        pdf.multi_cell(0, 6, line)
        i += 1
    return bytes(pdf.output())
if st.button("Generate Notes"):
  if not (year_input and branch_input and subject_input and topics_input):
    st.error("Please fill in ALL fields before generating.")
  else:
    with st.spinner("Generating notes... please wait..."):
      try:
        response = llm.invoke(prompt)
        notes = response.content
      except Exception as e:
        notes = None
        st.error(f"Error: {e}")
    if notes:
      st.success("Notes generated!")
      st.divider()
      st.markdown(notes)
      images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", notes)
      if images:
        st.divider()
        st.subheader("Reference Images")
        cols = st.columns(3)
        for i, url in enumerate(images[:6]):
          try:
            cols[i % 3].image(url, use_container_width=True)
          except Exception:
            st.caption(f"Could not load: {url}")
      st.divider()
      col1, col2 = st.columns(2)
      with col1:
        st.download_button(
            "Download Notes (.md)",
            data=notes,
            file_name=f"{subject_input.replace(' ', '_')}_notes.md",
            mime="text/markdown",
        )
      with col2:
        pdf_data = generate_pdf(notes)
        st.download_button(
            "Download Notes (.pdf)",
            data=pdf_data,
            file_name=f"{subject_input.replace(' ', '_')}_notes.pdf",
            mime="application/pdf",
        )
st.caption("Built with Streamlit + LangChain + Groq")
st.caption("By ~Yash Bhure")
