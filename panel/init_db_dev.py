import os
import sys

# Add the current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import engine
from app.models import Base

print("Initializing development SQLite database...")
Base.metadata.create_all(engine)
print("Initialization complete!")
