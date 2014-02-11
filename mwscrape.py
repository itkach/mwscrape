from __future__  import print_function
import urlparse
import mwclient
import couchdb
import argparse


def fix_server_url(general_siteinfo):
    """
    Get server url from siteinfo's 'general' dict,
    add http if scheme is missing. This will also modify
    given dictionary.

    >>> general_siteinfo = {'server': '//simple.wikipedia.org'}
    >>> fix_server_url(general_siteinfo)
    'http://simple.wikipedia.org'
    >>> general_siteinfo
    {'server': 'http://simple.wikipedia.org'}

    >>> fix_server_url({'server': 'https://en.wikipedia.org'})
    'https://en.wikipedia.org'

    >>> fix_server_url({})
    ''

    """
    server = general_siteinfo.get('server', '')
    if server:
        p = urlparse.urlparse(server)
        if not p.scheme:
            server = urlparse.urlunparse(
                urlparse.ParseResult('http', p.netloc, p.path,
                                     p.params, p.query, p.fragment))
            general_siteinfo['server'] = server
    return server


def upate_siteinfo(site, couch_server, db_name):
    try:
        siteinfo_db = couch_server.create('siteinfo')
    except couchdb.PreconditionFailed:
        siteinfo_db = couch_server['siteinfo']

    siteinfo = site.api('query', meta='siteinfo',
                        siprop='general|interwikimap|rightsinfo')['query']

    fix_server_url(siteinfo['general'])

    siteinfo.pop('userinfo', None)

    siteinfo_doc = siteinfo_db.get(db_name)

    if siteinfo_doc:
        siteinfo_doc.update(siteinfo)
    else:
        siteinfo_doc = siteinfo

    siteinfo_db[db_name] = siteinfo_doc


def parse_args():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('site',
                           help=('MediaWiki site to scrape (host name), '
                                 'e.g. en.m.wikipeia.org'))
    argparser.add_argument('-c', '--couch',
                           help=('CouchDB server URL. '
                                 'Default: %(default)s'),
                           default='http://localhost:5984')
    argparser.add_argument('--db',
                           help=('CouchDB database name. '
                                 'If not specified, the name will be '
                                 'derived from Mediawiki host name.'),
                           default=None)
    argparser.add_argument('--titles', nargs='+',
                           help=('Download article pages with '
                                 'these names (titles).'))
    argparser.add_argument('--start',
                           help=('Download all article pages '
                                 'starting with this name'))
    argparser.add_argument('-S', '--siteinfo-only', action='store_true',
                           help=('Fetch or update siteinfo, then exit'))

    return argparser.parse_args()


def main():

    args = parse_args()

    db_name = args.db or args.site.replace('.', '-')

    site = mwclient.Site(args.site)
    couch_server = couchdb.Server(args.couch)

    upate_siteinfo(site, couch_server, db_name)

    if args.siteinfo_only:
        return

    try:
        db = couch_server.create(db_name)
    except couchdb.PreconditionFailed:
        db = couch_server[db_name]

    if args.titles:
        pages = (site.Pages[title.decode('utf8')] for title in args.titles)
    else:
        pages = site.allpages(start=args.start)

    for index, page in enumerate(pages):
        title = page.name
        print('%7s %s' % (index, title))
        if not page.exists:
            print('Not found: %r' % title)
            continue
        try:
            aliases = set()
            while page.redirect:
                aliases.add(title)
                page = page.redirects_to()
                print('%s ==> %s' % (title, page.name))
                if page.name in aliases:
                    print('Redirect cycle: %r' % aliases)
                    break
                title = page.name
            if page.redirect:
                print('Failed to resolve redirect %s', title)
                continue
            doc = db.get(title)
            if doc:
                current_aliases = set(doc.get('aliases', []))
                if not aliases.issubset(current_aliases):
                    doc['aliases'] = list(aliases|current_aliases)
                    db[title] = doc
                revid = doc.get('parse', {}).get('revid')
                if page.revision == revid:
                    print('%s is up to date (rev. %s), skipping' %
                          (title, revid))
                    continue
                else:
                    print('New rev. %s is available for %s (have rev. %s)' %
                          (page.revision, title, revid))

            parse = site.api('parse', page=title)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print('ERROR: %s' % e)
            continue
        if doc:
            doc.update(parse)
        else:
            doc = parse
            if aliases:
                doc['aliases'] = list(aliases)
        db[title] = doc


if __name__ == '__main__':
    main()
