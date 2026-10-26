from engine.workloads import catalog


def resolve_operations(operations=None, profile="standard"):
    cases = catalog(profile)["cases"]
    known = list(dict.fromkeys(c["operation"] for c in cases))
    if operations is None:
        return known, cases
    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) > len(known)
        or any(not isinstance(op, str) or op not in known for op in operations)
        or len(set(operations)) != len(operations)
    ):
        raise ValueError("Choose known operation identifiers without duplicates")
    selected = set(operations)
    return [op for op in known if op in selected], [c for c in cases if c["operation"] in selected]
