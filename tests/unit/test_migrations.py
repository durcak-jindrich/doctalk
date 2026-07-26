import re

from app.retrieval import embedding_dim
from app.storage import MIGRATIONS_DIR, migration_files


def test_migration_filenames_are_ordered_and_unique():
    versions = [path.name for path in migration_files()]
    assert versions, "no migrations found"
    prefixes = [name.split("_", 1)[0] for name in versions]
    assert all(prefix.isdigit() for prefix in prefixes), f"unnumbered migration in {versions}"
    assert len(set(prefixes)) == len(prefixes), f"duplicate migration number in {versions}"


def test_vector_width_matches_the_configured_embedding_model():
    """The schema's VECTOR(N) is a literal, so nothing stops EMBEDDING_MODEL from
    drifting away from it at runtime. This is the guard: change the model to one
    of a different width and this fails, pointing at the missing migration."""
    sql = (MIGRATIONS_DIR / "0001_initial_schema.sql").read_text()
    match = re.search(r"embedding VECTOR\((\d+)\)", sql)
    assert match, "could not find the embedding column in 0001_initial_schema.sql"
    assert int(match.group(1)) == embedding_dim()
