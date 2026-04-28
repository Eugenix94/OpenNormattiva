with open('space/app.py', 'rb') as f:
    content = f.read()

old = (
    b'def main():\r\n'
    b'    pages = {\r\n'
    b'        "\xf0\x9f\x93\x8a Dashboard": page_dashboard,\r\n'
    b'        "\xf0\x9f\x87\xae\xf0\x9f\x87\xb9 Costituzione & Codici": page_costituzione,\r\n'
    b'        "\xf0\x9f\x94\x8d Search": page_search,\r\n'
    b'        "\xf0\x9f\x93\x8b Browse": page_browse,\r\n'
    b'        "\xf0\x9f\x93\x96 Law Detail": page_law_detail,\r\n'
    b'        "\xf0\x9f\x94\x97 Citations": page_citations,\r\n'
    b'        "\xf0\x9f\x8f\x9b\xef\xb8\x8f Domains": page_domains,\r\n'
    b'        "\xef\xbf\xbd Legge di Bilancio": page_bilancio,\r\n'
    b'        "\xef\xbf\xbd\xf0\x9f\x94\x94 Notifications": page_notifications,\r\n'
    b'        "\xf0\x9f\x93\x9d Update Log": page_update_log,\r\n'
    b'        "\xf0\x9f\x93\xa5 Export": page_export,\r\n'
    b'    }'
)

new = (
    b'def main():\r\n'
    b'    pages = {\r\n'
    b'        "\xf0\x9f\x93\x8a Dashboard": page_dashboard,\r\n'
    b'        "\xf0\x9f\x87\xae\xf0\x9f\x87\xb9 Costituzione & Codici": page_costituzione,\r\n'
    b'        "\xe2\x9a\x96\xef\xb8\x8f Corte Costituzionale": page_corte_cost,\r\n'
    b'        "\xf0\x9f\x94\x8d Search": page_search,\r\n'
    b'        "\xf0\x9f\x93\x8b Browse": page_browse,\r\n'
    b'        "\xf0\x9f\x93\x96 Law Detail": page_law_detail,\r\n'
    b'        "\xf0\x9f\x94\x97 Citations": page_citations,\r\n'
    b'        "\xf0\x9f\x8f\x9b\xef\xb8\x8f Domains": page_domains,\r\n'
    b'        "\xf0\x9f\x92\xb0 Legge di Bilancio": page_bilancio,\r\n'
    b'        "\xf0\x9f\x94\x94 Notifications": page_notifications,\r\n'
    b'        "\xf0\x9f\x93\x9d Update Log": page_update_log,\r\n'
    b'        "\xf0\x9f\x93\xa5 Export": page_export,\r\n'
    b'    }'
)

if old in content:
    content = content.replace(old, new, 1)
    with open('space/app.py', 'wb') as f:
        f.write(content)
    print('OK - navigation patched')
else:
    print('MISS - old block not found, checking context...')
    idx = content.find(b'def main()')
    print(repr(content[idx:idx+700]))
