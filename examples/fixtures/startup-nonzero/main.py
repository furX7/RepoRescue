import sys

print("RepoRescue startup failure fixture", file=sys.stderr)
raise RuntimeError("intentional fixture failure")
