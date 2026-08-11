from reconciliation_agent.rag import embeddings


class EncodedVectors:
    def tolist(self) -> list[list[float]]:
        return [[1.0, 0.0], [0.0, 1.0]]


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    def encode(self, texts: list[str], *, normalize_embeddings: bool):
        self.calls.append((texts, normalize_embeddings))
        return EncodedVectors()


def test_embedding_service_requests_normalized_vectors(monkeypatch) -> None:
    model = FakeModel()
    monkeypatch.setattr(embeddings, "_load_model", lambda _name: model)

    result = embeddings.EmbeddingService("demo-model").embed(["a", "b"])

    assert result == [[1.0, 0.0], [0.0, 1.0]]
    assert model.calls == [(["a", "b"], True)]
