"""Pull course content from Moodle, embed it, upsert to Qdrant.

Phase 1 upgrades:
- Heading-aware semantic chunking (`chunker.semantic_chunks`).
- Embedding cache short-circuits unchanged chunks (`embedding_cache.EmbeddingCache`).
- Batch embeddings via `OllamaClient.embed_batch`.
- Lineage payload (run_id, source_hash, embed_model, chunker_version).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from qdrant_client.http import models as qm

from .chunker import semantic_chunks
from .embedding_cache import EmbeddingCache
from .lineage import lineage_payload, new_ingest_run_id, source_hash
from .moodle import MoodleClient
from .ollama import OllamaClient
from .text import html_to_text
from .vectorstore import VectorStore

log = logging.getLogger(__name__)


@dataclass
class _RawDoc:
    course_id: int
    section_id: int | None
    section_name: str
    module_id: int | None
    module_name: str
    modtype: str
    title: str
    text: str
    url: str | None


def _stable_point_id(
    course_id: int,
    modtype: str,
    module_id: int | None,
    title: str,
    chunk_no: int,
) -> int:
    import hashlib

    raw = f"{course_id}|{modtype}|{module_id}|{title}|{chunk_no}"
    return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:15], 16)


def _collect_course_docs(moodle: MoodleClient, course_id: int) -> list[_RawDoc]:
    """Pull the course tree and any Page bodies; emit one ``_RawDoc`` per item.

    ``core_course_get_contents`` returns Page bodies as a virtual ``index.html``
    file (the inline ``content`` field is empty), so we additionally call
    ``mod_page_get_pages_by_courses`` and merge the bodies in by ``cmid``.
    Without this, courses whose primary content lives in Page activities
    (including every synthetic course we publish) would ingest as empty.
    """
    docs: list[_RawDoc] = []
    contents = moodle.course_contents(course_id)

    page_bodies: dict[int, str] = {}
    try:
        page_resp = moodle.pages([course_id])
        for pg in (page_resp or {}).get("pages") or []:
            cmid = int(pg.get("coursemodule") or 0)
            body = pg.get("content") or ""
            if cmid and body:
                page_bodies[cmid] = body
    except Exception as exc:
        log.warning("Skipping page bodies for course %s: %s", course_id, exc)

    for section in contents:
        section_text = html_to_text(section.get("summary"))
        if section_text:
            docs.append(
                _RawDoc(
                    course_id=course_id,
                    section_id=section.get("id"),
                    section_name=section.get("name", ""),
                    module_id=None,
                    module_name="",
                    modtype="section",
                    title=f"Section: {section.get('name', '')}",
                    text=section_text,
                    url=None,
                )
            )
        for module in section.get("modules", []):
            modtype = module.get("modname", "")
            mod_text_parts: list[str] = []
            desc = html_to_text(module.get("description"))
            if desc:
                mod_text_parts.append(desc)
            for content in module.get("contents", []):
                ctext = html_to_text(content.get("content"))
                if ctext:
                    mod_text_parts.append(ctext)
            # Page resources need their body fetched separately (see header note).
            if modtype == "page":
                cmid = int(module.get("id") or 0)
                body_html = page_bodies.get(cmid)
                if body_html:
                    body_text = html_to_text(body_html)
                    if body_text:
                        mod_text_parts.append(body_text)
            text = "\n\n".join(p for p in mod_text_parts if p).strip()
            if not text:
                continue
            docs.append(
                _RawDoc(
                    course_id=course_id,
                    section_id=section.get("id"),
                    section_name=section.get("name", ""),
                    module_id=module.get("id"),
                    module_name=module.get("name", ""),
                    modtype=modtype,
                    title=module.get("name", "") or modtype,
                    text=text,
                    url=module.get("url"),
                )
            )

    try:
        assigns = moodle.assignments([course_id])
        for course in assigns.get("courses", []):
            for asn in course.get("assignments", []):
                intro = html_to_text(asn.get("intro"))
                if not intro:
                    continue
                docs.append(
                    _RawDoc(
                        course_id=course_id,
                        section_id=None,
                        section_name="",
                        module_id=asn.get("cmid"),
                        module_name=asn.get("name", ""),
                        modtype="assign",
                        title=f"Assignment: {asn.get('name', '')}",
                        text=intro,
                        url=None,
                    )
                )
    except Exception as exc:
        log.warning("Skipping assignments for course %s: %s", course_id, exc)

    return docs


def _expand_doc_to_chunks(doc: _RawDoc, *, max_chars: int, overlap_sentences: int) -> list[tuple[str, str]]:
    heading = doc.title or doc.section_name or doc.modtype or "section"
    chunks = semantic_chunks(
        doc.text,
        max_chars=max_chars,
        overlap_sentences=overlap_sentences,
        default_heading=heading,
    )
    return [(c.heading_path or heading, c.text) for c in chunks]


def ingest_raw_docs(
    course_id: int,
    docs: list[_RawDoc],
    ollama: OllamaClient,
    store: VectorStore,
    *,
    replace: bool = True,
    max_chars: int = 1200,
    overlap_sentences: int = 1,
    use_cache: bool = True,
    payload_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Embed + upsert arbitrary course-shaped documents (Moodle ingest or synthetic).

    ``payload_extra`` is merged into every Qdrant point payload (e.g.
    ``provenance``, ``synth_topic``).
    """
    run_id = new_ingest_run_id()
    if not docs:
        return {"course_id": course_id, "documents": 0, "chunks": 0, "vectors": 0, "ingest_run_id": run_id}

    doc_chunks: list[tuple[_RawDoc, int, str, str]] = []
    for doc in docs:
        for i, (heading, chunk_text) in enumerate(
            _expand_doc_to_chunks(doc, max_chars=max_chars, overlap_sentences=overlap_sentences)
        ):
            doc_chunks.append((doc, i, heading, chunk_text))

    if not doc_chunks:
        return {"course_id": course_id, "documents": len(docs), "chunks": 0, "vectors": 0, "ingest_run_id": run_id}

    cache = EmbeddingCache(_settings_from_store(store))
    embed_model = ollama.embed_model

    cached_vectors: list[list[float] | None] = []
    miss_indices: list[int] = []
    miss_texts: list[str] = []
    for idx, (_doc, _i, _h, ctext) in enumerate(doc_chunks):
        sh = source_hash(ctext)
        cv = cache.get(sh, embed_model) if use_cache else None
        cached_vectors.append(cv)
        if cv is None:
            miss_indices.append(idx)
            miss_texts.append(ctext)

    new_vectors = ollama.embed_batch(miss_texts) if miss_texts else []
    for j, vec in enumerate(new_vectors):
        cached_vectors[miss_indices[j]] = vec

    sample_vec = next((v for v in cached_vectors if v is not None), None)
    if sample_vec is None:
        return {"course_id": course_id, "documents": len(docs), "chunks": len(doc_chunks), "vectors": 0, "ingest_run_id": run_id}
    cache.ensure(vector_size=len(sample_vec))
    for j, vec in enumerate(new_vectors):
        sh = source_hash(miss_texts[j])
        cache.put(sh, embed_model, vec)

    store.ensure_collection(vector_size=len(sample_vec))
    if replace and store.collection_exists():
        store.delete_course(course_id)

    points: list[qm.PointStruct] = []
    for (doc, chunk_no, heading, ctext), vec in zip(doc_chunks, cached_vectors):
        if vec is None:
            continue
        payload: dict[str, Any] = {
            "course_id": doc.course_id,
            "section_id": doc.section_id,
            "section_name": doc.section_name,
            "module_id": doc.module_id,
            "module_name": doc.module_name,
            "modtype": doc.modtype,
            "title": doc.title,
            "heading_path": heading,
            "url": doc.url,
            "chunk_no": chunk_no,
            "text": ctext,
            **lineage_payload(ingest_run_id=run_id, chunk_text=ctext, embed_model=embed_model),
        }
        if payload_extra:
            payload.update(payload_extra)
        points.append(
            qm.PointStruct(
                id=_stable_point_id(doc.course_id, doc.modtype, doc.module_id, doc.title, chunk_no),
                vector=vec,
                payload=payload,
            )
        )

    store.upsert(points)
    return {
        "course_id": course_id,
        "documents": len(docs),
        "chunks": len(doc_chunks),
        "vectors": len(points),
        "embeddings_from_cache": len(doc_chunks) - len(miss_texts),
        "embeddings_new": len(miss_texts),
        "embed_model": embed_model,
        "ingest_run_id": run_id,
    }


