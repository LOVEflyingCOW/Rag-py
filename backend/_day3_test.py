# -*- coding: utf-8 -*-
"""Day 3 Test: Document Upload -> Chunking -> Embedding -> Vector Search
Runs with: python _day3_test.py
"""
import os
import sys
import io
import time
import shutil
import tempfile

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

results = []

def log(msg):
    try:
        print(msg)
    except Exception:
        # fallback: replace non-ascii
        print(msg.encode("ascii", errors="replace").decode("ascii"))
    sys.stdout.flush()

def check(desc, condition):
    results.append((desc, condition))
    tag = "[OK]" if condition else "[FAIL]"
    log("  %s %s" % (tag, desc))

log("=" * 60)
log("Day 3 - Document Pipeline & Semantic Search Test")
log("=" * 60)

# ----- 1. Import Test -----
log("\n[1] Imports")
try:
    from app.core.config import settings
    check("config module", True)
except Exception as e:
    check("config module: %s" % str(e)[:50], False)

try:
    from app.processors.document.document_processor import DocumentProcessor
    check("DocumentProcessor", True)
except Exception as e:
    check("DocumentProcessor: %s" % str(e)[:50], False)

try:
    from app.processors.embedding.embedding_service import MockEmbeddingProvider
    check("EmbeddingService (mock)", True)
except Exception as e:
    check("EmbeddingService: %s" % str(e)[:50], False)

try:
    from app.processors.retrieval.vector_store import (
        VectorStoreManager, PurePythonVectorStore, _HAS_FAISS
    )
    check("VectorStoreManager", True)
    check("FAISS available (optional)", _HAS_FAISS)
except Exception as e:
    check("VectorStore: %s" % str(e)[:50], False)

try:
    from app.services.document_service import DocumentService
    check("DocumentService", True)
except Exception as e:
    check("DocumentService: %s" % str(e)[:50], False)

try:
    from fastapi.testclient import TestClient
    from app.main import app
    check("FastAPI TestClient", True)
except Exception as e:
    check("FastAPI TestClient: %s" % str(e)[:50], False)

# ----- 2. Document Processor Test -----
log("\n[2] Document Processor")
try:
    processor = DocumentProcessor(chunk_size=200, chunk_overlap=20)
    check("init DocumentProcessor", True)

    check("detect .txt -> text", processor.detect_file_type("x.txt") == "text")
    check("detect .md -> markdown", processor.detect_file_type("x.md") == "markdown")

    sample_text = (
        "RAG (Retrieval-Augmented Generation) is a technique combining "
        "information retrieval with large language model generation.\n"
        "\n"
        "Core idea: Before answering, retrieve relevant content from an "
        "external knowledge base, then submit the retrieved context plus "
        "the question to the LLM.\n"
        "\n"
        "Key benefits: (1) Answer domain-specific questions, "
        "(2) Reduce hallucinations, (3) Provide source citations, "
        "(4) Lower cost vs fine-tuning."
    )
    chunks = processor.split_chunks(sample_text, chunk_size=150, chunk_overlap=20)
    check("split_chunks returns results", len(chunks) > 0)
    if chunks:
        check("first chunk has content", len(chunks[0].get("content", "")) > 5)
        check("first chunk has char_count", chunks[0].get("char_count", 0) > 0)
        log("    produced %d chunks" % len(chunks))
        for i, c in enumerate(chunks[:2]):
            snippet = c["content"][:60].replace("\n", " ")
            log("    chunk %d: %s" % (i, snippet))
except Exception as e:
    import traceback
    log("  [EXCEPTION] %s" % str(e)[:100])
    log("  %s" % traceback.format_exc()[:300])

# ----- 3. Embedding Test -----
log("\n[3] Embedding Service")
try:
    provider = MockEmbeddingProvider(dim=32)
    check("MockEmbeddingProvider init dim=32", True)

    texts = [
        "RAG is retrieval-augmented generation combining search and LLMs.",
        "Vector databases store high-dimensional embeddings for similarity search.",
        "LLMs generate natural language text, for example GPT and Llama."
    ]
    vectors = provider.encode(texts)
    check("encode() produces %d vectors" % len(texts), len(vectors) == len(texts))
    check("vectors have dim 32", all(len(v) == 32 for v in vectors))

    a = provider.encode_single("Hello world")
    b = provider.encode_single("Hello world")
    check("same input -> same vector", a == b)

    v = provider.encode_single("test embedding normalization")
    magnitude = sum(x * x for x in v)
    check("magnitude roughly positive (%0.2f)" % magnitude, magnitude > 0.1)
except Exception as e:
    import traceback
    log("  [EXCEPTION] %s" % str(e)[:100])
    log("  %s" % traceback.format_exc()[:300])

