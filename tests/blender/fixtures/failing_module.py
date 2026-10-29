def test_ok():
    assert True


def test_broken():
    raise AssertionError("deliberate failure")
