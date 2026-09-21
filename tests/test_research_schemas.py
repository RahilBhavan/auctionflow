
import pyarrow.parquet as pq
import pytest

from auctionflow.research import (
    CANDIDATE_SCHEMA,
    RESEARCH_SCHEMA_VERSION,
    CandidateRow,
    rows_to_table,
    write_parquet_atomic,
)


def row(i=1):
    return CandidateRow(candidate_id=f"c{i}", event_id=f"e{i}", session_id="s", strategy="x", direction="long", entry_ticks=100, stop_ticks=98, status="accepted", trading_config_revision="t", strategy_config_revision="s")


@pytest.mark.parametrize("rows", [[], [row()], [row(1), row(2)]])
def test_explicit_schema_stable(rows):
    assert rows_to_table(rows, CANDIDATE_SCHEMA, CandidateRow).schema == CANDIDATE_SCHEMA


def test_metadata_round_trip(tmp_path):
    path = tmp_path / "x.parquet"
    write_parquet_atomic(path, [row()], CANDIDATE_SCHEMA, CandidateRow)
    assert pq.read_table(path).schema.metadata[b"schema_version"].decode() == RESEARCH_SCHEMA_VERSION


def test_validation_precedes_replace(tmp_path):
    path = tmp_path / "x.parquet"
    write_parquet_atomic(path, [row()], CANDIDATE_SCHEMA, CandidateRow)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_parquet_atomic(path, [{"candidate_id": "bad"}], CANDIDATE_SCHEMA, CandidateRow, overwrite=True)
    assert path.read_bytes() == before


def test_overwrite_requires_opt_in(tmp_path):
    path = tmp_path / "x.parquet"
    write_parquet_atomic(path, [row()], CANDIDATE_SCHEMA, CandidateRow)
    with pytest.raises(FileExistsError):
        write_parquet_atomic(path, [row(2)], CANDIDATE_SCHEMA, CandidateRow)
    write_parquet_atomic(path, [row(2)], CANDIDATE_SCHEMA, CandidateRow, overwrite=True)
    assert pq.read_table(path)["candidate_id"].to_pylist() == ["c2"]
