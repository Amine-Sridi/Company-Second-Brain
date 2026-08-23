-- Company Second Brain: extensions (Step 3)
--
-- pgvector lives only in second_brain — the only schema that stores embeddings. No other
-- schema needs it and no other role is granted access to it.

CREATE EXTENSION IF NOT EXISTS vector SCHEMA second_brain;
