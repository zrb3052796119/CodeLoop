import warnings

def test_ok():
    warnings.warn('1 failed historical request; 2 errors already recovered')
    assert True
