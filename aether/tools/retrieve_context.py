from aether.tools.base import BaseTool
from aether.rag.retriever import HybridRetriever


class RetrieveContextTool(BaseTool):
    """Wraps the already-instantiated HybridRetriever for executor dispatch.

    Must be constructed with a live retriever instance (one that has already
    ingested documents). Do not instantiate HybridRetriever inside this class.
    """

    def __init__(self, retriever: HybridRetriever) -> None:
        self._retriever = retriever

    @property
    def name(self) -> str:
        return "retrieve_context"

    def run(self, args: dict) -> dict:
        """
        Expected args:
            query (str): the retrieval query
            top_k (int, optional): number of chunks to return, default 5

        Returns:
            {
                "chunks": [
                    {"content": str, "source": str, "score": float},
                    ...
                ]
            }
        """
        query = args.get("query")
        if not query:
            return {"error": "retrieve_context requires a 'query' argument",
                    "chunks": []}

        top_k = int(args.get("top_k", 5))
        chunks = self._retriever.retrieve(query=query, top_k=top_k)

        return {
            "chunks": [
                {
                    "content": c.content,
                    "source": c.metadata.source_path,
                    "score": round(c.score, 4) if hasattr(c, "score") else None,
                }
                for c in chunks
            ]
        }
