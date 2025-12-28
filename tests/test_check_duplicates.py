from unittest import mock
import pytest


def make_fake_conn(columns, duplicates):
    """Return a fake connection object where get_table_columns returns columns and
    find_duplicates_for_column will return duplicates mapping. This will be used by
    patching psycopg2.connect to return an object with a cursor that responds to execute/fetchall.
    """
    class FakeCursor:
        def __init__(self):
            self._last_query = None

        def execute(self, query, params=None):
            self._last_query = (query, params)

        def fetchall(self):
            # If last executed query was the information_schema columns query, return columns
            q, p = self._last_query
            if isinstance(q, str) and 'information_schema.columns' in q:
                return columns
            # Otherwise, assume it's the duplicates aggregation and return based on query params
            # For our testing, return the provided duplicates (list of tuples)
            return duplicates

        def fetchone(self):
            # For checking 'id' column existence we can return 1
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    return FakeConn()


def test_generate_report_happy_path(monkeypatch):
    # Prepare fake table columns and duplicates
    columns = [('id', 'bigint'), ('text1', 'text'), ('text2', 'text')]
    # duplicates should be returned as list of (val, ids, cnt)
    duplicates = [('example', [1, 2], 2)]

    fake_conn = make_fake_conn(columns, duplicates)

    # Patch psycopg2.connect to return the fake connection
    with mock.patch('src.cannonical_data_pipeline.deduplication.check_duplicates.psycopg2.connect', return_value=fake_conn):
        from src.cannonical_data_pipeline.deduplication.check_duplicates import generate_duplicates_report
        report = generate_duplicates_report()

    assert report['table'] == 'poc'
    assert 'text1' in report['columns']
    assert isinstance(report['columns']['text1'], list)
    # If only one duplicate group, count should be 1
    assert len(report['columns']['text1']) == 1
    entry = report['columns']['text1'][0]
    assert entry['value'] == 'example'
    assert entry['count'] == 2


def test_generate_report_connection_error(monkeypatch):
    # Simulate psycopg2.connect raising an OperationalError
    class FakeError(Exception):
        pass

    def fake_connect(*args, **kwargs):
        raise Exception('connection failed')

    with mock.patch('src.cannonical_data_pipeline.deduplication.check_duplicates.psycopg2.connect', fake_connect):
        from src.cannonical_data_pipeline.deduplication.check_duplicates import generate_duplicates_report
        report = generate_duplicates_report()

    assert report['error'] is not None
    assert 'Failed to connect' in report['error'] or 'Error while checking duplicates' in report['error']
