# Copyright (C) 2013-2014 Igor Tkach
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__  import print_function
import argparse
import couchdb
import mwclient
import os
import socket
import traceback
import urlparse
import time
import random

from datetime import datetime

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


def update_siteinfo(site, couch_server, db_name):
    try:
        siteinfo_db = couch_server.create('siteinfo')
    except couchdb.PreconditionFailed:
        siteinfo_db = couch_server['siteinfo']

    siteinfo = site.api('query', meta='siteinfo',
                        siprop='general|interwikimap|rightsinfo|statistics|namespaces'
    )['query']

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
    argparser.add_argument('site', nargs='?',
                           help=('MediaWiki site to scrape (host name), '
                                 'e.g. en.m.wikipedia.org'))
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
                                 'these names (titles). '
                                 'It name starts with @ it is '
                                 'interpreted as name of file containing titles, '
                                 'one per line, utf8 encoded.'))
    argparser.add_argument('--start',
                           help=('Download all article pages '
                                 'beginning with this name'))
    argparser.add_argument('--changes-since',
                           help=('Download all article pages '
                                 'that change since specified time. '
                                 'Timestamp format is yyyymmddhhmmss. '
                                 'See https://www.mediawiki.org/wiki/Timestamp'))
    argparser.add_argument('--timeout',
                           default=30.0,
                           type=float,
                           help=('Network communications timeout. '
                                 'Default: %(default)ss'))
    argparser.add_argument('-S', '--siteinfo-only', action='store_true',
                           help=('Fetch or update siteinfo, then exit'))
    argparser.add_argument('-r', '--resume', nargs='?',
                           default='',
                           metavar='SESSION ID',
                           help=('Resume previous scrape session. '
                                 'This relies on stats saved in '
                                 'mwscrape database.'))
    argparser.add_argument('--sessions-db-name',
                           default='mwscrape',
                           help=('Name of database where '
                                 'session info is stored. '
                                 'Default: %(default)s'))
    argparser.add_argument('--desc',
                           action='store_true',
                           help=('Request all pages in descending order'))

    return argparser.parse_args()


SHOW_FUNC = r"""
function(doc, req)
{
  var r = /href="\/wiki\/(.*?)"/gi;
  var replace = function(match, p1, offset, string) {
    return 'href="' + p1.replace(/_/g, ' ') + '"';
  };
  return doc.parse.text['*'].replace(r, replace);
}
"""

def set_show_func(db, show_func=SHOW_FUNC, force=False):
    design_doc = db.get('_design/w', {})
    shows = design_doc.get('shows', {})
    if force or not shows.get('html'):
        shows['html'] = show_func
        design_doc['shows'] = shows
        db['_design/w'] = design_doc


def main():

    args = parse_args()

    socket.setdefaulttimeout(args.timeout)

    couch_server = couchdb.Server(args.couch)

    sessions_db_name = args.sessions_db_name
    try:
        sessions_db = couch_server.create(sessions_db_name)
    except couchdb.PreconditionFailed:
        sessions_db = couch_server[sessions_db_name]

    if args.resume or args.resume is None:
        session_id = args.resume
        if session_id is None:
            current_doc = sessions_db['$current']
            session_id = current_doc['session_id']
        print('Resuming session %s' % session_id)
        session_doc = sessions_db[session_id]
        site_host = session_doc['site']
        db_name = session_doc['db_name']
        session_doc['resumed_at'] = datetime.utcnow().isoformat()
        if args.start:
            start_page_name = args.start
        else:
            start_page_name = session_doc.get('last_page_name', args.start)
        if args.desc:
            descending = True
        else:
            descending = session_doc.get('descending', False)
        sessions_db[session_id] = session_doc
    else:
        site_host = args.site
        db_name = args.db
        start_page_name = args.start
        descending = args.desc
        if not site_host:
            print('Site to scrape is not specified')
            raise SystemExit(1)
        if not db_name:
            db_name = site_host.replace('.', '-')
        session_id = '-'.join((db_name,
                               str(int(time.time())),
                               str(int(1000*random.random()))))
        print('Starting session %s' % session_id)
        sessions_db[session_id] = {
            'created_at': datetime.utcnow().isoformat(),
            'site': site_host,
            'db_name': db_name,
            'descending': descending
        }
        current_doc = sessions_db.get('$current', {})
        current_doc['session_id'] = session_id
        sessions_db['$current'] = current_doc


    site = mwclient.Site(site_host)

    update_siteinfo(site, couch_server, db_name)

    if args.siteinfo_only:
        return

    try:
        db = couch_server.create(db_name)
    except couchdb.PreconditionFailed:
        db = couch_server[db_name]

    set_show_func(db)

    def titles_from_args(titles):
        for title in titles:
            if title.startswith('@'):
                with open(os.path.expanduser(title[1:])) as f:
                    for line in f:
                        yield line.strip()
            else:
                yield title

    def titles_from_recent_changes(timestamp):
        changes = site.recentchanges(start=timestamp,
                                     namespace=0,
                                     show='!minor|!redirect')
        for change in changes:
            title = change.get('title')
            if title:
                doc = db.get(title)
                doc_revid = doc.get('parse', {}).get('revid') if doc else None
                revid = change.get('revid')
                if doc_revid == revid:
                    continue
                yield title

    if args.titles:
        pages = (site.Pages[title.decode('utf8')]
                 for title in titles_from_args(args.titles))
    elif args.changes_since:
        print('Getting recent changes (since %s)' % args.changes_since)
        pages = (site.Pages[title]
                 for title in titles_from_recent_changes(args.changes_since))
    else:
        print('Starting at %s' % start_page_name)
        pages = site.allpages(start=start_page_name,
                              dir='descending' if descending else 'ascending')

    def inc_count(count_name):
        session_doc = sessions_db[session_id]
        count = session_doc.get(count_name, 0)
        session_doc[count_name] = count + 1
        sessions_db[session_id] = session_doc

    for index, page in enumerate(pages):
        if index > 0 and index % 100 == 0:
            sessions_db.compact()
        title = page.name
        print('%7s %s' % (index, title))

        session_doc = sessions_db[session_id]
        session_doc['last_page_name'] = title
        session_doc['updated_at'] = datetime.utcnow().isoformat()
        sessions_db[session_id] = session_doc

        if not page.exists:
            print('Not found: %r' % title)
            inc_count('not_found')
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
                inc_count('failed_redirect')
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
                    inc_count('up_to_date')
                    continue
                else:
                    inc_count('updated')
                    print('New rev. %s is available for %s (have rev. %s)' %
                          (page.revision, title, revid))

            parse = site.api('parse', page=title)
        except KeyboardInterrupt:
            raise
        except Exception:
            print('Failed to process %s:' % title)
            traceback.print_exc()
            inc_count('error')
            continue
        if doc:
            doc.update(parse)
        else:
            inc_count('new')
            doc = parse
            if aliases:
                doc['aliases'] = list(aliases)
        db[title] = doc


if __name__ == '__main__':
    main()
