import pytest
from local_ai.calculator import calculate


@pytest.mark.parametrize('expression,value',[
    ('4.6 - 4.3','0.3'),
    ('4.3 < 4.6 - 0.3',False),
    ('4.6 - 4.3 == 0.3',True),
    ('4.5 > 4 and 4.6 - 4.3 >= 0.3',True),
    ('4.0 > 4',False),
    ('-2 * (3 + 4)','-14'),
])
def test_numeric_boundaries(expression,value):
    assert calculate(expression)['value'] == value


@pytest.mark.parametrize('expression',[
    '__import__("os").system("echo unsafe")', 'open(".env")', '2 ** 100000', '1 / 0', '1e999999',
])
def test_calculator_rejects_code_and_unbounded_values(expression):
    with pytest.raises(ValueError): calculate(expression)
