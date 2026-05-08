def iata_from_destination(destination: str) -> str:
    d = (destination or "").strip()
    if "_" in d:
        return d.rsplit("_", 1)[-1]
    return d
