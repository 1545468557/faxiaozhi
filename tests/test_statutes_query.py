import pytest
from app import statutes

@pytest.mark.parametrize('query', ['民法典34', '民法典 34', '民法典第34条', '《民法典》第三十四条', '民法典３４', '民法典,34'])
def test_common_article_input(query):
    assert statutes.parse_query(query) == ('民法典', '第三十四条')


def test_article_suffix_and_keywords():
    assert statutes.parse_query('刑法第133条之一') == ('刑法', '第一百三十三条之一')
    assert statutes.parse_query('押金 退还')[1] == ''
    assert statutes.normalize_article_no('10000') == ''

@pytest.mark.skipif(not statutes.DB_PATH.exists(), reason='local statute database unavailable')
def test_exact_law_does_not_return_commentaries_or_wrong_article():
    result = statutes.search('民法典34')
    assert result['items']
    assert all(i['title'] == '中华人民共和国民法典' and i['no'] == '第三十四条' for i in result['items'])
    assert statutes.search('民法典9999')['items'] == []
