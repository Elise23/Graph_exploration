import os
from dotenv import load_dotenv
from langchain_community.graphs import Neo4jGraph
from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_openai import ChatOpenAI

load_dotenv()


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
    return ChatOpenAI(model="gpt-4o")  # reads OPENAI_API_KEY from .env automatically


def extract_graph_documents(
    llm,
    documents,
    ignore_tool_usage=False,
    allowed_nodes=None,
    allowed_relationships=None,
    node_properties=False,
    relationship_properties=False,
):
    transformer_kwargs = {
        "llm": llm,
        "ignore_tool_usage": ignore_tool_usage,
        "node_properties": node_properties,
        "relationship_properties": relationship_properties,
    }

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


def main():
    # text = """
    # Marie Curie, 7 November 1867 – 4 July 1934, was a Polish and naturalised-French physicist and chemist who conducted pioneering research on radioactivity.
    # She was the first woman to win a Nobel Prize, the first person to win a Nobel Prize twice, and the only person to win a Nobel Prize in two scientific fields.
    # Her husband, Pierre Curie, was a co-winner of her first Nobel Prize, making them the first-ever married couple to win the Nobel Prize and launching the Curie family legacy of five Nobel Prizes.
    # She was, in 1906, the first woman to become a professor at the University of Paris.
    # """ #pdf_001
    # pdf_id = "pdf_001"

    text = """
    Robin Williams Curie, 7 November 1867 – 4 July 1934, was a Polish and naturalised-French physicist and chemist who conducted pioneering research on radioactivity.
    She was the first woman to win a Nobel Prize, the first person to win a Nobel Prize twice, and the only person to win a Nobel Prize in two scientific fields.
    Her husband, Pierre Curie, was a co-winner of her first Nobel Prize, making them the first-ever married couple to win the Nobel Prize and launching the Curie family legacy of five Nobel Prizes.
    She was, in 1906, the first woman to become a professor at the University of Paris.
    """ #pdf_002
    pdf_id = "pdf_002"

    graph = connect_graph()
    documents = load_documents(text)
    llm = build_llm()
    allowed_nodes = ["Person"]
    graph_documents = extract_graph_documents(
        llm, documents, allowed_nodes=allowed_nodes
    )

    graph_documents = tag_graph_documents_with_import_pdf_id(graph_documents, pdf_id)
    print(graph_documents)

    # clean_graph(graph)
    ingest_to_graph(graph, graph_documents)
    upsert_pdf_ids_from_graph_documents(graph, graph_documents, pdf_id)


if __name__ == "__main__":
    main()
