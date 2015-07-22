# Copyright (C) 2013-2014 Igor Tkach
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__  import print_function
import argparse
import couchdb
import fcntl
import hashlib
import mwclient
import mwclient.page
import os
import socket
import traceback
import urlparse
import tempfile
import time
import thread
import random

from collections import namedtuple
from datetime import datetime, timedelta
from multiprocessing import RLock
from multiprocessing.pool import ThreadPool
from contextlib import contextmanager


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
    argparser.add_argument('--site-path', default='/w/',
                           help=('MediaWiki site API path'
                                 'Default: %(default)s'))
    argparser.add_argument('--site-ext', default='.php',
                           help=('MediaWiki site API script extension'
                                 'Default: %(default)s'))
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
                                 'See https://www.mediawiki.org/wiki/Timestamp. '
                                 'Hours, minutes and seconds can be omited'
                             ))
    argparser.add_argument('--recent-days', type=int, default=1,
                           help=('Number of days to look back for recent changes'))
    argparser.add_argument('--recent', action='store_true',
                           help=('Download recently changed articles only'))
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

    argparser.add_argument('--delete-not-found',
                           action='store_true',
                           help=('Remove non-existing pages from the database'))

    argparser.add_argument('--speed',
                           type=int,
                           choices=range(0, 6),
                           default=0,
                           help=('Scrape speed'))

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


Redirect = namedtuple('Redirect', 'page fragment')

def redirects_to(site, from_title):
    """ Same as mwclient.page.Page.redirects_to except it returns page and fragment
    in a named tuple instead of just target page
    """
    info = site.api('query', prop='pageprops', titles=from_title, redirects='')['query']
    if 'redirects' in info:
        for page in info['redirects']:
            if page['from'] == from_title:
                return Redirect(
                    page=mwclient.page.Page(site, page['to']),
                    fragment=page.get('tofragment', u'')
                )
        return None
    else:
        return None


def scheme_and_host(site_host):
    p = urlparse.urlparse(site_host)
    scheme = p.scheme if p.scheme else 'https'
    host = p.netloc if p.scheme else site_host
    return scheme, host


def mkcouch(url):
    parsed = urlparse.urlparse(url)
    server_url = parsed.scheme + '://'+ parsed.netloc
    server = couchdb.Server(server_url)
    user = parsed.username
    password = parsed.password
    if password:
        print('Connecting %s as user %s' % (server.resource.url, user))
        server.resource.credentials = (user, password)
    return server


@contextmanager
def flock(path):
    with open(path, 'w') as lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        except IOError as ex:
            if ex.errno == 11:
                print (
                    'Scrape for this host is already in progress. '
                    'Use --speed option instead of starting multiple processes.')
            raise SystemExit(1)
        finally:
            lock_fd.close()


