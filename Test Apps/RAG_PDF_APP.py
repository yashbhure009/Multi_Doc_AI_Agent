import os
import chromadb
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
load_dotenv()
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY"),
)
pdf_path = "BMW_Z4_Complete_Guide_India.pdf"
reader = PdfReader(pdf_path)
all_text = "\n".join(page.extract_text() or "" for page in reader.pages)
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
)
chunks = text_splitter.create_documents([all_text])
print(f"Total document fragments created: {len(chunks)}")
client = chromadb.Client()
vectordb = client.get_or_create_collection(
    name="BMW_Z4_Complete_Guide_India",
    metadata={"hnsw:space": "cosine"},
)
vectordb.add(
    documents=[chunk.page_content for chunk in chunks],
    ids=[f"c{i}" for i in range(len(chunks))],
)
print(f"Successfully added {len(chunks)} chunks to the vector collection!")
user_question = "What is the Ex Showroom Price of BMW Z4 in Pune?"
search_results = vectordb.query(query_texts=[user_question], n_results=1)
retrieved_context = search_results["documents"][0][0]
prompt = f"""You are a document-QA assistant for an automotive guide.
Use the CONTEXT below as the preferred source of truth. If it does not contain
the answer, answer from your general knowledge and prefix that answer with
[GENERAL KNOWLEDGE]. Never fabricate exact numbers, prices, dates, or specs.
Output only the answer.

CONTEXT:
\"\"\"
{retrieved_context}
\"\"\"

QUESTION: {user_question}
ANSWER:"""
response = llm.invoke(prompt)
print(response.content)