# ----- 4. Vector Store & Search Test -----
log("\n[4] Vector Store and Similarity Search")
try:
    tmp_dir = tempfile.mkdtemp(prefix="rag_vec_")
    manager = VectorStoreManager(base_dir=tmp_dir, default_dim=32)

    test_texts = [
        "RAG is retrieval augmented generation combining info retrieval with LLM.",
        "Vector database stores high dimensional vectors for similarity search.",
        "FAISS is Facebook open source vector search library supporting many indices.",
        "Large language models generate natural language text such as GPT or Llama.",
        "Document chunking splits long text into smaller pieces for embedding.",
        "Python is an interpreted object oriented high level programming language.",
        "FastAPI is a modern high performance web framework for building APIs.",
        "SQLAlchemy is a SQL toolkit and object relational mapper for Python.",
        "File upload is a common web application feature using multipart encoding.",
        "Authentication and access control are important for API security."
    ]

    prov2 = MockEmbeddingProvider(dim=32)
    vectors2 = prov2.encode(test_texts)
    metas = [{"chunk_id": i + 1, "text": test_texts[i]} for i in range(len(test_texts))]

    store = manager.get_store(knowledge_base_id=9999, dim=32)
    indices = store.add(vectors2, metadata=metas)
    check("add vectors -> %d items stored" % len(indices), len(indices) == len(test_texts))
    check("store total == %d" % len(test_texts), store.total() == len(test_texts))

    query_vec = prov2.encode_single("How does RAG work with retrieval and LLM?")
    hits = store.search(query_vec, top_k=3)
    check("search returns results", len(hits) > 0)
    log("    top-%d search results:" % len(hits))
    for vec_idx, score in hits[:3]:
        meta = store.get_metadata(vec_idx) or {}
        snippet = meta.get("text", "unknown")[:60]
        log("    score=%0.4f: %s" % (score, snippet))

    manager.save(9999)
    saved_path = os.path.join(tmp_dir, "kb_9999.vecstore")
    check("persisted to disk", os.path.isfile(saved_path))

    del manager
    manager2 = VectorStoreManager(base_dir=tmp_dir, default_dim=32)
    store2 = manager2.get_store(knowledge_base_id=9999, dim=32)
    check("reloaded from disk -> %d items" % len(test_texts), store2.total() == len(test_texts))

    shutil.rmtree(tmp_dir, ignore_errors=True)
except Exception as e:
    import traceback
    log("  [EXCEPTION] %s" % str(e)[:100])
    log("  %s" % traceback.format_exc()[:300])

