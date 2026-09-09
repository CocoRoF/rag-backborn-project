from __future__ import annotations

from ragb.models._types import EMBED_DIM
from ragb.providers.embedding import HashEmbedding, pad


def test_pad_normalises_every_provider_width():
    assert len(pad([0.1] * 768)) == EMBED_DIM
    assert len(pad([0.1] * 3072)) == EMBED_DIM
    assert pad([0.1] * 768)[1000] == 0.0


async def test_hash_embedding_is_deterministic_and_unit_length():
    e = HashEmbedding()
    a, b = await e.embed(["보증 기간"]), await e.embed(["보증 기간"])
    assert a == b
    assert abs(sum(x * x for x in a[0]) ** 0.5 - 1.0) < 1e-6
