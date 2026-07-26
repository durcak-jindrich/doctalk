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


def test_agreement_across_legs_outweighs_one_leg_ranking_something_first():
    """The property `k` exists to produce, asserted through behaviour.

    A large `k` damps the gap between adjacent ranks, so a chunk both legs
    found agreeing at rank 4 outranks one that only the dense leg liked at
    rank 1. Shrink `k` toward 0 and the single first place wins instead —
    which is the failure this pins down, since the constant is otherwise
    a magic number no test would notice changing.
    """
    dense = ["dense-favourite", "x", "y", "agreed"]
    lexical = ["p", "q", "r", "agreed"]

    assert _rrf_fuse([dense, lexical])[0] == "agreed"
