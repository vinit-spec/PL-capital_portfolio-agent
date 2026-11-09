from agent.evaluator import _float_eq, _row_eq, _sets_match

def test_float_equality():
    assert _float_eq(10.0, 10.05, rtol=0.01)  # within 1%
    assert not _float_eq(10.0, 11.0, rtol=0.01) # outside 1%
    assert _float_eq("Tech", "tech")
    assert _float_eq(0.0, 0.0)

def test_row_equality():
    r1 = ("AAPL", 185.50, 100)
    r2 = ("AAPL", 185.52, 100)
    assert _row_eq(r1, r2)

    r3 = ("MSFT", 185.50, 100)
    assert not _row_eq(r1, r3)

def test_sets_match():
    s1 = [("A", 1), ("B", 2)]
    s2 = [("B", 2), ("A", 1)]
    assert _sets_match(s1, s2)

    s3 = [("A", 1), ("B", 3)]
    assert not _sets_match(s1, s3)