# ----- 5. API End-to-End Test -----
log("\n[5] End-to-End API Test")
try:
    # reset data dir
    data_dir = os.path.join(BACKEND_DIR, "data")
    for sub in ["vector_stores", "uploads"]:
        p = os.path.join(data_dir, sub)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
    db_path = os.path.join(data_dir, "rag_system.db")
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass

    from app.models.database import init_db
    init_db()
    check("database initialized", True)

    # reload main module to get fresh app with new db session
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app)

    # 5a. register user
    r = client.post("/v1/auth/register", json={
        "username": "day3user",
        "password": "TestPass1234",
        "confirm_password": "TestPass1234",
        "email": "day3@example.com",
    })
    check("register user (status=%d)" % r.status_code, r.status_code == 200)
    token = ""
    if r.status_code == 200:
        token = r.json()["data"]["access_token"]

    # 5b. create knowledge base
    r = client.post("/v1/knowledge-bases", json={
        "name": "Day3 Test KB",
        "description": "Testing document pipeline and search.",
        "chunk_size": 200,
        "chunk_overlap": 30,
        "is_public": False,
    }, headers={"Authorization": "Bearer " + token})
    check("create knowledge base (status=%d)" % r.status_code, r.status_code == 200)
    kb_id = 0
    if r.status_code == 200:
        kb_id = r.json()["data"]["id"]
        log("    KB id: %d" % kb_id)

    # 5c. upload document #1
    doc1_content = (
        "RAG System Core Principle\n"
        "\n"
        "RAG stands for Retrieval-Augmented Generation. It is a technique that "
        "combines information retrieval systems with large language models.\n"
        "\n"
        "Step 1 - Document Processing: After a document is uploaded, the system "
        "parses and extracts plain text from it.\n"
        "\n"
        "Step 2 - Chunking: Long text is split into smaller segments called chunks. "
        "Each chunk has a limited character size with a small overlap between them.\n"
        "\n"
        "Step 3 - Embedding: An embedding model converts each chunk into a "
        "high-dimensional numerical vector.\n"
        "\n"
        "Step 4 - Vector Index: Vectors are stored in a vector database and "
        "indexed for fast nearest-neighbor search.\n"
        "\n"
        "Step 5 - Retrieval and Generation: When a user asks a question, the "
        "question is first embedded. The system searches the vector database for "
        "the most similar chunks. The retrieved context plus the original question "
        "are submitted to the LLM which produces the final answer.\n"
    )
    f1 = io.BytesIO(doc1_content.encode("utf-8"))
    r = client.post(
        "/v1/knowledge-bases/%d/documents" % kb_id,
        files={"file": ("rag_guide.txt", f1, "text/plain")},
        data={"chunk_size": "200", "chunk_overlap": "30"},
        headers={"Authorization": "Bearer " + token},
    )
    check("upload document #1 (status=%d)" % r.status_code, r.status_code == 200)
    doc1_id = 0
    if r.status_code == 200:
        payload = r.json()
        doc1_id = payload["data"]["document"]["id"]
        doc1_status = payload["data"]["document"]["status"]
        doc1_chunks = payload["data"]["document"]["total_chunks"]
        log("    doc id=%d status=%s chunks=%d" % (doc1_id, doc1_status, doc1_chunks))

    # 5d. upload document #2
    doc2_content = (
        "LLM Security Best Practices\n"
        "\n"
        "1. Data Privacy: Uploaded documents may contain sensitive information. "
        "Always sanitize and de-identify data before uploading.\n"
        "\n"
        "2. Hallucination Mitigation: LLMs can produce plausible but incorrect "
        "content. RAG reduces this risk by grounding answers in retrieved context.\n"
        "\n"
        "3. Access Control: Different users should have different permissions on "
        "each knowledge base. Use bearer tokens to authenticate API calls.\n"
        "\n"
        "4. Content Safety: Filter inappropriate output and follow applicable "
        "content safety regulations.\n"
    )
    f2 = io.BytesIO(doc2_content.encode("utf-8"))
    r = client.post(
        "/v1/knowledge-bases/%d/documents" % kb_id,
        files={"file": ("llm_security.txt", f2, "text/plain")},
        headers={"Authorization": "Bearer " + token},
    )
    check("upload document #2 (status=%d)" % r.status_code, r.status_code == 200)

    # 5e. list documents
    r = client.get(
        "/v1/knowledge-bases/%d/documents" % kb_id,
        headers={"Authorization": "Bearer " + token},
    )
    check("list documents (status=%d)" % r.status_code, r.status_code == 200)
    if r.status_code == 200:
        payload = r.json()
        check("list returns 2 documents", payload["data"]["total"] == 2)

    # 5f. get document detail
    if doc1_id:
        r = client.get(
            "/v1/knowledge-bases/%d/documents/%d" % (kb_id, doc1_id),
            headers={"Authorization": "Bearer " + token},
        )
        check("get document detail (status=%d)" % r.status_code, r.status_code == 200)

    # 5g. semantic search
    t0 = time.time()
    r = client.post(
        "/v1/knowledge-bases/%d/documents/search?query=%s&top_k=5"
        % (kb_id, "What are the five steps of the RAG pipeline?"),
        headers={"Authorization": "Bearer " + token},
    )
    elapsed_ms = (time.time() - t0) * 1000
    check("semantic search #1 (status=%d, elapsed=%.1fms)" % (r.status_code, elapsed_ms),
          r.status_code == 200)
    if r.status_code == 200:
        payload = r.json()
        hits_data = payload["data"]
        log("    returned %d hits (search_time=%.1fms)"
            % (hits_data["total"], hits_data["search_time_ms"]))
        for item in hits_data["results"][:3]:
            snippet = item["content"][:60].replace("\n", " ")
            log("    score=%0.4f file=%s: %s"
                % (item["score"], item["document_filename"], snippet))

    # 5h. second search query (about security)
    r = client.post(
        "/v1/knowledge-bases/%d/documents/search?query=%s&top_k=5"
        % (kb_id, "How to protect uploaded documents and control access?"),
        headers={"Authorization": "Bearer " + token},
    )
    check("semantic search #2 (status=%d)" % r.status_code, r.status_code == 200)
    if r.status_code == 200:
        payload = r.json()
        log("    returned %d hits about security" % payload["data"]["total"])

    # 5i. unauthenticated upload must fail
    r = client.post(
        "/v1/knowledge-bases/%d/documents" % kb_id,
        files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    check("unauthenticated upload rejected (status=%d)" % r.status_code,
          r.status_code in (401, 403))

    # 5j. delete document
    if doc1_id:
        r = client.delete(
            "/v1/knowledge-bases/%d/documents/%d" % (kb_id, doc1_id),
            headers={"Authorization": "Bearer " + token},
        )
        check("delete document (status=%d)" % r.status_code, r.status_code == 200)

    # cleanup
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass
except Exception as e:
    import traceback
    log("  [EXCEPTION] %s" % str(e)[:100])
    log("  %s" % traceback.format_exc()[:500])

# ----- Summary -----
log("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
total = len(results)
log("Result: %d / %d checks passed" % (passed, total))
log("=" * 60)
log("")
log("Day 3 deliverables:")
log("  - DocumentProcessor (text extraction + smart chunking)")
log("  - EmbeddingService (Mock provider, pluggable)")
log("  - VectorStoreManager (FAISS with pure-python fallback, persistence)")
log("  - DocumentService (upload pipeline, list, detail, delete, search)")
log("  - API: POST /v1/knowledge-bases/{id}/documents")
log("         POST /v1/knowledge-bases/{id}/documents/search")
log("         GET  /v1/knowledge-bases/{id}/documents")
log("         DEL  /v1/knowledge-bases/{id}/documents/{doc_id}")
log("")
log("Usage:")
log("  cd %s" % BACKEND_DIR)
log("  python start.py          -> start server")
log("  http://127.0.0.1:8000/docs -> interactive API docs")