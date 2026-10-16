import streamlit as st
from langchain_groq import ChatGroq
import os
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
st.title("Query Generator App")
st.subheader("Lets Generate Queries By ~Yash Bhure")
llm = ChatGroq(model="openai/gpt-oss-120b",temperature=0,api_key=os.getenv("GROQ_API_KEY"))
uploaded_file = st.file_uploader("Upload your database file (CSV or Excel)", type=["csv", "xlsx", "xls"])
if uploaded_file is not None:
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        st.success(f"Successfully loaded: **{uploaded_file.name}**")
    except Exception as e:
        st.error(f"Error reading file: {e}")
        df = None
else:
    if os.path.exists('employee_db.csv'):
        df = pd.read_csv('employee_db.csv')
    else:
        df = None
if df is not None:
    columns = df.columns
    question = st.text_input("Hey There, Ask me the question: ")
    prompt = f"""You are an expert data analyst who writes clean pandas code.
Write a python code block to answer the following question.
Question: {question}
The dataframe variable name is df.
Database columns are: {list(columns)}

CRITICAL REQUIREMENTS:
1. You MUST assign the final computed answer (whether it's a scalar value, a string, or a pandas DataFrame/Series) to a variable named `result`.
2. Output MUST be valid, raw Python code ready for direct execution/eval().
3. DO NOT use markdown code blocks or code fences (NO ``` or ```python).
4. DO NOT include comments, preamble, or conversational text. Return ONLY the code.
""".format(question=question, columns=columns)
    if st.button("Generate Query"):
        if question.strip():
            response = llm.invoke(prompt)
            query_code = response.content.strip()
            if query_code.startswith("```"):
                query_code = query_code.split("```")[1]
                if query_code.startswith("python"):
                    query_code = query_code[6:].strip()
            query_code = query_code.strip("`").strip()
            st.write("Generated Code:")
            st.code(query_code, language="python")
            try:
                local_vars = {"df": df, "pd": pd}
                exec(query_code, {}, local_vars)
                result = local_vars.get("result", "No 'result' variable found.")
                st.write("### Answer:")
                if isinstance(result, pd.DataFrame):
                    st.dataframe(result, use_container_width=True)
                elif isinstance(result, pd.Series):
                    st.dataframe(result.reset_index(), use_container_width=True)
                else:
                    st.success(str(result))
            except Exception as e:
                st.error(f"Error executing generated code: {e}")
        else:
            st.warning("Please enter a question first.")
else:
    st.info("Please upload a CSV or Excel file to get started.")