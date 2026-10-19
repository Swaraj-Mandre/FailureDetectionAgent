"""RAG over our own error-handling guide (corpus/error_guide.md).

Each "## " section of the guide is one chunk. Chunks are embedded with
nomic-embed-text and kept in Chroma on disk, so the index is built once and
reused. Answers come ONLY from retrieved sections, and the prompt tells the
model to say it doesn't know when the guide has nothing.
"""
import hashlib

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import ChatOllama, OllamaEmbeddings

from config import CHAT_MODEL, CHROMA_DIR, CORPUS_FILE, EMBED_MODEL, RAG_K

COLLECTION = "error_guide"
HASH_FILE = CHROMA_DIR / "corpus.sha1"

GUIDE_PROMPT = """Answer using ONLY the guide sections below.
If only part of the question is covered, answer that part and say which part the guide does not cover.
If none of it is covered, reply exactly: I don't know, the guide does not cover that.
Answer in plain text, no formulas or markdown.

Guide sections:
{context}

Question: {question}
Answer:"""

ADVICE_PROMPT = """A SQL query on the Sahyadri Stores database failed.
Using ONLY the guide sections below, write one or two sentences telling the query writer
exactly what to change. Name the specific table, column or function. Do not write the whole query.
If the guide does not cover this error, reply exactly: I don't know.

Guide sections:
{context}

Failed SQL: {sql}
Error: {error}
Advice:"""

_vectorstore = None
_llm = None


def load_chunks():
    """Split the guide on '## ' headings. The heading stays inside the chunk text."""
    text = CORPUS_FILE.read_text(encoding="utf-8")
    chunks = []
    for part in text.split("\n## ")[1:]:
        title = part.splitlines()[0].strip()
        chunks.append(Document(page_content="## " + part.strip(), metadata={"section": title}))
    return chunks


def corpus_hash():
    return hashlib.sha1(CORPUS_FILE.read_bytes()).hexdigest()


def get_vectorstore(rebuild=False):
    """Open the saved index, and rebuild it only when the guide changed (or on request)."""
    global _vectorstore
    if _vectorstore is not None and not rebuild:
        return _vectorstore

    store = Chroma(collection_name=COLLECTION, embedding_function=OllamaEmbeddings(model=EMBED_MODEL),
                   persist_directory=str(CHROMA_DIR))
    saved_hash = HASH_FILE.read_text().strip() if HASH_FILE.exists() else ""
    if rebuild or saved_hash != corpus_hash():
        store.reset_collection()
        chunks = load_chunks()
        store.add_documents(chunks, ids=[f"section-{i + 1}" for i in range(len(chunks))])
        HASH_FILE.write_text(corpus_hash())
    _vectorstore = store
    return store


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatOllama(model=CHAT_MODEL, temperature=0)
    return _llm


def retrieve(query, k=RAG_K):
    return get_vectorstore().similarity_search(query, k=k)


def join_chunks(chunks):
    return "\n\n".join(c.page_content for c in chunks)


def ask_guide(question, k=RAG_K):
    """Plain grounded question answering over the guide. Returns (answer, sections used)."""
    chunks = retrieve(question, k)
    answer = get_llm().invoke(GUIDE_PROMPT.format(context=join_chunks(chunks), question=question)).content
    return answer.strip(), [c.metadata["section"] for c in chunks]


def advise(error, sql="", k=RAG_K):
    """Specific correction advice for one failed query. Returns (advice, sections used)."""
    chunks = retrieve(error, k)
    prompt = ADVICE_PROMPT.format(context=join_chunks(chunks), sql=sql or "(not available)", error=error)
    return get_llm().invoke(prompt).content.strip(), [c.metadata["section"] for c in chunks]


if __name__ == "__main__":
    store = get_vectorstore(rebuild=True)
    print(f"Indexed {len(load_chunks())} sections from {CORPUS_FILE.name} into {CHROMA_DIR}")
