import ast
import json
import os
from pathlib import Path
import re
import pypdfium2 as pdfium
from dotenv import load_dotenv
from langchain_community.graphs import Neo4jGraph
from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_openai import ChatOpenAI

load_dotenv()

CACHE_PATH = Path("raw_data/relationship_types_cache.json")
RELATION_TOKEN_PATTERN = re.compile(r"[A-Z]+(?:_[A-Z]+)*")


def connect_graph():
    return Neo4jGraph(
        url=os.getenv("NEO4J_URL"),
        username=os.getenv("NEO4J_USERNAME"),
        password=os.getenv("NEO4J_PASSWORD"),
        database=os.getenv("NEO4J_DATABASE"),
        refresh_schema=False,
    )


def load_documents(text):
    return [Document(page_content=text)]


def build_llm():
    return ChatOpenAI(model="gpt-4o", temperature=0)  # reads OPENAI_API_KEY from .env automatically


def extract_global_relationship_types(llm, text):
    prompt = f"""
You are an expert in information extraction and knowledge graph construction.

Your task is to analyze the following text and identify the main types of relationships that can exist specifically BETWEEN COUNTRIES.

The goal is NOT to extract individual relations, but to define a SMALL and GENERAL set of RELATION TYPES that can be used to build a consistent knowledge graph of interactions between countries.

Important constraints:
- ONLY consider relationships where BOTH entities are countries
- Ignore any relationships involving organizations, institutions, NGOs, or individuals
- Focus only on meaningful geopolitical or diplomatic interactions between countries

Instructions:
- Group similar actions under the same relation type
- Use high-level, normalized relation names (e.g., SUPPORTS, OPPOSES, NEGOTIATES_WITH)
- Avoid very specific or rare verbs
- Each relation type must be:
  - UPPERCASE
  - concise
  - semantically clear
- Limit the number of relation types to 5–10 maximum
- The relation types should be reusable across multiple documents

Output format:
Return ONLY a Python list of strings, like:
["SUPPORTS", "OPPOSES", "NEGOTIATES_WITH", "CALLS_FOR"]

Text:
\"\"\"
{text}
\"\"\"
"""

    response = llm.invoke(prompt)
    content = response.content.strip()

    # --- Robust parsing ---
    try:
        relations = ast.literal_eval(content)
        if isinstance(relations, list):
            return sanitize_relationship_types(relations)
    except Exception:
        pass

    # fallback simple si le format n'est pas respecté
    lines = content.replace("[", "").replace("]", "").split(",")
    relations = [l.strip().strip('"').strip("'") for l in lines if l.strip()]

    return sanitize_relationship_types(relations)


def sanitize_relationship_types(raw_values):
    """Normalize and filter noisy LLM outputs into clean relation labels."""
    if not isinstance(raw_values, list):
        return []

    clean_relations = []
    seen = set()

    for value in raw_values:
        if not isinstance(value, str):
            value = str(value)

        cleaned = value.upper()
        cleaned = cleaned.replace("```PYTHON", " ")
        cleaned = cleaned.replace("```", " ")

        for token in RELATION_TOKEN_PATTERN.findall(cleaned):
            if token == "PYTHON":
                continue
            if token not in seen:
                seen.add(token)
                clean_relations.append(token)

    return clean_relations


def load_relationship_types_cache():
    if not CACHE_PATH.exists():
        return {}

    try:
        with CACHE_PATH.open("r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (json.JSONDecodeError, OSError):
        return {}

    return cache if isinstance(cache, dict) else {}


def save_relationship_types_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, ensure_ascii=False, indent=2)


def get_or_extract_relationship_types(llm, text, pdf_id):
    cache = load_relationship_types_cache()

    if pdf_id in cache and isinstance(cache[pdf_id], list):
        cached_relationships = sanitize_relationship_types(cache[pdf_id])
        if cached_relationships:
            if cached_relationships != cache[pdf_id]:
                cache[pdf_id] = cached_relationships
                save_relationship_types_cache(cache)
            return cached_relationships

    relationship_types = sanitize_relationship_types(
        extract_global_relationship_types(llm, text)
    )
    cache[pdf_id] = relationship_types
    save_relationship_types_cache(cache)
    return relationship_types

def extract_graph_documents(
    llm,
    documents,
    ignore_tool_usage=None,
    allowed_nodes=None,
    allowed_relationships=None,
    node_properties=None,
    relationship_properties=None,
):
    transformer_kwargs = {"llm": llm}

    if ignore_tool_usage is not None:
        transformer_kwargs["ignore_tool_usage"] = ignore_tool_usage
    if node_properties is not None:
        transformer_kwargs["node_properties"] = node_properties
    if relationship_properties is not None:
        transformer_kwargs["relationship_properties"] = relationship_properties
    if allowed_nodes is not None:
        transformer_kwargs["allowed_nodes"] = allowed_nodes
    if allowed_relationships is not None:
        transformer_kwargs["allowed_relationships"] = allowed_relationships

    transformer = LLMGraphTransformer(
        **transformer_kwargs,
    )
    return transformer.convert_to_graph_documents(documents)


def ingest_to_graph(graph, graph_documents):
    graph.add_graph_documents(graph_documents, include_source=True)