def ingest_course(
    course_id: int,
    moodle: MoodleClient,
    ollama: OllamaClient,
    store: VectorStore,
    *,
    replace: bool = True,
    max_chars: int = 1200,
    overlap_sentences: int = 1,
    use_cache: bool = True,
) -> dict[str, Any]:
    docs = _collect_course_docs(moodle, course_id)
    return ingest_raw_docs(
        course_id,
        docs,
        ollama,
        store,
        replace=replace,
        max_chars=max_chars,
        overlap_sentences=overlap_sentences,
        use_cache=use_cache,
    )


def ingest_module(
    course_id: int,
    module_id: int,
    moodle: MoodleClient,
    ollama: OllamaClient,
    store: VectorStore,
) -> dict[str, Any]:
    """Re-ingest a single module (used by the events webhook)."""
    docs = [
        d
        for d in _collect_course_docs(moodle, course_id)
        if d.module_id == module_id or d.modtype == "section"
    ]
    docs = [d for d in docs if d.module_id == module_id]
    if not docs:
        return {"course_id": course_id, "module_id": module_id, "documents": 0, "vectors": 0}
    store.delete_module(course_id, module_id)
    sub_id = next(iter([d.course_id for d in docs]))
    fake_full = list(docs)
    return _ingest_doc_subset(sub_id, fake_full, ollama, store)


