"""Remove orphaned sandbox containers (e.g. if the backend crashed mid-run).
Call on app startup (lifespan)."""
def reap_orphans() -> int:
    import docker

    client = docker.from_env()
    n = 0
    for c in client.containers.list(all=True, filters={"label": "decyra.sandbox=1"}):
        c.remove(force=True)
        n += 1
    return n
