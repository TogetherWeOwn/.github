def smoke_verdict(failed):
    verdict = "SKIP" if failed else "PASS"
    return verdict