def clean_graph(graph):
    query = """
    MATCH (n)
    DETACH DELETE n
    """
    graph.query(query)


def tag_graph_documents_with_import_pdf_id(graph_documents, pdf_id):
    """Attach pdf_id to source metadata for traceability."""
    for gd in graph_documents:
        if gd.source.metadata is None:
            gd.source.metadata = {}
        gd.source.metadata["id"] = pdf_id

    return graph_documents


def upsert_pdf_ids_from_graph_documents(graph, graph_documents, pdf_id):
    """Add pdf_id to pdf_ids on imported nodes and relationships without duplicates."""
    # Collect unique node IDs (and check in the relationships as well to catch any nodes that might not be in the nodes list but are in relationships)
    node_ids = sorted(
        {
            str(node.id)
            for gd in graph_documents
            for node in gd.nodes
        }
        |
        {
            str(rel.source.id)
            for gd in graph_documents
            for rel in gd.relationships
            if rel.source is not None
        }
        |
        {
            str(rel.target.id)
            for gd in graph_documents
            for rel in gd.relationships
            if rel.target is not None
        }
    )
    rel_rows = [
        {
            "source": str(rel.source.id),
            "target": str(rel.target.id),
            "type": rel.type,
        }
        for gd in graph_documents
        for rel in gd.relationships
    ]

    if node_ids:
        graph.query(
            """
            UNWIND $node_ids AS node_id
            MATCH (n {id: node_id})
            SET n.pdf_ids = CASE
                WHEN n.pdf_ids IS NULL THEN [$pdf_id]
                WHEN $pdf_id IN n.pdf_ids THEN n.pdf_ids
                ELSE n.pdf_ids + $pdf_id
            END
            """,
            {"node_ids": node_ids, "pdf_id": pdf_id},
        )

    if rel_rows:
        graph.query(
            """
            UNWIND $rels AS rel_row
            MATCH (s {id: rel_row.source})-[r]->(t {id: rel_row.target})
            WHERE type(r) = rel_row.type
            SET r.pdf_ids = CASE
                WHEN r.pdf_ids IS NULL THEN [$pdf_id]
                WHEN $pdf_id IN r.pdf_ids THEN r.pdf_ids
                ELSE r.pdf_ids + $pdf_id
            END
            """,
            {"rels": rel_rows, "pdf_id": pdf_id},
        )

def extract_text_from_pdf(path):
    """Extract text from a PDF file and return it as a single string, and a pdf_id based on the filename."""
    text = "\n".join(
        p.get_textpage().get_text_range() 
        for p in pdfium.PdfDocument(path)
    )
    pdf_id = path.split("/")[-1].split(".")[0]  # Extract filename without extension
    return text, pdf_id


def main():
    # text = """
    # Marie Curie, 7 November 1867 – 4 July 1934, was a Polish and naturalised-French physicist and chemist who conducted pioneering research on radioactivity.
    # She was the first woman to win a Nobel Prize, the first person to win a Nobel Prize twice, and the only person to win a Nobel Prize in two scientific fields.
    # Her husband, Pierre Curie, was a co-winner of her first Nobel Prize, making them the first-ever married couple to win the Nobel Prize and launching the Curie family legacy of five Nobel Prizes.
    # She was, in 1906, the first woman to become a professor at the University of Paris.
    # """ #pdf_001
    # pdf_id = "pdf_001"

    # text = """
    # Robin Williams Curie, 7 November 1867 – 4 July 1934, was a Polish and naturalised-French physicist and chemist who conducted pioneering research on radioactivity.
    # She was the first woman to win a Nobel Prize, the first person to win a Nobel Prize twice, and the only person to win a Nobel Prize in two scientific fields.
    # Her husband, Pierre Curie, was a co-winner of her first Nobel Prize, making them the first-ever married couple to win the Nobel Prize and launching the Curie family legacy of five Nobel Prizes.
    # She was, in 1906, the first woman to become a professor at the University of Paris.
    # """ #pdf_002
    # pdf_id = "pdf_002"

    text, pdf_id = extract_text_from_pdf("raw_data/enb12856e.pdf")
    print(f"Extracted text from PDF (id: {pdf_id}):\n{text[:500]}...")  # Print the first 500 characters for verification

    graph = connect_graph()
    documents = load_documents(text)
    llm = build_llm()

    # Step 1: Extract global relationship
    relationship_types = get_or_extract_relationship_types(llm, text, pdf_id)
    print(f"Extracted relationship types for {pdf_id}: {relationship_types}")

    # Step 2: Extract graph documents with constraints
    allowed_nodes = ["Country"]  # Example allowed node types
    allowed_relationships = relationship_types
    # relationship_properties = ["verb"]  # Example to include relationship properties
    graph_documents = extract_graph_documents(
        llm, documents, ignore_tool_usage=False, allowed_nodes=allowed_nodes, allowed_relationships=allowed_relationships,
    )

    graph_documents = tag_graph_documents_with_import_pdf_id(graph_documents, pdf_id)
    print(graph_documents)

    clean_graph(graph)
    ingest_to_graph(graph, graph_documents)
    upsert_pdf_ids_from_graph_documents(graph, graph_documents, pdf_id)


if __name__ == "__main__":
    main()
