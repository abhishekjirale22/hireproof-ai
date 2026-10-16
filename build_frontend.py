"""Build the Vite app so FastAPI can expose its static assets on Vercel."""

import subprocess
import os

npm = "npm.cmd" if os.name == "nt" else "npm"

subprocess.run([npm, "ci", "--prefix", "frontend"], check=True)
subprocess.run([npm, "run", "build", "--prefix", "frontend"], check=True)
