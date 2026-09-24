"""Use Lambda's invocation containment instead of Linux PR_SET_PDEATHSIG.

Lambda denies prctl(PR_SET_PDEATHSIG). The handler owns a fresh process group,
kills that group on its internal watchdog, and leaves time to save a failure.
The Lambda sandbox also owns the group at the service watchdog. Only the local
process-ownership wrapper is removed; the benchmark worker, model, arguments,
random state and geometry observer are unchanged.
"""
from serverless.benchmark import run_batch


def main():
    run_batch.supervised = list
    run_batch.main()


if __name__ == '__main__':
    main()
