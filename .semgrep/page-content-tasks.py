# semgrep --test 的範例:ruleid 那一行要被抓到,ok 那一行不能被抓到


def example(change, said, name, cell):
    # ruleid: page-text-key-in-task
    change["after_text"]

    # ruleid: page-text-key-in-task
    {"quote": said}

    # ok: page-text-key-in-task
    {"item": name}

    # ruleid: getattr-in-task
    getattr(cell, "said", "")

    # ok: getattr-in-task
    cell.said
