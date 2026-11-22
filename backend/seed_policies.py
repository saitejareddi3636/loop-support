"""Run after migrate.py and seed_data.py. Unchanged embeddings are reused."""
from app.policy import seed
if __name__=='__main__': print(seed())
