from datetime import date

from etl.extract.downloader import register_local_archive


def test_local_archive_replay_registers_and_reuses_the_archive_hash(pg_conn, sample_zip_path):
    fecha = date(2026, 3, 17)

    first = register_local_archive(pg_conn, sample_zip_path, fecha)
    second = register_local_archive(pg_conn, sample_zip_path, fecha)

    assert first.is_new is True
    assert second.is_new is False
    assert first.raw_file_id == second.raw_file_id
    assert first.path == sample_zip_path.resolve()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT url, size_bytes FROM raw_files WHERE id = %s", (first.raw_file_id,))
        url, size_bytes = cur.fetchone()
    assert url == sample_zip_path.resolve().as_uri()
    assert size_bytes == sample_zip_path.stat().st_size