def _ingest_doc_subset(
    course_id: int,
    docs: Iterable[_RawDoc],
    ollama: OllamaClient,
    store: VectorStore,
) -> dict[str, Any]:
    run_id = new_ingest_run_id()
    docs_list = list(docs)
    chunks: list[tuple[_RawDoc, int, str, str]] = []
    for doc in docs_list:
        for i, (heading, chunk_text) in enumerate(_expand_doc_to_chunks(doc, max_chars=1200, overlap_sentences=1)):
            chunks.append((doc, i, heading, chunk_text))
    if not chunks:
        return {"course_id": course_id, "documents": len(docs_list), "vectors": 0, "ingest_run_id": run_id}
    vecs = ollama.embed_batch([c[3] for c in chunks])
    store.ensure_collection(vector_size=len(vecs[0]))
    embed_model = ollama.embed_model
    points = [
        qm.PointStruct(
            id=_stable_point_id(d.course_id, d.modtype, d.module_id, d.title, i),
            vector=v,
            payload={
                "course_id": d.course_id,
                "section_id": d.section_id,
                "section_name": d.section_name,
                "module_id": d.module_id,
                "module_name": d.module_name,
                "modtype": d.modtype,
                "title": d.title,
                "heading_path": h,
                "url": d.url,
                "chunk_no": i,
                "text": t,
                **lineage_payload(ingest_run_id=run_id, chunk_text=t, embed_model=embed_model),
            },
        )
        for (d, i, h, t), v in zip(chunks, vecs)
    ]
    store.upsert(points)
    return {"course_id": course_id, "documents": len(docs_list), "vectors": len(points), "ingest_run_id": run_id}


def _settings_from_store(store: VectorStore):
    """Reconstruct a Settings-like with just the qdrant_url for the cache.

    Avoids a hard dependency on importing config to keep this module thin.
    """
    from .config import Settings

    s = Settings()  # re-reads .env / env, same as elsewhere
    return s
