"""Simplest possible manual check: ingest 2 documents -> pause so you can look
in the database yourself -> ask 1 question -> see the right document cited.

Run from the project root:
    uv run python -m scripts.manual_verify_with_pause

Needs Postgres/ParadeDB up:  docker compose up -d db

DESTRUCTIVE: starts from an empty schema, so any documents already in this
database are removed.
"""

from app.retrieval import HybridRerankRetriever, embedding_dim
from app.storage import get_chunks, get_connection, ingest_document, reset_schema

# Two documents, clearly different topics, each with enough sections to produce
# several chunks. The IT one is a plausible distractor for the HR question.
DOCUMENTS = {
    "hr-handbook.md": b"""# HR Handbook

## Vacation

Full-time employees accrue twenty-five days of paid vacation per calendar
year. Vacation requests must be approved by a line manager at least two
weeks in advance. Up to five unused days may be carried into the next year.

## Sick Leave

Employees are entitled to ten paid sick days per year, available from the
first day of employment. A doctor's note is required for absences longer
than three consecutive working days.

## Parental Leave

Primary caregivers receive sixteen weeks of paid parental leave. Secondary
caregivers receive four weeks, which may be taken in two separate blocks
within the first year.

## Expenses

Business travel is reimbursed at the standard national mileage rate.
Receipts must be submitted within thirty days of the expense being incurred.
""",
    "it-security-policy.md": b"""# IT Security Policy

## Passwords

Passwords must be at least twelve characters long, contain mixed case and a
number, and be rotated every ninety days. Password reuse across systems is
prohibited.

## Remote Access

All remote connections must use the corporate VPN client. Multi-factor
authentication is mandatory for every remote session, without exception.

## Device Encryption

Laptops and mobile devices must have full-disk encryption enabled before
any company data is stored on them. The IT helpdesk verifies this at setup.

## Incident Reporting

Suspected security incidents must be reported to the security team within
one hour of discovery, by phone as well as by email.
""",
}

QUESTION = "How many days of paid holiday do full-time staff get each year?"
EXPECTED_FILE = "hr-handbook.md"

SEP = "-" * 72
PSQL = "docker compose exec db psql -U doctalk -d doctalk"


def main() -> None:
    # --- 1. Clean slate -----------------------------------------------------
    reset_schema()
    print(f"\nEmpty schema created (vector width {embedding_dim()}).")

    # --- 2. Ingest both documents ------------------------------------------
    print(f"\n{SEP}\nSTEP 1 - INGEST\n{SEP}")
    for filename, content in DOCUMENTS.items():
        result = ingest_document(filename, content)
        print(f"\n{filename}: {result.chunk_count} chunks stored")
        with get_connection() as conn:
            for chunk in get_chunks(conn, result.document_id):
                section = " > ".join(chunk["section_path"] or ["(no section)"])
                print(f"   {chunk['id']:28s} {section:34s} {len(chunk['text']):4d} chars")

    # --- 3. Pause for manual database inspection ---------------------------
    print(f"\n{SEP}\nSTEP 2 - CHECK THE DATABASE YOURSELF\n{SEP}")
    print(f"""
Open a second terminal and run:

  {PSQL}

Then paste these four queries (each should look as noted):

  -- 2 rows: hr-handbook.md and it-security-policy.md
  SELECT id, filename, file_type, char_count FROM documents;

  -- one row per chunk, section_path filled in, text readable
  SELECT id, section_path, length(text) AS chars, left(text, 50) AS preview
  FROM chunks ORDER BY id;

  -- every chunk has a real vector, all the same width as the embedding model
  -- reports above (384 for the default all-MiniLM-L6-v2), none NULL/empty
  SELECT id, vector_dims(embedding) AS dims, left(embedding::text, 40) AS vector_head
  FROM chunks ORDER BY id;

  -- chunk counts per document
  SELECT document_id, count(*) FROM chunks GROUP BY 1;

  \\q     -- to quit psql
""")
    input("Press Enter here when you are done looking at the database... ")

    # --- 4. One semantic query ---------------------------------------------
    print(f"\n{SEP}\nSTEP 3 - ASK ONE QUESTION\n{SEP}")
    print(f"\nQuestion: {QUESTION!r}")
    print(
        "Note: the wording deliberately shares no keywords with the answer "
        '("paid holiday" vs "paid vacation"), so a keyword search alone would miss it.\n'
    )

    with get_connection() as conn:
        results = HybridRerankRetriever().retrieve(conn, QUESTION, top_k=3)

    for rank, r in enumerate(results, start=1):
        section = " > ".join(r.section_path or ["(no section)"])
        print(f"  #{rank}  score {r.rerank_score:+7.3f}   CITATION: [{r.chunk_id}]")
        print(f"      source:  {r.filename} > {section}")
        print(f"      text:    {' '.join(r.text.split())[:150]}...\n")

    # --- 5. Verdict ---------------------------------------------------------
    top = results[0]
    print(SEP)
    if top.filename == EXPECTED_FILE and "twenty-five" in top.text:
        print(f"PASS - top hit is the Vacation section of {EXPECTED_FILE}, cited as")
        print(f"       [{top.chunk_id}], and it contains the answer: twenty-five days.")
    else:
        print(f"FAIL - expected the Vacation section of {EXPECTED_FILE} on top, got")
        print(f"       [{top.chunk_id}] from {top.filename}.")
    print(SEP + "\n")


if __name__ == "__main__":
    main()
