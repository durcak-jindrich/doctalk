from app.retrieval.retriever import _rrf_fuse


def test_rrf_fuse_prioritizes_items_ranked_high_in_both_legs():
    dense = ["a", "b", "c"]
    lexical = ["b", "a", "d"]
    fused = _rrf_fuse([dense, lexical])
    assert fused[0] in ("a", "b")
    assert set(fused) == {"a", "b", "c", "d"}


def test_rrf_fuse_handles_disjoint_legs():
    fused = _rrf_fuse([["a", "b"], ["c", "d"]])
    assert set(fused) == {"a", "b", "c", "d"}


def test_rrf_fuse_handles_empty_input():
    assert _rrf_fuse([[], []]) == []
    assert _rrf_fuse([["a"], []]) == ["a"]


def test_rrf_fuse_single_leg_preserves_order():
    assert _rrf_fuse([["a", "b", "c"]]) == ["a", "b", "c"]