def main():

    args = parse_args()

    socket.setdefaulttimeout(args.timeout)

    couch_server = mkcouch(args.couch)

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
        scheme, host = scheme_and_host(site_host)
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
        scheme, host = scheme_and_host(site_host)
        if not db_name:
            db_name = host.replace('.', '-')
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


    site = mwclient.Site((scheme, host), path=args.site_path, ext=args.site_ext)

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
                                     toponly=1,
                                     show='!minor|!redirect|!anon')
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
    elif args.changes_since or args.recent:
        if args.recent:
            recent_days = args.recent_days
            changes_since = datetime.strftime(
                datetime.utcnow() + timedelta(days=-recent_days),
                '%Y%m%d%H%M%S')
        else:
            changes_since = args.changes_since.ljust(14, '0')
        print('Getting recent changes (since %s)' % changes_since)
        pages = (site.Pages[title]
                 for title in titles_from_recent_changes(changes_since))
    else:
        print('Starting at %s' % start_page_name)
        pages = site.allpages(start=start_page_name,
                              dir='descending' if descending else 'ascending')

    #threads are updating the same session document,
    #we don't want to have conflicts
    lock = RLock()

    def inc_count(count_name):
        with lock:
            session_doc = sessions_db[session_id]
            count = session_doc.get(count_name, 0)
            session_doc[count_name] = count + 1
            sessions_db[session_id] = session_doc

    def update_session(title):
        with lock:
            session_doc = sessions_db[session_id]
            session_doc['last_page_name'] = title
            session_doc['updated_at'] = datetime.utcnow().isoformat()
            sessions_db[session_id] = session_doc

    def process(page):
        title = page.name
        if not page.exists:
            print('Not found: %s' % title)
            inc_count('not_found')
            if args.delete_not_found:
                try:
                    del db[title]
                except couchdb.ResourceNotFound:
                    print('%s was not in the database' % title)
                except couchdb.ResourceConflict:
                    print('Conflict while deleting %s' % title)
                else:
                    print('%s removed from the database' % title)
            return
        try:
            aliases = set()
            redirect_count = 0
            while page.redirect:
                redirect_count += 1
                redirect_target = redirects_to(site, page.name)
                frag = redirect_target.fragment
                if frag:
                    alias = (title, frag)
                else:
                    alias = title
                aliases.add(alias)

                page = redirect_target.page
                print('%s ==> %s' % (
                    title,
                    page.name + (('#'+frag) if frag else '')))

                if redirect_count >= 10:
                    print('Too many redirect levels: %r' % aliases)
                    break

                title = page.name

            if page.redirect:
                print('Failed to resolve redirect %s', title)
                inc_count('failed_redirect')
                return

            doc = db.get(title)
            if doc:
                current_aliases = set()
                for alias in doc.get('aliases', ()):
                    if isinstance(alias, list):
                        alias = tuple(alias)
                    current_aliases.add(alias)
                if not aliases.issubset(current_aliases):
                    merged_aliases = aliases|current_aliases
                    #remove aliases without fragment if one with fragment is present
                    #this is mostly to cleanup aliases in old scrapes
                    to_remove = set()
                    for alias in merged_aliases:
                        if isinstance(alias, tuple):
                            to_remove.add(alias[0])
                    merged_aliases = merged_aliases - to_remove
                    doc['aliases'] = list(merged_aliases)
                    db[title] = doc
                revid = doc.get('parse', {}).get('revid')
                if page.revision == revid:
                    print('%s is up to date (rev. %s), skipping' %
                          (title, revid))
                    inc_count('up_to_date')
                    return
                else:
                    inc_count('updated')
                    print('New rev. %s is available for %s (have rev. %s)' %
                          (page.revision, title, revid))

            parse = site.api('parse', page=title)
        except KeyboardInterrupt as ki:
            print ('Caught KeyboardInterrupt', ki)
            thread.interrupt_main()
        except couchdb.ResourceConflict:
            print('Update conflict, skipping: %s' % title)
            return
        except Exception:
            print('Failed to process %s:' % title)
            traceback.print_exc()
            inc_count('error')
            return
        if doc:
            doc.update(parse)
        else:
            inc_count('new')
            doc = parse
            if aliases:
                doc['aliases'] = list(aliases)
        try:
            db[title] = doc
        except couchdb.ResourceConflict:
            print('Update conflict, skipping: %s' % title)
            return

    import pylru
    seen = pylru.lrucache(10000)

    def ipages(pages):
        for index, page in enumerate(pages):
            title = page.name
            print('%7s %s' % (index, title))
            if title in seen:
                print('Already saw %s, skipping' % (title,))
                continue
            seen[title] = True
            update_session(title)
            yield page


    with flock(os.path.join(tempfile.gettempdir(),
                            hashlib.sha1(host).hexdigest())):
        if args.speed:
            pool = ThreadPool(processes=args.speed*2)
            for _result in pool.imap(process, ipages(pages)):
                pass

        else:
            for page in ipages(pages):
                process(page)


if __name__ == '__main__':
    main()
