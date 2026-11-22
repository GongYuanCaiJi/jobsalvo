# semgrep --test 的範例:ruleid 那一行要被抓到,ok 那一行不能被抓到


def example(page, browser, seen, door, tab, url, text, json, request_page):
    # ruleid: page-content-field-outside-core
    page._seen_parts

    # ruleid: page-content-field-outside-core
    getattr(page, "_seen_parts")

    # ok: page-content-field-outside-core
    seen.read_workspace(browser)

    # ruleid: page-content-raw-read-outside-core
    browser.raw_tabs()

    # ok: page-content-raw-read-outside-core
    seen.read_workspace(browser)

    # ok: page-content-raw-read-outside-core
    sheet, end = json.JSONDecoder().raw_decode(text)

    # ruleid: page-read-outside-boundary
    door.read_page(tab)

    # ruleid: page-read-outside-boundary
    request_page(url)

    # ruleid: page-read-outside-boundary
    from web.fetch import request_page  # noqa: F401

    # ruleid: page-read-outside-boundary
    import page_fetch  # noqa: F401

    # ok: page-read-outside-boundary
    seen.fetch(browser, url)
