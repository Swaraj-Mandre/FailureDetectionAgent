"""RAG over our own error-handling guides (corpus/).

Each "## " section of the guide is one chunk. Chunks are embedded with
nomic-embed-text and kept in Chroma on disk, so the index is built once and
reused. Answers come ONLY from retrieved sections, and the prompt tells the
model to say it doesn't know when the guide has nothing.
"""
import hashlib
import threading

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import ChatOllama, OllamaEmbeddings

from config import CHAT_MODEL, CHROMA_DIR, CORPUS_FILE, EMBED_MODEL, MODEL_RETRIES, RAG_K

# one collection per guide: error_guide.md (sample shop) and any_shop_guide.md (uploaded shops)

GUIDE_PROMPT = """Answer using ONLY the guide sections below.
If only part of the question is covered, answer that part and say which part the guide does not cover.
If none of it is covered, reply exactly: I don't know, the guide does not cover that.
Answer in plain text, no formulas or markdown.

Guide sections:
{context}

Question: {question}
Answer:"""

ADVICE_PROMPT = """A SQL query on the {shop} database failed.
Using ONLY the guide sections below, write one or two sentences telling the query writer
exactly what to do next. If the query itself is wrong, name the table, column or function to
change. Do not write the whole query.
If the guide does not cover this error, reply exactly: I don't know.

Guide sections:
{context}
{tables}
Failed SQL: {sql}
Error: {error}
Advice:"""

_vectorstores = {}
_building = threading.Lock()
_llm = None


def load_chunks(guide=CORPUS_FILE):
    """Split the guide on '## ' headings. The heading stays inside the chunk text."""
    text = guide.read_text(encoding="utf-8")
    chunks = []
    for part in text.split("\n## ")[1:]:
        title = part.splitlines()[0].strip()
        chunks.append(Document(page_content="## " + part.strip(), metadata={"section": title}))
    return chunks


def corpus_hash(guide=CORPUS_FILE):
    return hashlib.sha1(guide.read_bytes()).hexdigest()


def get_vectorstore(rebuild=False, guide=CORPUS_FILE):
    """Open the saved index, and rebuild it only when the guide changed (or on request)."""
    with _building:
        return _get_vectorstore(rebuild, guide)


def _get_vectorstore(rebuild, guide):
    if guide in _vectorstores and not rebuild:
        return _vectorstores[guide]

    hash_file = CHROMA_DIR / ("corpus.sha1" if guide == CORPUS_FILE else f"{guide.stem}.sha1")
    store = Chroma(collection_name=guide.stem, embedding_function=OllamaEmbeddings(model=EMBED_MODEL),
                   persist_directory=str(CHROMA_DIR))
    saved_hash = hash_file.read_text().strip() if hash_file.exists() else ""
    if rebuild or saved_hash != corpus_hash(guide):
        store.reset_collection()
        chunks = load_chunks(guide)
        store.add_documents(chunks, ids=[f"section-{i + 1}" for i in range(len(chunks))])
        hash_file.write_text(corpus_hash(guide))
    _vectorstores[guide] = store
    return store


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatOllama(model=CHAT_MODEL, temperature=0).with_retry(stop_after_attempt=MODEL_RETRIES)
    return _llm


def retrieve(query, k=RAG_K, guide=CORPUS_FILE):
    return get_vectorstore(guide=guide).similarity_search(query, k=k)


def join_chunks(chunks):
    return "\n\n".join(c.page_content for c in chunks)


def ask_guide(question, k=RAG_K):
    """Plain grounded question answering over the guide. Returns (answer, sections used)."""
    chunks = retrieve(question, k)
    answer = get_llm().invoke(GUIDE_PROMPT.format(context=join_chunks(chunks), question=question)).content
    return answer.strip(), [c.metadata["section"] for c in chunks]


def advise(error, sql="", k=RAG_K, guide=CORPUS_FILE, shop="Sahyadri Stores", tables=""):
    """Specific correction advice for one failed query. Returns (advice, sections used).
    An uploaded shop also passes its table list, because its guide does not name any columns."""
    chunks = retrieve(error, k, guide)
    prompt = ADVICE_PROMPT.format(context=join_chunks(chunks), sql=sql or "(not available)", error=error, shop=shop,
                                  tables=f"\nTables:\n{tables}\n" if tables else "")
    return get_llm().invoke(prompt).content.strip(), [c.metadata["section"] for c in chunks]


if __name__ == "__main__":
    store = get_vectorstore(rebuild=True)
    print(f"Indexed {len(load_chunks())} sections from {CORPUS_FILE.name} into {CHROMA_DIR}")
